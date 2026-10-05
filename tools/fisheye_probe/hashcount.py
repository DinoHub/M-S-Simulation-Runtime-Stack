"""Count delivered vs genuinely new frames on one image topic.

A shared-cubemap camera captures its cube faces at the burst rate but publishes
at its publish rate; a publish between bursts re-projects the same faces through
the same baked map, so its pixels are byte-identical to the previous frame.
This hashes every frame and counts how many differ from the one before.

    python3 hashcount.py /fisheye_front/image_raw /tmp/hashcount.json

Runs until SIGINT, then writes the JSON summary.
"""
import hashlib
import json
import signal
import sys
import time

import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image

topic, out = sys.argv[1], sys.argv[2]
rclpy.init()
node = Node("hashcount")
state = {"frames": 0, "new": 0, "prev": None, "first": None, "last": None, "series": []}


def cb(msg):
    now = time.time()
    h = hashlib.blake2b(bytes(msg.data), digest_size=16).digest()
    state["frames"] += 1
    is_new = h != state["prev"]
    state["new"] += is_new
    state["prev"] = h
    state["first"] = state["first"] or now
    state["last"] = now
    state["series"].append((round(now, 3), msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9, int(is_new)))


node.create_subscription(Image, topic, cb, qos_profile_sensor_data)
stop = {"now": False}
signal.signal(signal.SIGINT, lambda *_: stop.update(now=True))
signal.signal(signal.SIGTERM, lambda *_: stop.update(now=True))
while not stop["now"]:
    rclpy.spin_once(node, timeout_sec=0.2)
dur = (state["last"] or 0) - (state["first"] or 0)
json.dump({"topic": topic, "frames": state["frames"], "new_frames": state["new"],
           "wall_s": round(dur, 2),
           "delivered_hz": round(state["frames"] / dur, 2) if dur else None,
           "new_hz": round(state["new"] / dur, 2) if dur else None,
           "unique_stamp_hz": round(len({x[1] for x in state["series"]}) / dur, 2) if dur else None,
           "series": state["series"]}, open(out, "w"))
