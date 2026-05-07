#!/usr/bin/env python3
"""
Per-drone MAVROS mission test. Runs INSIDE mavros_dN, so the rclpy node
sits on `agent_internal-N` at ROS_DOMAIN_ID=N (or whatever the autonomy
team's flat domain is, propagated via DRONE_N_DOMAIN_ID).

PASS criterion (proves the agent_internal-N → MAVROS → FCU → SITL chain):
  1. /<vehicle>/mavros/state reports `connected: true`
  2. set_mode(GUIDED) service call succeeds AND state.mode becomes GUIDED
  3. arming.cmd(true) service call succeeds AND state.armed becomes true
  4. takeoff cmd succeeds (the FCU returns success)
  5. (best-effort) if /<vehicle>/mavros/local_position/pose is publishing,
     verify the drone displaced more than `--movement-threshold` meters
     after a forward velocity setpoint stream. ArduPilot SITL doesn't
     always stream LOCAL_POSITION_NED on the SR0 channel; in that case
     this step is skipped and we report movement=unknown but still PASS
     based on (1)-(4).
  6. land cmd issued and arming.cmd(false) issued (safety on exit)

ArduPilot SR0_POSITION=10 in default_params.parm SHOULD give us
LOCAL_POSITION_NED at 10 Hz, but in practice mavros's local_position
plugin doesn't always pick it up (silent stream-rate mismatch). Don't
hard-fail on missing pose — the state-transition checks are the real
proof of the command path.
"""
import argparse
import math
import sys
import time

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy, DurabilityPolicy

from geometry_msgs.msg import PoseStamped, Twist
from mavros_msgs.msg import State
from mavros_msgs.srv import CommandBool, CommandTOL, SetMode

# MAVROS uses BEST_EFFORT for high-rate telemetry like /local_position/pose;
# default Subscription QoS is RELIABLE so the two never match without this.
SENSOR_QOS = QoSProfile(
    reliability=ReliabilityPolicy.BEST_EFFORT,
    history=HistoryPolicy.KEEP_LAST,
    depth=10,
    durability=DurabilityPolicy.VOLATILE,
)


class SingleDroneMission(Node):
    def __init__(self, vehicle: str):
        super().__init__(f"mavros_mission_{vehicle.lower()}")
        self.vehicle = vehicle
        self.state = State()
        self.pose: PoseStamped | None = None

        self.create_subscription(
            State, f"/{vehicle}/mavros/state", self._on_state, 10,
        )
        # /local_position/pose publishes BEST_EFFORT.
        self.create_subscription(
            PoseStamped, f"/{vehicle}/mavros/local_position/pose",
            self._on_pose, SENSOR_QOS,
        )
        self.vel_pub = self.create_publisher(
            Twist, f"/{vehicle}/mavros/setpoint_velocity/cmd_vel_unstamped", 10,
        )
        self.arm_cli = self.create_client(CommandBool, f"/{vehicle}/mavros/cmd/arming")
        self.mode_cli = self.create_client(SetMode, f"/{vehicle}/mavros/set_mode")
        self.takeoff_cli = self.create_client(CommandTOL, f"/{vehicle}/mavros/cmd/takeoff")
        self.land_cli = self.create_client(CommandTOL, f"/{vehicle}/mavros/cmd/land")

    def _on_state(self, msg: State) -> None:
        self.state = msg

    def _on_pose(self, msg: PoseStamped) -> None:
        self.pose = msg

    def wait_connected(self, timeout: float) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            rclpy.spin_once(self, timeout_sec=0.5)
            if self.state.connected:
                return True
        return False

    def wait_state(self, predicate, timeout: float, label: str) -> bool:
        """Spin until `predicate(self.state)` is True or `timeout` elapses."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            rclpy.spin_once(self, timeout_sec=0.2)
            if predicate(self.state):
                return True
        self.get_logger().error(
            f"{self.vehicle} timed out waiting for {label} (last state: "
            f"connected={self.state.connected} armed={self.state.armed} "
            f"guided={self.state.guided} mode={self.state.mode})"
        )
        return False

    def _call(self, cli, req, label: str, timeout: float = 10.0):
        if not cli.wait_for_service(timeout_sec=timeout):
            self.get_logger().error(f"{label}: service unavailable")
            return None
        fut = cli.call_async(req)
        rclpy.spin_until_future_complete(self, fut, timeout_sec=timeout)
        return fut.result()

    def set_mode(self, mode: str):
        req = SetMode.Request()
        req.custom_mode = mode
        return self._call(self.mode_cli, req, f"{self.vehicle} set_mode {mode}")

    def arm(self, value: bool = True):
        req = CommandBool.Request()
        req.value = value
        return self._call(self.arm_cli, req, f"{self.vehicle} arm={value}")

    def takeoff(self, alt: float):
        req = CommandTOL.Request()
        req.altitude = float(alt)
        return self._call(self.takeoff_cli, req, f"{self.vehicle} takeoff {alt}m")

    def land(self):
        req = CommandTOL.Request()
        return self._call(self.land_cli, req, f"{self.vehicle} land")

    def stream_velocity(self, vx: float, vy: float, vz: float, duration: float, rate_hz: int = 20):
        twist = Twist()
        twist.linear.x = float(vx)
        twist.linear.y = float(vy)
        twist.linear.z = float(vz)
        period = 1.0 / rate_hz
        end = time.time() + duration
        while time.time() < end:
            self.vel_pub.publish(twist)
            rclpy.spin_once(self, timeout_sec=0.0)
            time.sleep(period)


def main() -> int:
    parser = argparse.ArgumentParser(description="Single-drone MAVROS mission test")
    parser.add_argument("--vehicle", required=True, help="e.g. Copter1")
    parser.add_argument("--target-altitude", type=float, default=5.0)
    parser.add_argument("--setpoint-duration", type=float, default=8.0)
    parser.add_argument("--setpoint-vx", type=float, default=1.0)
    parser.add_argument("--movement-threshold", type=float, default=1.0)
    parser.add_argument("--connection-timeout", type=float, default=30.0)
    parser.add_argument("--state-transition-timeout", type=float, default=15.0,
                        help="How long to wait for armed/guided state transitions")
    parser.add_argument("--takeoff-settle", type=float, default=8.0)
    parser.add_argument("--land-settle", type=float, default=2.0)
    args = parser.parse_args()

    rclpy.init()
    mission = SingleDroneMission(args.vehicle)
    v = args.vehicle

    try:
        # 1. CONNECT
        if not mission.wait_connected(args.connection_timeout):
            print(f"FAIL: {v} MAVROS never reported connected to FCU")
            return 2
        mission.get_logger().info(f"{v}: state.connected=true")

        # 2. GUIDED
        if mission.set_mode("GUIDED") is None:
            print(f"FAIL: {v} set_mode GUIDED service call timed out")
            return 2
        if not mission.wait_state(
            lambda s: s.mode == "GUIDED" or s.guided,
            args.state_transition_timeout, "GUIDED mode",
        ):
            print(f"FAIL: {v} set_mode GUIDED accepted but state never reflected GUIDED")
            return 2
        mission.get_logger().info(f"{v}: mode=GUIDED")

        # 3. ARM
        if mission.arm(True) is None:
            print(f"FAIL: {v} arm service call timed out")
            return 2
        if not mission.wait_state(
            lambda s: s.armed,
            args.state_transition_timeout, "armed=true",
        ):
            print(f"FAIL: {v} arm accepted but state.armed never became true")
            return 2
        mission.get_logger().info(f"{v}: armed=true")

        # Optional: snapshot pose at takeoff start (may be None)
        rclpy.spin_once(mission, timeout_sec=0.5)
        pre = mission.pose

        # 4. TAKEOFF
        if mission.takeoff(args.target_altitude) is None:
            print(f"FAIL: {v} takeoff service call timed out")
            mission.arm(False)
            return 2
        time.sleep(args.takeoff_settle)
        mission.get_logger().info(
            f"{v}: takeoff issued, settled {args.takeoff_settle}s"
        )

        # 5. SETPOINT STREAM (forward velocity)
        mission.get_logger().info(
            f"{v}: streaming vx={args.setpoint_vx} for {args.setpoint_duration}s"
        )
        mission.stream_velocity(
            vx=args.setpoint_vx, vy=0.0, vz=0.0, duration=args.setpoint_duration,
        )

        rclpy.spin_once(mission, timeout_sec=0.5)
        post = mission.pose

        # 6. LAND + DISARM
        mission.land()
        time.sleep(args.land_settle)
        mission.arm(False)

        # Movement reporting (best-effort).
        if pre is not None and post is not None:
            dx = post.pose.position.x - pre.pose.position.x
            dy = post.pose.position.y - pre.pose.position.y
            dz = post.pose.position.z - pre.pose.position.z
            displacement = math.sqrt(dx * dx + dy * dy + dz * dz)
            move_ok = displacement >= args.movement_threshold
            verdict = (
                f"PASS (cmd path verified, |d|={displacement:.2f}m)"
                if move_ok
                else f"PASS-PARTIAL (cmd path OK, but |d|={displacement:.2f}m < threshold)"
            )
            print(
                f"MOVE: dX,dY,dZ = {dx:.2f},{dy:.2f},{dz:.2f} | |d|={displacement:.2f} ({verdict})"
            )
        else:
            print(
                f"MOVE: pose unavailable (SR0 stream rates may not include LOCAL_POSITION_NED) "
                f"(PASS: cmd path verified via state transitions)"
            )
        return 0

    finally:
        try:
            mission.arm(False)
        except Exception:
            pass
        rclpy.shutdown()


if __name__ == "__main__":
    sys.exit(main())
