#!/usr/bin/env python3
"""super-planner <-> TEVV adaptor. Yours to edit: TEVV runs it, but never changes it.

TEVV gives every component the same fixed interface (tevv.interface.v1),
namespaced by the drone it is attached to (/tevv/<drone>, e.g. /tevv/Drone2).
On the component's own network and ROS domain it publishes
/tevv/<drone>/gt/..., /tevv/<drone>/sensors/..., /tevv/<drone>/mission/waypoints
and .../mission/next, and /tevv/<drone>/control_state, and reads back
/tevv/<drone>/estimate, /tevv/<drone>/command and /tevv/<drone>/mission_status.
$TEVV_INTERFACE (interface.json) names the drone and its namespace, and lists
every one of those this package gets, with its message type, frame and QoS,
and the calibration of the sensors it consumes. TEVV's names are written
tevv("/gt/odom") below, so the same adaptor works on any drone.

This adaptor converts between those names and your component's own:

  INPUTS   TEVV topic -> your topic (same message type)
  OUTPUTS  your topic (its type) -> TEVV topic, through a convert function

For SUPER: its topic names are the ones files/config/click.yaml sets, and its
PositionTarget (position, velocity, acceleration) is sent to TEVV as a
velocity setpoint with yaw. Written from `component init --kind planner`.
"""
import json
import os
from pathlib import Path

import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from rosidl_runtime_py.utilities import get_message

INTERFACE = json.loads(Path(os.environ.get("TEVV_INTERFACE", "/cfg/component/interface.json")).read_text())
# This drone's namespace, e.g. /tevv/Drone2.
NAMESPACE = str(INTERFACE.get("namespace") or "/tevv").rstrip("/")


def tevv(name):
    """A TEVV topic for this drone: tevv("/gt/odom") -> /tevv/Drone2/gt/odom."""
    return NAMESPACE + name

# --- your component's topics -------------------------------------------------

INPUTS = {
    # TEVV topic                 SUPER's topic (files/config/click.yaml)
    tevv("/gt/odom"):             "/px4vision_custom_0/mavros/local_position/odom",
    tevv("/sensors/point_cloud"): "/px4vision_custom_0/lidar",
    tevv("/mission/next"):        "/goal",
}


def velocity_command(msg):
    """SUPER's PositionTarget -> TEVV's command: its velocity (odom ENU) and yaw."""
    from mavros_msgs.msg import PositionTarget
    out = PositionTarget()
    out.header.frame_id = "odom"
    out.coordinate_frame = PositionTarget.FRAME_LOCAL_NED  # MAVROS's name for its ENU frame
    out.type_mask = (PositionTarget.IGNORE_PX | PositionTarget.IGNORE_PY | PositionTarget.IGNORE_PZ
                     | PositionTarget.IGNORE_AFX | PositionTarget.IGNORE_AFY | PositionTarget.IGNORE_AFZ
                     | PositionTarget.IGNORE_YAW_RATE)
    out.velocity = msg.velocity
    out.yaw = msg.yaw
    return out


OUTPUTS = {
    # SUPER's topic                                  (its type)                        TEVV topic       convert
    "/px4vision_custom_0/mavros/setpoint_raw/local": ("mavros_msgs/msg/PositionTarget", tevv("/command"), velocity_command),
}

# --- the relay (generic; usually left as it is) ------------------------------


def tevv_topics():
    """Every topic interface.json names: {topic: (type, qos)}."""
    found = {}

    def walk(node):
        if isinstance(node, dict):
            for key, kind in (("topic", node.get("type")), ("image", node.get("type")),
                              ("info", "sensor_msgs/msg/CameraInfo")):
                if isinstance(node.get(key), str) and node[key].startswith("/"):
                    found[node[key]] = (kind, node.get("qos", "reliable"))
            for value in node.values():
                walk(value)

    walk(INTERFACE)
    return found


def profile(name, depth=10):
    return QoSProfile(depth=depth,
                      reliability=ReliabilityPolicy.BEST_EFFORT if name == "best_effort"
                      else ReliabilityPolicy.RELIABLE,
                      durability=DurabilityPolicy.TRANSIENT_LOCAL if name == "reliable_transient_local"
                      else DurabilityPolicy.VOLATILE)


class Adaptor(Node):
    def __init__(self):
        super().__init__("tevv_adaptor")
        known = tevv_topics()
        for tevv, mine in INPUTS.items():
            if tevv not in known:
                self.get_logger().warning(f"{tevv} is not in interface.json (consumes it in component.yaml?)")
                continue
            kind, qos = known[tevv]
            msg_type = get_message(kind)
            pub = self.create_publisher(msg_type, mine, profile(qos))
            self.create_subscription(msg_type, tevv, pub.publish, profile(qos))
            self.get_logger().info(f"{tevv} -> {mine}")
        for mine, (kind, tevv, convert) in OUTPUTS.items():
            pub = self.create_publisher(get_message(known[tevv][0]), tevv, 10)
            self.create_subscription(get_message(kind), mine,
                                     lambda msg, pub=pub, convert=convert: pub.publish(convert(msg)), 10)
            self.get_logger().info(f"{mine} -> {tevv}")


def main():
    rclpy.init()
    rclpy.spin(Adaptor())


if __name__ == "__main__":
    main()
