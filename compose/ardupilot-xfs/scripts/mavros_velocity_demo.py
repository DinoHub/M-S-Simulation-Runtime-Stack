#!/usr/bin/env python3
"""
Same flow as mavros_velocity_demo.sh but as a single rclpy node — easier to
extend with custom missions, easier to read, single process for all 4 drones.

Run from inside ardupilot-xfs-ros2 (after bringup + 4x MAVROS):
    python3 /scripts/mavros_velocity_demo.py
"""
import time

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
from mavros_msgs.msg import State
from mavros_msgs.srv import CommandBool, CommandTOL, SetMode

VEHICLES = ["Copter1", "Copter2", "Copter3", "Copter4"]


class FleetVelocityDemo(Node):
    def __init__(self):
        super().__init__("fleet_velocity_demo")

        self.state = {v: State() for v in VEHICLES}
        self.vel_pubs = {}
        self.arm_clis = {}
        self.mode_clis = {}
        self.takeoff_clis = {}
        self.land_clis = {}

        for v in VEHICLES:
            self.create_subscription(
                State, f"/{v}/mavros/state",
                lambda msg, vname=v: self._on_state(vname, msg), 10,
            )
            self.vel_pubs[v] = self.create_publisher(
                Twist, f"/{v}/mavros/setpoint_velocity/cmd_vel_unstamped", 10
            )
            self.arm_clis[v] = self.create_client(CommandBool, f"/{v}/mavros/cmd/arming")
            self.mode_clis[v] = self.create_client(SetMode, f"/{v}/mavros/set_mode")
            self.takeoff_clis[v] = self.create_client(CommandTOL, f"/{v}/mavros/cmd/takeoff")
            self.land_clis[v] = self.create_client(CommandTOL, f"/{v}/mavros/cmd/land")

    def _on_state(self, v, msg):
        self.state[v] = msg

    def wait_connected(self, timeout=30.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            rclpy.spin_once(self, timeout_sec=0.5)
            if all(self.state[v].connected for v in VEHICLES):
                self.get_logger().info("all 4 MAVROS instances connected to FCU")
                return True
        missing = [v for v in VEHICLES if not self.state[v].connected]
        self.get_logger().error(f"timeout: not connected: {missing}")
        return False

    def _call(self, cli, req, label, timeout=5.0):
        if not cli.wait_for_service(timeout_sec=timeout):
            self.get_logger().error(f"{label}: service unavailable")
            return None
        fut = cli.call_async(req)
        rclpy.spin_until_future_complete(self, fut, timeout_sec=timeout)
        return fut.result()

    def set_mode(self, v, mode):
        req = SetMode.Request()
        req.custom_mode = mode
        return self._call(self.mode_clis[v], req, f"{v} set_mode {mode}")

    def arm(self, v, value=True):
        req = CommandBool.Request()
        req.value = value
        return self._call(self.arm_clis[v], req, f"{v} arm={value}")

    def takeoff(self, v, alt=5.0):
        req = CommandTOL.Request()
        req.altitude = float(alt)
        return self._call(self.takeoff_clis[v], req, f"{v} takeoff {alt}m")

    def land(self, v):
        req = CommandTOL.Request()
        return self._call(self.land_clis[v], req, f"{v} land")

    def stream_velocity(self, v, vx, vy, vz, duration_s, rate_hz=20):
        twist = Twist()
        twist.linear.x = float(vx)
        twist.linear.y = float(vy)
        twist.linear.z = float(vz)
        end = time.time() + duration_s
        period = 1.0 / rate_hz
        while time.time() < end:
            self.vel_pubs[v].publish(twist)
            rclpy.spin_once(self, timeout_sec=0.0)
            time.sleep(period)


def main():
    rclpy.init()
    demo = FleetVelocityDemo()
    if not demo.wait_connected():
        return

    # Sequential per-drone for easy debugging. For parallel, dispatch each in its own thread.
    for v in VEHICLES:
        demo.get_logger().info(f"=== {v} ===")
        demo.set_mode(v, "GUIDED")
        time.sleep(1)
        demo.arm(v, True)
        time.sleep(2)
        demo.takeoff(v, 5.0)
        time.sleep(8)
        demo.get_logger().info(f"{v}: streaming forward 1 m/s for 8s")
        demo.stream_velocity(v, vx=1.0, vy=0.0, vz=0.0, duration_s=8.0)
        demo.land(v)
        time.sleep(2)

    demo.get_logger().info("done; final state:")
    for v in VEHICLES:
        s = demo.state[v]
        demo.get_logger().info(f"  {v}: armed={s.armed} mode={s.mode}")

    rclpy.shutdown()


if __name__ == "__main__":
    main()
