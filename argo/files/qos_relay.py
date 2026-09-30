#!/usr/bin/env python3
"""qos_relay.py TOPIC [TOPIC ...]: republish best-effort topics as reliable on TOPIC_reliable.

The bridge publishes fisheye images best-effort (throughput); OpenVINS subscribes to its
cameras with the default, reliable QoS, and DDS delivers nothing between the two. This
relays the serialized bytes unchanged, so nothing is decoded or copied into a message.
Remove it once the estimator subscribes with SensorDataQoS.
"""
import sys

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import Image


def main():
    rclpy.init()
    node = Node("gap_qos_relay")
    sub_qos = QoSProfile(depth=10, reliability=ReliabilityPolicy.BEST_EFFORT, history=HistoryPolicy.KEEP_LAST)
    pub_qos = QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE, history=HistoryPolicy.KEEP_LAST)
    keep = []
    for topic in sys.argv[1:]:
        pub = node.create_publisher(Image, topic + "_reliable", pub_qos)
        keep.append(node.create_subscription(Image, topic, pub.publish, sub_qos, raw=True))
        node.get_logger().info(f"relaying {topic} -> {topic}_reliable")
    # Argo stops sidecars with SIGTERM once the recorder exits, and fails the pod on any
    # sidecar exit code other than 143/137; a clean stop must not read as a failure.
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass


if __name__ == "__main__":
    main()
