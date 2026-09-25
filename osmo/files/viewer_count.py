"""Publish how many clients are connected to foxglove_bridge, once a second.

Read from the kernel's socket table rather than the bridge's log: an
ESTABLISHED TCP connection on the bridge's port is a viewer, whether it came
through `kubectl port-forward` (from 127.0.0.1) or a NodePort (from a node
address). The recorder reads /viz/viewers to decide how long to keep the run
up after its recording closes.
"""
import os
import sys
import time

import rclpy
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import Int32

PORT = int(os.environ.get("FOXGLOVE_PORT", "8765"))
ESTABLISHED = "01"


def viewers(port: int) -> int:
    n = 0
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            rows = open(table).read().splitlines()[1:]
        except OSError:
            continue
        for row in rows:
            fields = row.split()
            local, state = fields[1], fields[3]
            if state == ESTABLISHED and int(local.rsplit(":", 1)[1], 16) == port:
                n += 1
    return n


def main() -> int:
    rclpy.init()
    node = rclpy.create_node("viz_viewer_count")
    qos = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL,
                     reliability=ReliabilityPolicy.RELIABLE)
    pub = node.create_publisher(Int32, "/viz/viewers", qos)
    last = None
    while rclpy.ok():
        n = viewers(PORT)
        pub.publish(Int32(data=n))
        if n != last:
            print("viewers: %d" % n, flush=True)
            last = n
        rclpy.spin_once(node, timeout_sec=0.0)
        time.sleep(1.0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
