"""Publish /mission/done once, latched, and leave."""
import sys, time
import rclpy
from rclpy.qos import QoSProfile, DurabilityPolicy, ReliabilityPolicy
from std_msgs.msg import Int32
rclpy.init()
node = rclpy.create_node("mission_announcer")
qos = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL,
                 reliability=ReliabilityPolicy.RELIABLE)
pub = node.create_publisher(Int32, "/mission/done", qos)
msg = Int32(); msg.data = int(sys.argv[1]) if len(sys.argv) > 1 else 0
deadline = time.time() + 20
while time.time() < deadline:
    pub.publish(msg)
    rclpy.spin_once(node, timeout_sec=0.5)
    if pub.get_subscription_count() > 0 and time.time() > deadline - 17:
        break
print("mission done announced (pilot exit %d)" % msg.data)
