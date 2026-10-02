#!/usr/bin/env python3
"""Save annotated frames as evidence: casio's own overlay on the exact camera frame.

  snapshots.py --out DIR [--min-h 40] [--every 2] [--max 60]

Runs in the casio-node image beside square_flight.py. It joins
/camera/image_raw with /target/gps/frames by exact stamp and draws the boxes
with casio_detection's gps_boxes + draw_overlays, the same code the
annotated RTSP stream uses (box, class, confidence, #track, lat/lon). A frame
is kept when a target box is at least --min-h px tall, at most one per
--every seconds. Below the frame goes a caption bar (not casio's): drone
height, casio's distance, and, when DIR/gt_objects.csv exists (gt_objects.py
writes it), the horizontal error from each estimated lat/lon to the nearest
mannequin. Writes DIR/snapshots/NNN.jpg and NNN.json.
"""
import argparse
import csv
import json
import math
import os

import cv2
import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.parameter import Parameter
from rclpy.qos import qos_profile_sensor_data
from mavros_msgs.msg import HomePosition
from nav_msgs.msg import Odometry
from sensor_msgs.msg import Image
from casio_interfaces.msg import TargetGpsArray
from casio_detection.detector_node import image_to_bgr
from casio_detection.drawing import draw_overlays
from casio_detection.frame_overlay import FrameOverlayJoin, gps_boxes

EARTH_R = 6378137.0


class Snapshots(Node):
    def __init__(self, a):
        super().__init__("casio_snapshots", parameter_overrides=[Parameter("use_sim_time", value=True)])
        self.a = a
        self.dir = os.path.join(a.out, "snapshots")
        os.makedirs(self.dir, exist_ok=True)
        self.join = FrameOverlayJoin(maxlen=90)
        self.home = None
        self.odom = None
        self.last_saved = -1e9
        self.count = 0
        sd = qos_profile_sensor_data
        self.create_subscription(Image, "/camera/image_raw", self.join.add_image, sd)
        self.create_subscription(TargetGpsArray, "/target/gps/frames", self.on_frame, 10)
        self.create_subscription(HomePosition, "/mavros/home_position/home", self.on_home, sd)
        self.create_subscription(Odometry, "/mavros/global_position/local", self.on_odom, sd)

    def on_home(self, m):
        self.home = (m.geo.latitude, m.geo.longitude)

    def on_odom(self, m):
        self.odom = m

    def truth(self):
        """Latest ENU of each object from gt_objects.csv (static mannequins: any row works)."""
        path = os.path.join(self.a.out, "gt_objects.csv")
        latest = {}
        if os.path.exists(path):
            with open(path, newline="") as f:
                for r in csv.DictReader(f):
                    latest[r["name"]] = (float(r["e"]), float(r["n"]))
        return latest

    def on_frame(self, msg):
        self.join.add_frame(msg)
        ready = self.join.pop_ready()
        if ready is None or self.count >= self.a.max:
            return
        image, result = ready
        t = image.header.stamp.sec + image.header.stamp.nanosec * 1e-9
        tall = [g for g in result.targets if g.bbox[3] >= self.a.min_h]
        if not tall or t - self.last_saved < self.a.every:
            return
        frame = image_to_bgr(image)
        if frame is None:
            return
        frame = draw_overlays(frame, gps_boxes(result.targets))

        truth = self.truth()
        lines, rows = [], []
        alt = self.odom.pose.pose.position.z if self.odom else float("nan")
        for g in result.targets:
            row = {"track_id": g.track_id, "confidence": g.confidence, "bbox_cxcywh": list(g.bbox),
                   "lat": g.latitude, "lon": g.longitude, "distance_m": g.distance_m}
            text = f"#{g.track_id} {g.class_id} {g.confidence:.2f} box {g.bbox[3]:.0f}px casio dist {g.distance_m:.1f} m"
            if self.home and truth:
                lat0, lon0 = self.home
                e = math.radians(g.longitude - lon0) * EARTH_R * math.cos(math.radians(lat0))
                n = math.radians(g.latitude - lat0) * EARTH_R
                name, err = min(((k, math.hypot(e - v[0], n - v[1])) for k, v in truth.items()),
                                key=lambda kv: kv[1])
                row.update(nearest_truth=name, horizontal_error_m=err)
                text += f" | GPS error to nearest mannequin {err:.1f} m"
            rows.append(row)
            lines.append(text)
        bar = np.zeros((30 + 26 * len(lines), frame.shape[1], 3), np.uint8)
        cv2.putText(bar, f"sim t={t:.1f}  drone {alt:.1f} m above home  (caption added by bench/snapshots.py)",
                    (10, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 1)
        for i, text in enumerate(lines):
            cv2.putText(bar, text, (10, 48 + 26 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
        self.count += 1
        stem = os.path.join(self.dir, f"{self.count:03d}")
        cv2.imwrite(stem + ".jpg", np.vstack([frame, bar]), [cv2.IMWRITE_JPEG_QUALITY, 92])
        with open(stem + ".json", "w") as f:
            json.dump({"t": t, "drone_alt_m": alt, "targets": rows}, f, indent=1)
        self.last_saved = t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--min-h", type=float, default=40.0, help="smallest box height (px) worth keeping")
    ap.add_argument("--every", type=float, default=2.0, help="seconds between kept frames")
    ap.add_argument("--max", type=int, default=60)
    a = ap.parse_args()
    rclpy.init()
    node = Snapshots(a)
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, rclpy.executors.ExternalShutdownException):
        pass


if __name__ == "__main__":
    main()
