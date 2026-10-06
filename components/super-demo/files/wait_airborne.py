#!/usr/bin/env python3
"""Block until the vehicle hovers, then exit 0, so SUPER starts in the air.

SUPER's state machine streams position targets from the moment it starts
(mav_pos_cmd_pub_en), and in ArduPilot GUIDED those override the pilot's
take-off, so the vehicle never climbs. The pilot takes off first; this gate
holds SUPER's launch until sim truth is MIN_CLIMB_M above where it started
and nearly still.
"""
import math
import os
import sys
import time

import rclpy
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data

TOPIC = os.environ.get("MNS_GATE_ODOM_TOPIC", "/ground_truth/odom")
MIN_CLIMB_M = float(os.environ.get("MNS_GATE_MIN_CLIMB_M", "1.5"))
MAX_SPEED = float(os.environ.get("MNS_GATE_MAX_SPEED", "0.3"))
STILL_S = float(os.environ.get("MNS_GATE_STILL_S", "2.0"))
TIMEOUT_S = float(os.environ.get("MNS_GATE_TIMEOUT_S", "3600"))


def main() -> int:
    rclpy.init()
    node = Node("super_launch_gate")
    state = {"z0": None, "msg": None}

    def on_odom(msg: Odometry) -> None:
        if state["z0"] is None:
            state["z0"] = msg.pose.pose.position.z
        state["msg"] = msg

    node.create_subscription(Odometry, TOPIC, on_odom, qos_profile_sensor_data)
    print(f"[gate] waiting for {TOPIC}: {MIN_CLIMB_M} m above the start, slower than {MAX_SPEED} m/s "
          f"for {STILL_S} s", flush=True)
    start, still_since = time.time(), None
    while time.time() - start < TIMEOUT_S:
        rclpy.spin_once(node, timeout_sec=0.1)
        msg = state["msg"]
        if msg is None:
            continue
        v = msg.twist.twist.linear
        speed = math.sqrt(v.x * v.x + v.y * v.y + v.z * v.z)
        up = msg.pose.pose.position.z - state["z0"]
        if up >= MIN_CLIMB_M and speed <= MAX_SPEED:
            still_since = still_since or time.time()
            if time.time() - still_since >= STILL_S:
                print(f"[gate] airborne: {up:.2f} m up, {speed:.2f} m/s; starting the planner", flush=True)
                return 0
        else:
            still_since = None
    print("[gate] the vehicle never took off; not starting the planner", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
