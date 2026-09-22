#!/usr/bin/env python3
"""Fly a mission through MAVROS, synchronously.

Two mission kinds. `--trajectory-b64` flies a declared route -- time-indexed
local-ENU waypoints with yaw, the first-class form a CampaignSpec compiles into
this command. Without it, the corridor arguments fly a straight traverse, which
is what the gauntlet campaigns use and why this file was once named for them.

Why this exists: on a generated stack the SITL container streams MAVLink to
its own loopback (`--uartA udpclient:127.0.0.1:14550`) and publishes no port,
so nothing on the host can reach it and `run_experiment.py --mission plan:`
has no `--mav` endpoint to talk to. MAVROS runs inside the bridge container
and is connected, so that is the control path a generated stack actually
offers. Run this INSIDE the bridge container:

    docker exec -i <bridge> python3 - --vehicle Copter1 < fly_mission_mavros.py
    docker exec -i <bridge> python3 - --vehicle Drone1 --autopilot px4 < fly_mission_mavros.py

The two flight stacks differ in more than a mode name. ArduPilot flies this in
GUIDED and climbs with a takeoff command. PX4 flies it in OFFBOARD, which it
will only enter while position setpoints are already arriving faster than 2 Hz
and leaves the moment they stop, so the stream is primed before the mode change
and never interrupted; a takeoff command would switch it to AUTO.TAKEOFF and
end the session, so the climb holds a setpoint overhead instead.

It returns only when the vehicle has landed (or failed), which lets
run_experiment drive it as `--mission external --start-cmd ... --done timeout`
with a short timeout: the recording spans the whole flight.

The corridor traverse is deliberately slow and straight: VIO is scored on it,
and a fast or twitchy profile would confound texture-induced drift with motion
blur. A declared route sets its own pace, since each leg's duration is stated.

Axis: MAVROS local position is ENU and the bridge maps it from AirSim NED, so
ROS +y is the level's +X. A corridor authored along the level's +X is therefore
flown along ROS +y; flying ROS +x sends the vehicle sideways through the wall.
Measured on a live stack: ROS (197.4, 0.0) == level (6.0 m, 197.4 m).
"""
from __future__ import annotations

import argparse
import base64
import bisect
import json
import math
import sys
import time

import rclpy
from geometry_msgs.msg import PoseStamped
from mavros_msgs.msg import State
from mavros_msgs.srv import CommandBool, CommandTOL, SetMode
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rclpy.qos import QoSDurabilityPolicy, QoSHistoryPolicy, QoSProfile, QoSReliabilityPolicy

# MAVROS publishes state and local position with SENSOR_DATA-ish QoS; matching
# best-effort here avoids a silent no-delivery subscription.
SENSOR_QOS = QoSProfile(
    reliability=QoSReliabilityPolicy.BEST_EFFORT,
    durability=QoSDurabilityPolicy.VOLATILE,
    history=QoSHistoryPolicy.KEEP_LAST,
    depth=10,
)


class GauntletPilot(Node):
    def __init__(self, vehicle: str) -> None:
        super().__init__("gauntlet_pilot")
        ns = f"/{vehicle}/mavros" if vehicle else "/mavros"
        self.state: State | None = None
        self.pose: PoseStamped | None = None
        self.odom: Odometry | None = None

        self.create_subscription(State, f"{ns}/state", self._on_state, 10)
        self.create_subscription(PoseStamped, f"{ns}/local_position/pose", self._on_pose, SENSOR_QOS)
        self.create_subscription(Odometry, f"{ns}/local_position/odom", self._on_odom, SENSOR_QOS)
        self.setpoint = self.create_publisher(PoseStamped, f"{ns}/setpoint_position/local", 10)

        self.set_mode = self.create_client(SetMode, f"{ns}/set_mode")
        self.arming = self.create_client(CommandBool, f"{ns}/cmd/arming")
        self.takeoff = self.create_client(CommandTOL, f"{ns}/cmd/takeoff")
        self.land = self.create_client(CommandTOL, f"{ns}/cmd/land")

    # -- plumbing ----------------------------------------------------------
    def _on_state(self, msg: State) -> None:
        self.state = msg

    def _on_pose(self, msg: PoseStamped) -> None:
        self.pose = msg

    def _on_odom(self, msg: Odometry) -> None:
        self.odom = msg

    def spin_for(self, seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end:
            rclpy.spin_once(self, timeout_sec=0.05)

    def log(self, message: str) -> None:
        print(f"[pilot] {message}", flush=True)

    def call(self, client, request, label: str, timeout: float = 20.0):
        if not client.wait_for_service(timeout_sec=timeout):
            raise RuntimeError(f"{label}: service never appeared")
        future = client.call_async(request)
        rclpy.spin_until_future_complete(self, future, timeout_sec=timeout)
        if future.result() is None:
            raise RuntimeError(f"{label}: no response")
        return future.result()

    def position(self) -> tuple[float, float, float]:
        if self.pose is not None:
            p = self.pose.pose.position
            return p.x, p.y, p.z
        if self.odom is not None:
            p = self.odom.pose.pose.position
            return p.x, p.y, p.z
        return 0.0, 0.0, 0.0

    # -- flight ------------------------------------------------------------
    def wait_connected(self, timeout: float) -> None:
        end = time.time() + timeout
        while time.time() < end:
            rclpy.spin_once(self, timeout_sec=0.2)
            if self.state is not None and self.state.connected:
                self.log(f"MAVROS connected, mode={self.state.mode} armed={self.state.armed}")
                return
        raise RuntimeError("MAVROS never reported a connected flight controller")

    def ensure_mode(self, mode: str, timeout: float = 30.0,
                    stream: tuple[float, float, float] | None = None) -> None:
        """Enter a flight mode, optionally holding a setpoint stream while trying.

        PX4 refuses OFFBOARD unless setpoints are already arriving faster than
        2 Hz, and drops out of it the moment they stop. So the retry loop has to
        keep publishing rather than sleep between attempts, which is the whole
        reason this takes a setpoint. ArduPilot's GUIDED needs none of that.
        """
        end = time.time() + timeout
        while time.time() < end:
            if self.state is not None and self.state.mode == mode:
                self.log(f"mode {mode}")
                return
            self.call(self.set_mode, SetMode.Request(base_mode=0, custom_mode=mode), f"set_mode {mode}")
            if stream is not None:
                self.stream_setpoint(*stream, seconds=1.0)
            else:
                self.spin_for(1.0)
        raise RuntimeError(f"could not enter {mode} (last mode {self.state.mode if self.state else '?'})")

    def publish_setpoint(self, x: float, y: float, z: float, yaw: float = 0.0) -> None:
        msg = PoseStamped()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = "map"
        msg.pose.position.x = x
        msg.pose.position.y = y
        msg.pose.position.z = z
        # Yaw about ENU z. A corridor traverse leaves this at zero; a declared
        # route commands heading, which is half of what makes it a route rather
        # than a list of places.
        msg.pose.orientation.z = math.sin(yaw / 2.0)
        msg.pose.orientation.w = math.cos(yaw / 2.0)
        self.setpoint.publish(msg)

    def stream_setpoint(self, x: float, y: float, z: float, seconds: float) -> None:
        """Hold one setpoint for a while, at the rate OFFBOARD requires."""
        end = time.time() + seconds
        while time.time() < end:
            self.publish_setpoint(x, y, z)
            rclpy.spin_once(self, timeout_sec=0.05)
            time.sleep(0.05)

    def wait_ready(self, timeout: float = 180.0, settle: float = 5.0) -> None:
        """Wait for the EKF to have a position before arming.

        A stack that has just come up refuses to arm for the first half minute
        or so while the EKF converges and gets its origin; the refusal looks
        exactly like a misconfiguration, so wait for evidence of a position
        estimate first and only then start asking.
        """
        end = time.time() + timeout
        while time.time() < end:
            rclpy.spin_once(self, timeout_sec=0.2)
            if self.pose is not None or self.odom is not None:
                self.log(f"position estimate available at z={self.position()[2]:.2f} m; settling {settle:.0f}s")
                self.spin_for(settle)
                return
        raise RuntimeError("no local position estimate before arming (EKF never converged?)")

    def arm(self, timeout: float = 180.0) -> None:
        end = time.time() + timeout
        while time.time() < end:
            if self.state is not None and self.state.armed:
                self.log("armed")
                return
            self.call(self.arming, CommandBool.Request(value=True), "arm")
            self.spin_for(2.0)
        raise RuntimeError("arming refused (EKF or GPS not ready?)")

    def climb(self, altitude: float, timeout: float = 90.0) -> None:
        self.call(self.takeoff, CommandTOL.Request(altitude=altitude), "takeoff")
        end = time.time() + timeout
        while time.time() < end:
            rclpy.spin_once(self, timeout_sec=0.1)
            if self.position()[2] >= altitude * 0.9:
                self.log(f"reached {self.position()[2]:.2f} m")
                return
        raise RuntimeError(f"never reached {altitude} m (at {self.position()[2]:.2f} m)")

    def climb_offboard(self, altitude: float, timeout: float = 90.0) -> None:
        """Climb by holding a setpoint overhead, rather than a takeoff command.

        A takeoff command would put PX4 into AUTO.TAKEOFF, which ends the
        OFFBOARD session the traverse depends on. Holding the setpoint keeps
        one mode for the whole flight.
        """
        x, y, _ = self.position()
        end = time.time() + timeout
        last_log = 0.0
        while time.time() < end:
            self.publish_setpoint(x, y, altitude)
            rclpy.spin_once(self, timeout_sec=0.05)
            here = self.position()[2]
            if here >= altitude * 0.9:
                self.log(f"reached {here:.2f} m")
                return
            if time.time() - last_log >= 10.0:
                last_log = time.time()
                self.log(f"climbing, z={here:.2f} m")
            time.sleep(0.05)
        raise RuntimeError(f"never reached {altitude} m (at {self.position()[2]:.2f} m)")

    def traverse(self, distance: float, altitude: float, speed: float, timeout: float,
                 axis: int = 1) -> None:
        """Walk the setpoint down the corridor so the vehicle flies at `speed`
        rather than dashing to the far end at its own maximum.

        `axis` indexes the ROS ENU position: 1 (+y) is the level's +X, which is
        where a gauntlet corridor runs.
        """
        start = self.position()
        start_axis = start[axis]
        held = start[0] if axis == 1 else start[1]
        target = start_axis + distance
        name = "xyz"[axis]
        self.log(f"traverse {name} {start_axis:.1f} -> {target:.1f} m at {speed} m/s")
        began = time.time()
        last_log = 0.0
        while time.time() - began < timeout:
            travelled = min(distance, speed * (time.time() - began))
            if axis == 1:
                self.publish_setpoint(held, start_axis + travelled, altitude)
            else:
                self.publish_setpoint(start_axis + travelled, held, altitude)
            rclpy.spin_once(self, timeout_sec=0.05)
            here = self.position()[axis]
            if abs(here - target) < 2.0:
                self.log(f"arrived at {name}={here:.1f} m")
                return
            if time.time() - last_log >= 15.0:
                last_log = time.time()
                self.log(f"{name}={here:.1f} m z={self.position()[2]:.1f} m")
            time.sleep(0.05)
        self.log(f"traverse timed out at {name}={self.position()[axis]:.1f} m; landing anyway")

    def descend(self, timeout: float = 120.0, touchdown_m: float = 0.3) -> None:
        """Down is enough: ArduCopter can sit armed on the ground for a while
        after touchdown, and the recording only needs the flight to be over."""
        self.call(self.land, CommandTOL.Request(altitude=0.0), "land")
        end = time.time() + timeout
        while time.time() < end:
            rclpy.spin_once(self, timeout_sec=0.1)
            if self.state is not None and not self.state.armed:
                self.log("landed and disarmed")
                return
            if self.position()[2] <= touchdown_m:
                self.log(f"touched down at z={self.position()[2]:.2f} m")
                return
        self.log(f"landing timed out at z={self.position()[2]:.2f} m; continuing")

    def follow(self, plan: dict) -> None:
        """Fly a declared route, in time.

        The setpoint is sampled from the plan at the wall clock, so each leg
        takes the time it was given rather than however long the autopilot needs
        to chase a distant target. That is what makes two runs of the same route
        comparable, and what a corridor traverse only approximates by walking
        its target at a fixed speed.
        """
        rate = float(plan.get("rate_hz", 20))
        period = 1.0 / rate
        end_t = float(plan["waypoints"][-1]["t"])
        origin = self.position() if plan.get("relative_to_start", True) else (0.0, 0.0, 0.0)
        self.log(f"route {plan.get('name') or 'unnamed'}: {len(plan['waypoints'])} waypoints "
                 f"over {end_t:.0f} s from ({origin[0]:.1f}, {origin[1]:.1f}, {origin[2]:.1f})")

        began = time.time()
        last_log = 0.0
        while True:
            t = time.time() - began
            if t > end_t:
                break
            xyz, yaw = sample(plan, t)
            self.publish_setpoint(origin[0] + xyz[0], origin[1] + xyz[1],
                                  origin[2] + xyz[2], yaw)
            rclpy.spin_once(self, timeout_sec=period / 2)
            if t - last_log >= 10.0:
                last_log = t
                here = self.position()
                self.log(f"t={t:5.1f}s commanded=({xyz[0]:6.1f},{xyz[1]:6.1f},{xyz[2]:5.1f}) "
                         f"at=({here[0]:6.1f},{here[1]:6.1f},{here[2]:5.1f})")
            time.sleep(period)

        # Hold the final point before landing. A setpoint stream that simply
        # stops drops PX4 out of OFFBOARD mid-air.
        hold = float(plan.get("finish_hold_seconds", 3))
        xyz, yaw = sample(plan, end_t)
        self.log(f"holding the last waypoint for {hold:.0f}s")
        end = time.time() + hold
        while time.time() < end:
            self.publish_setpoint(origin[0] + xyz[0], origin[1] + xyz[1],
                                  origin[2] + xyz[2], yaw)
            rclpy.spin_once(self, timeout_sec=period / 2)
            time.sleep(period)

    def prestream(self, plan: dict) -> tuple[float, float, float]:
        """Publish the route's first point before anything else.

        PX4 will not enter OFFBOARD unless setpoints are already arriving, so
        the first point has to be in the air before the mode change is even
        attempted. Returns it, for the mode call to keep holding.
        """
        origin = self.position() if plan.get("relative_to_start", True) else (0.0, 0.0, 0.0)
        xyz, _ = sample(plan, 0.0)
        point = (origin[0] + xyz[0], origin[1] + xyz[1], origin[2] + xyz[2])
        self.stream_setpoint(*point, seconds=float(plan.get("prestream_seconds", 2)))
        return point


def sample(plan: dict, t: float) -> tuple[list[float], float]:
    """Position and yaw at time t. A compact copy of trajectory_plan.sample:
    this file is piped into a container on stdin and can import nothing local.
    The host-side copy is the one validated and under test."""
    points = plan["waypoints"]
    times = [float(p["t"]) for p in points]
    i = max(0, min(bisect.bisect_right(times, t) - 1, len(points) - 2))
    a, b = points[i], points[i + 1]
    span = float(b["t"]) - float(a["t"])
    f = 0.0 if span <= 0 else max(0.0, min(1.0, (t - float(a["t"])) / span))
    xyz = [float(p) + f * (float(q) - float(p))
           for p, q in zip(a["position"], b["position"])]
    delta = math.atan2(math.sin(float(b["yaw"]) - float(a["yaw"])),
                       math.cos(float(b["yaw"]) - float(a["yaw"])))
    return xyz, float(a["yaw"]) + f * delta


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vehicle", default="Copter1", help="MAVROS namespace vehicle name")
    ap.add_argument("--altitude", type=float, default=1.5, help="traverse altitude (m)")
    ap.add_argument("--distance", type=float, default=197.0, help="corridor distance to fly (m)")
    ap.add_argument("--speed", type=float, default=1.0, help="traverse speed (m/s)")
    ap.add_argument("--axis", choices=("x", "y"), default="y",
                    help="ROS ENU axis to traverse; y is the level's +X (default: y)")
    ap.add_argument("--traverse-timeout", type=float, default=600.0)
    ap.add_argument("--autopilot", choices=("ardupilot", "px4"), default="ardupilot",
                    help="which flight stack is flying: they differ in the mode the "
                         "traverse runs in and in how the climb is commanded "
                         "(default: ardupilot)")
    ap.add_argument("--trajectory-b64",
                    help="a declared route as base64 JSON. Passed this way because "
                         "stdin already carries this script, so there is no second "
                         "stream to read a file from, and because it keeps the "
                         "command self-contained rather than depending on a mount.")
    args = ap.parse_args(argv)

    plan = None
    if args.trajectory_b64:
        plan = json.loads(base64.b64decode(args.trajectory_b64))

    rclpy.init()
    pilot = GauntletPilot(args.vehicle)
    try:
        pilot.wait_connected(120.0)
        pilot.wait_ready()
        # A route commands its own climb, as its first leg; the corridor mission
        # climbs to a fixed altitude first and traverses at it.
        altitude = sample(plan, 0.0)[0][2] if plan else args.altitude
        if args.autopilot == "px4":
            # OFFBOARD only accepts a mode change while setpoints are already
            # arriving, and ends as soon as they stop, so the stream is primed
            # first and never interrupted: no takeoff command, one mode from
            # arming to the landing call.
            if plan:
                point = pilot.prestream(plan)
            else:
                x, y, _ = pilot.position()
                point = (x, y, altitude)
                pilot.stream_setpoint(*point, seconds=2.0)
            pilot.ensure_mode("OFFBOARD", stream=point)
            pilot.arm()
            if not plan:
                pilot.climb_offboard(altitude)
        else:
            pilot.ensure_mode("GUIDED")
            pilot.arm()
            if plan:
                pilot.prestream(plan)
            else:
                pilot.climb(altitude)
        if plan:
            pilot.follow(plan)
        else:
            pilot.traverse(args.distance, altitude, args.speed, args.traverse_timeout,
                           axis={"x": 0, "y": 1}[args.axis])
        pilot.descend()
    except Exception as exc:  # noqa: BLE001 - the caller only needs the reason and a code
        print(f"[pilot] FAILED: {exc}", file=sys.stderr, flush=True)
        return 1
    finally:
        pilot.destroy_node()
        rclpy.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
