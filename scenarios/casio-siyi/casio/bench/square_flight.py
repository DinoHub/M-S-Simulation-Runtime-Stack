#!/usr/bin/env python3
"""Fly a PX4 mission square around a point, camera held on it, and record casio.

Runs inside the casio-node image (rclpy, mavros_msgs, vision_msgs,
casio_interfaces) on the stack network; square-flight.sh starts it.

  square_flight.py --out DIR --center-n N --center-e E
                   [--side 60] [--alt 40] [--roi-alt 15] [--laps 2] [--speed 4]
                   [--first-corner 0..3]

Offsets are metres north / east of the drone's start, which is PX4's home.
The mission is: take off to --alt, point the nose (and so the fixed camera)
at the centre with DO_SET_ROI_LOCATION, fly --laps laps of the square at
--speed, return over home and land. Nothing here publishes setpoints: PX4
flies the mission itself, so this works without a local planner, and
without obstacle avoidance: pick an altitude and a first corner (corners run
SW, SE, NE, NW from 0) whose straight legs clear the level's structures.
If sim truth stops moving while PX4's estimate says the drone is flying,
it has hit something: that is logged as a "collision" event and the run
ends with exit code 2 (the drone stays stuck; restart the stack).

Every row is stamped with the message's header time (sim time) where it has
one. Files written to --out:
  mission.json      the uploaded items and arguments
  events.csv        mode / arming / landed-state changes and PX4 status text
  ekf.csv           /mavros/global_position/local (ENU, the pose casio uses)
  gt_odom.csv       /ground_truth/odom from the bridge (sim truth)
  frames.csv        per camera frame: detections in /detections
  detections.csv    one row per 2D detection
  positions.csv     /objects/position (localiser output, map frame)
  tracked.csv       /objects/tracked (tracker output, map frame)
  target_gps.csv    /target/gps (what cloud_relay turns into RC3 posts)
  surveillance.csv  /surveillance/goal_valid and /surveillance/goal
"""
import argparse
import csv
import json
import math
import os
import sys
import time
from collections import deque

import rclpy
from rclpy.node import Node
from rclpy.parameter import Parameter
from rclpy.qos import qos_profile_sensor_data, QoSProfile, ReliabilityPolicy, DurabilityPolicy

from geometry_msgs.msg import PoseStamped
from mavros_msgs.msg import ExtendedState, State, StatusText, Waypoint
from mavros_msgs.srv import CommandBool, SetMode, WaypointClear, WaypointPush
from nav_msgs.msg import Odometry
from sensor_msgs.msg import CameraInfo, NavSatFix
from std_msgs.msg import Bool
from vision_msgs.msg import Detection2DArray
from casio_interfaces.msg import ObjectRange, TargetGps

EARTH_R = 6378137.0
FRAME_MISSION = 2  # MAV_FRAME_MISSION: PX4 rejects DO_ commands without a position in any other
FRAME_REL_ALT = 3  # MAV_FRAME_GLOBAL_RELATIVE_ALT
CMD = dict(waypoint=16, land=21, takeoff=22, change_speed=178, roi_location=195, roi_none=197)
LANDED = {0: "undefined", 1: "on_ground", 2: "in_air", 3: "takeoff", 4: "landing"}


def stamp_s(header):
    return header.stamp.sec + header.stamp.nanosec * 1e-9


def yaw_of(q):
    return math.atan2(2 * (q.w * q.z + q.x * q.y), 1 - 2 * (q.y * q.y + q.z * q.z))


class Csv:
    def __init__(self, path, header):
        self.f = open(path, "w", newline="")
        self.w = csv.writer(self.f)
        self.w.writerow(header)

    def row(self, *values):
        self.w.writerow([f"{v:.6f}" if isinstance(v, float) else v for v in values])

    def close(self):
        self.f.close()


class SquareFlight(Node):
    def __init__(self, args):
        super().__init__("casio_square_flight",
                         parameter_overrides=[Parameter("use_sim_time", value=True)])
        self.args = args
        out = args.out
        self.state = None
        self.ext = None
        self.fix = None
        self.local = None
        self.last_ekf = self.last_gt = -1.0
        self.airborne = False
        self.truth = deque()  # (t, x, y, z) of /ground_truth/odom, last 20 s

        self.events = Csv(f"{out}/events.csv", ["t", "kind", "value"])
        self.ekf = Csv(f"{out}/ekf.csv", ["t", "x_e", "y_n", "z_u", "yaw", "vx", "vy", "vz"])
        self.gt = Csv(f"{out}/gt_odom.csv", ["t", "frame", "child", "x", "y", "z", "yaw"])
        self.frames = Csv(f"{out}/frames.csv", ["t", "n_detections"])
        self.dets = Csv(f"{out}/detections.csv", ["t", "class", "score", "cx", "cy", "w", "h"])
        self.pos = Csv(f"{out}/positions.csv", ["t", "frame", "id", "class", "x", "y", "z",
                                                "var_x", "var_y", "var_z", "bbox_h", "conf", "hold",
                                                "veh_x", "veh_y", "veh_z"])
        self.trk = Csv(f"{out}/tracked.csv", ["t", "frame", "id", "class", "x", "y", "z",
                                              "var_x", "var_y", "var_z", "bbox_h", "conf", "hold",
                                              "veh_x", "veh_y", "veh_z"])
        self.gps = Csv(f"{out}/target_gps.csv", ["t", "track_id", "class", "lat", "lon", "alt",
                                                 "distance_m", "conf", "held"])
        self.surv = Csv(f"{out}/surveillance.csv", ["t", "kind", "valid", "x", "y", "z"])
        self.cam = Csv(f"{out}/camera.csv", ["t", "fx", "fy", "cx", "cy"])
        self.all = [self.events, self.ekf, self.gt, self.frames, self.dets, self.pos,
                    self.trk, self.gps, self.surv, self.cam]

        sd = qos_profile_sensor_data
        latched = QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE,
                             durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.create_subscription(State, "/mavros/state", self.on_state, latched)
        self.create_subscription(ExtendedState, "/mavros/extended_state", self.on_ext, sd)
        self.create_subscription(StatusText, "/mavros/statustext/recv", self.on_text, sd)
        self.create_subscription(NavSatFix, "/mavros/global_position/global", self.on_fix, sd)
        self.create_subscription(Odometry, "/mavros/global_position/local", self.on_ekf, sd)
        self.create_subscription(Odometry, "/ground_truth/odom", self.on_gt, sd)
        self.create_subscription(CameraInfo, "/camera/camera_info", self.on_cam, sd)
        self.create_subscription(Detection2DArray, "/detections", self.on_dets, sd)
        self.create_subscription(ObjectRange, "/objects/position",
                                 lambda m: self.on_range(self.pos, m), sd)
        self.create_subscription(ObjectRange, "/objects/tracked",
                                 lambda m: self.on_range(self.trk, m), sd)
        self.create_subscription(TargetGps, "/target/gps", self.on_gps, sd)
        self.create_subscription(Bool, "/surveillance/goal_valid", self.on_valid, sd)
        self.create_subscription(PoseStamped, "/surveillance/goal", self.on_goal, sd)
        self.cam_seen = 0

    # --- recording ---------------------------------------------------------
    def now_s(self):
        return self.get_clock().now().nanoseconds * 1e-9

    def event(self, kind, value):
        self.events.row(self.now_s(), kind, value)
        self.get_logger().info(f"{kind}: {value}")

    def on_state(self, m):
        if self.state is None or (m.mode, m.armed, m.connected) != (
                self.state.mode, self.state.armed, self.state.connected):
            self.event("state", f"mode={m.mode} armed={m.armed} connected={m.connected}")
        self.state = m

    def on_ext(self, m):
        if self.ext is None or m.landed_state != self.ext.landed_state:
            self.event("landed_state", LANDED.get(m.landed_state, m.landed_state))
        if m.landed_state == 2:
            self.airborne = True
        self.ext = m

    def on_text(self, m):
        self.event("px4", m.text)

    def on_fix(self, m):
        self.fix = m

    def on_ekf(self, m):
        self.local = m
        t = stamp_s(m.header)
        if t - self.last_ekf < 0.1:
            return
        self.last_ekf = t
        p, v = m.pose.pose.position, m.twist.twist.linear
        self.ekf.row(t, p.x, p.y, p.z, yaw_of(m.pose.pose.orientation), v.x, v.y, v.z)

    def on_gt(self, m):
        t = stamp_s(m.header)
        if t - self.last_gt < 0.1:
            return
        self.last_gt = t
        p = m.pose.pose.position
        self.truth.append((t, p.x, p.y, p.z))
        while self.truth and t - self.truth[0][0] > 20.0:
            self.truth.popleft()
        self.gt.row(t, m.header.frame_id, m.child_frame_id, p.x, p.y, p.z,
                    yaw_of(m.pose.pose.orientation))

    def on_cam(self, m):
        self.cam_seen += 1
        if self.cam_seen % 20 == 1:
            self.cam.row(stamp_s(m.header), m.k[0], m.k[4], m.k[2], m.k[5])

    def on_dets(self, m):
        t = stamp_s(m.header)
        self.frames.row(t, len(m.detections))
        for d in m.detections:
            cls, score = ("", 0.0)
            if d.results:
                h = d.results[0].hypothesis
                cls, score = h.class_id, h.score
            c = d.bbox.center
            cx, cy = (c.position.x, c.position.y) if hasattr(c, "position") else (c.x, c.y)
            self.dets.row(t, cls, score, cx, cy, d.bbox.size_x, d.bbox.size_y)

    def on_range(self, sink, m):
        p, c = m.pose.pose.position, m.pose.covariance
        vp = m.vehicle_position
        sink.row(stamp_s(m.header), m.header.frame_id, m.detection_id, m.class_id,
                 p.x, p.y, p.z, c[0], c[7], c[14], m.bbox[3],  # bbox is cx, cy, w, h
                 m.confidence, m.hold_position, vp.x, vp.y, vp.z)

    def on_gps(self, m):
        self.gps.row(stamp_s(m.header), m.track_id, m.class_id, m.latitude, m.longitude,
                     m.altitude, m.distance_m, m.confidence, m.position_held)

    def on_valid(self, m):
        self.surv.row(self.now_s(), "valid", m.data, "", "", "")

    def on_goal(self, m):
        p = m.pose.position
        self.surv.row(stamp_s(m.header), "goal", "", p.x, p.y, p.z)

    def collided(self):
        """Truth still for 15 s while PX4's estimate is moving: stuck on geometry."""
        if not self.airborne or len(self.truth) < 2 or self.local is None:
            return False
        first, last = self.truth[0], self.truth[-1]
        if last[0] - first[0] < 15.0:
            return False
        moved = math.dist(first[1:], last[1:])
        v = self.local.twist.twist.linear
        return moved < 0.5 and math.hypot(v.x, v.y, v.z) > 0.5

    # --- flying ------------------------------------------------------------
    def spin_for(self, seconds, until=None):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            rclpy.spin_once(self, timeout_sec=0.1)
            if until is not None and until():
                return True
        return False

    def call(self, srv_type, name, request, timeout=15.0):
        client = self.create_client(srv_type, name)
        if not client.wait_for_service(timeout_sec=timeout):
            raise RuntimeError(f"service {name} not available")
        future = client.call_async(request)
        end = time.monotonic() + timeout
        while not future.done() and time.monotonic() < end:
            rclpy.spin_once(self, timeout_sec=0.1)
        if not future.done():
            raise RuntimeError(f"service {name} timed out")
        return future.result()

    def geo(self, north, east):
        lat0, lon0 = self.fix.latitude, self.fix.longitude
        lat = lat0 + math.degrees(north / EARTH_R)
        lon = lon0 + math.degrees(east / (EARTH_R * math.cos(math.radians(lat0))))
        return lat, lon

    def item(self, command, north=None, east=None, alt=0.0, params=(0.0, 0.0, 0.0, 0.0)):
        w = Waypoint()
        w.frame = FRAME_REL_ALT if north is not None else FRAME_MISSION
        w.command = command
        w.autocontinue = True
        w.param1, w.param2, w.param3, w.param4 = [float(p) for p in params]
        if north is not None:
            w.x_lat, w.y_long = self.geo(north, east)
        w.z_alt = float(alt)
        return w

    def mission(self):
        a = self.args
        n0, e0, half = a.center_n, a.center_e, a.side / 2.0
        corners = [(n0 - half, e0 - half), (n0 - half, e0 + half),
                   (n0 + half, e0 + half), (n0 + half, e0 - half)]
        corners = corners[a.first_corner:] + corners[:a.first_corner]
        nan = float("nan")
        items = [
            self.item(CMD["takeoff"], 0.0, 0.0, a.alt, (0, 0, 0, nan)),
            self.item(CMD["change_speed"], params=(1, a.speed, -1, 0)),
            self.item(CMD["roi_location"], n0, e0, a.roi_alt),
        ]
        for _ in range(a.laps):
            items += [self.item(CMD["waypoint"], n, e, a.alt, (0, 1.0, 0, nan)) for n, e in corners]
        items += [
            self.item(CMD["waypoint"], *corners[0], a.alt, (0, 1.0, 0, nan)),
            self.item(CMD["roi_none"]),
            self.item(CMD["waypoint"], 0.0, 0.0, a.alt, (0, 1.0, 0, nan)),
            self.item(CMD["land"], 0.0, 0.0, 0.0, (0, 0, 0, nan)),
        ]
        items[0].is_current = True
        return items, corners

    def run(self):
        a = self.args
        self.get_logger().info("waiting for MAVROS connection, GPS and local position")
        ok = self.spin_for(a.ready_timeout, lambda: self.state is not None and self.state.connected
                           and self.fix is not None and self.fix.status.status >= 0
                           and self.local is not None)
        if not ok:
            raise RuntimeError("no MAVROS connection / GPS fix / local position")

        items, corners = self.mission()
        with open(f"{a.out}/mission.json", "w") as f:
            json.dump({"args": vars(a),
                       "home": {"lat": self.fix.latitude, "lon": self.fix.longitude,
                                "alt": self.fix.altitude},
                       "corners_ne": corners,
                       "items": [{"command": w.command, "lat": w.x_lat, "lon": w.y_long,
                                  "alt": w.z_alt, "params": [w.param1, w.param2, w.param3, w.param4]}
                                 for w in items]}, f, indent=1)

        self.call(WaypointClear, "/mavros/mission/clear", WaypointClear.Request())
        push = WaypointPush.Request()
        push.start_index = 0
        push.waypoints = items
        r = self.call(WaypointPush, "/mavros/mission/push", push, timeout=30.0)
        self.event("mission_push", f"success={r.success} transferred={r.wp_transfered}/{len(items)}")
        if not r.success:
            raise RuntimeError("mission upload failed")

        mode = SetMode.Request()
        mode.custom_mode = "AUTO.MISSION"
        self.event("set_mode", self.call(SetMode, "/mavros/set_mode", mode).mode_sent)

        arm = CommandBool.Request()
        arm.value = True
        end = time.monotonic() + a.arm_timeout
        while True:
            r = self.call(CommandBool, "/mavros/cmd/arming", arm)
            self.event("arm", f"success={r.success} result={r.result}")
            if r.success:
                break
            if time.monotonic() > end:
                raise RuntimeError("PX4 refused to arm; see events.csv for its reasons")
            self.spin_for(3.0)

        n1, e1 = corners[0]
        path = 2 * math.hypot(n1, e1) + a.laps * 4 * a.side
        timeout = a.flight_timeout or 1.5 * (path / a.speed + 2 * a.alt / 1.5) + 120.0
        t0 = time.monotonic()
        done = self.spin_for(timeout, lambda: self.collided() or (
            self.airborne and self.state is not None and not self.state.armed))
        if self.collided():
            p = self.truth[-1]
            self.event("collision", f"sim truth still at x={p[1]:.1f} y={p[2]:.1f} z={p[3]:.1f} "
                                    "while PX4 reports motion")
            self.spin_for(a.tail)
            return 2
        if not done:
            self.event("timeout", f"{timeout:.0f}s; switching to AUTO.RTL")
            mode.custom_mode = "AUTO.RTL"
            self.call(SetMode, "/mavros/set_mode", mode)
            self.spin_for(240.0, lambda: self.state is not None and not self.state.armed)
        self.event("flight_done", f"{time.monotonic() - t0:.1f}s wall")
        self.spin_for(a.tail)  # the tracker and relay lag the last frames
        return 0

    def close(self):
        for c in self.all:
            c.close()


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", required=True)
    p.add_argument("--center-n", type=float, required=True)
    p.add_argument("--center-e", type=float, required=True)
    p.add_argument("--side", type=float, default=60.0)
    p.add_argument("--alt", type=float, default=40.0, help="metres above home")
    p.add_argument("--roi-alt", type=float, default=15.0, help="metres above home")
    p.add_argument("--laps", type=int, default=2)
    p.add_argument("--speed", type=float, default=4.0)
    p.add_argument("--ready-timeout", type=float, default=120.0)
    p.add_argument("--arm-timeout", type=float, default=90.0)
    p.add_argument("--first-corner", type=int, default=0, choices=range(4))
    p.add_argument("--flight-timeout", type=float, default=0.0, help="0: from the mission length")
    p.add_argument("--tail", type=float, default=5.0)
    args = p.parse_args()
    os.makedirs(args.out, exist_ok=True)

    rclpy.init()
    node = SquareFlight(args)
    try:
        rc = node.run()
    except Exception as exc:  # recorded, then reported by the wrapper
        node.event("error", str(exc))
        rc = 1
    finally:
        node.close()
        node.destroy_node()
        rclpy.shutdown()
    sys.exit(rc)


if __name__ == "__main__":
    main()
