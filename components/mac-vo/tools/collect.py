"""Write /mns/estimate/odom and /ground_truth/odom to TUM files until SIGINT/SIGTERM.
Run inside the adaptor image (rclpy, nav_msgs). Stamps are the messages' header stamps."""
import signal, sys
import rclpy
from rclpy.node import Node
from nav_msgs.msg import Odometry

out = sys.argv[1] if len(sys.argv) > 1 else "/out"
rclpy.init()
node = Node("tum_collector")
files = {"/mns/estimate/odom": open(f"{out}/est.tum", "w"), "/ground_truth/odom": open(f"{out}/gt.tum", "w")}

def writer(f):
    def cb(m):
        s = m.header.stamp.sec + m.header.stamp.nanosec * 1e-9
        p, q = m.pose.pose.position, m.pose.pose.orientation
        f.write(f"{s:.9f} {p.x} {p.y} {p.z} {q.x} {q.y} {q.z} {q.w}\n")
    return cb

for topic, f in files.items():
    node.create_subscription(Odometry, topic, writer(f), 50)
stop = []
signal.signal(signal.SIGTERM, lambda *_: stop.append(1))
signal.signal(signal.SIGINT, lambda *_: stop.append(1))
while not stop:
    rclpy.spin_once(node, timeout_sec=0.2)
for f in files.values():
    f.close()
