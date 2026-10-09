"""Grab one frame from an image topic: save raw bytes + print mean luminance.

    python3 grabframe.py /fisheye_front/image_raw /tmp/frame_X
"""
import sys

import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image

topic, out = sys.argv[1], sys.argv[2]
rclpy.init()
node = Node("grabframe")
got = {}
node.create_subscription(Image, topic, lambda m: got.setdefault("m", m), qos_profile_sensor_data)
while "m" not in got:
    rclpy.spin_once(node, timeout_sec=0.5)
m = got["m"]
ch = m.step // m.width
a = np.frombuffer(bytes(m.data), np.uint8).reshape(m.height, m.width, ch)
rgb = a[..., :3].astype(np.float32)
if m.encoding.startswith("bgr"):
    rgb = rgb[..., ::-1]
lum = 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]
np.save(out + ".npy", a)
dark = float((lum < 20).mean())
print(f"{m.width}x{m.height} {m.encoding} mean_lum={lum.mean():.1f} p10={np.percentile(lum,10):.1f} p90={np.percentile(lum,90):.1f} dark_frac={dark:.3f}")
