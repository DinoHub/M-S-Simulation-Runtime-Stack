"""Save OpenVINS track-history images (JPEG, named by header stamp ns) and the estimate."""
import os, sys
import cv2, rclpy
from cv_bridge import CvBridge
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from sensor_msgs.msg import Image
from nav_msgs.msg import Odometry
out = sys.argv[1]; os.makedirs(out, exist_ok=True)
rclpy.init(); n = Node("trackdump"); br = CvBridge()
qos = QoSProfile(depth=50, reliability=ReliabilityPolicy.RELIABLE, history=HistoryPolicy.KEEP_LAST)
f = open(os.path.join(out, "odomimu.csv"), "w"); f.write("t,x,y,z\n")
def img(m):
    a = br.imgmsg_to_cv2(m, "bgr8"); a = cv2.resize(a, (a.shape[1] // 2, a.shape[0] // 2), interpolation=cv2.INTER_AREA)
    cv2.imwrite(os.path.join(out, f"{m.header.stamp.sec * 1000000000 + m.header.stamp.nanosec}.jpg"), a, [cv2.IMWRITE_JPEG_QUALITY, 80])
def odo(m):
    p = m.pose.pose.position; f.write(f"{m.header.stamp.sec + m.header.stamp.nanosec * 1e-9:.6f},{p.x},{p.y},{p.z}\n"); f.flush()
n.create_subscription(Image, "/ov_msckf/trackhist", img, qos)
n.create_subscription(Odometry, "/ov_msckf/odomimu", odo, qos)
rclpy.spin(n)
