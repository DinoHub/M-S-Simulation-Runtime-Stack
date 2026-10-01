#!/usr/bin/env python3
"""Make the simulated A8 frames look like the real camera's before casio sees them.

Subscribes /camera/image_sim + /camera/camera_info_sim (the bridge's ideal
pinhole frames, renamed there by apply-realism.sh) and publishes
/camera/image_raw + /camera/camera_info the way the real A8 path would:

  1. lens    remaps the pinhole image onto the A8's Kalibr model (K and
             plumb_bob D from calibration_file). camera_info then carries that
             K and D, as casio_siyi_cam publishes them on the drone.
  2. noise   shot + read noise per pixel, sigma = sqrt(noise_read^2 +
             noise_gain * I) for I in 0..255.
  3. jpeg    a JPEG round trip at jpeg_quality, standing in for the block
             artefacts of the A8's H.264 stream (0 turns it off).

Each stage has its own parameter so a run can switch one off for an A/B.
Runs in the casio-node image (rclpy, numpy, cv2, yaml).
"""
import array
import time

import cv2
import numpy as np
import rclpy
import yaml
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, qos_profile_sensor_data
from sensor_msgs.msg import CameraInfo, Image

NOISE_POOL = 4  # pre-drawn unit-normal frames, picked at random per image


def load_calibration(path):
    doc = yaml.safe_load(open(path))
    k = np.array(doc["camera_matrix"]["data"], np.float64).reshape(3, 3)
    d = np.array(doc["distortion_coefficients"]["data"], np.float64)
    p = np.array(doc["projection_matrix"]["data"], np.float64).reshape(3, 4)
    return int(doc["image_width"]), int(doc["image_height"]), k, d, p


class CameraRealism(Node):
    def __init__(self):
        super().__init__("camera_realism")
        self.lens = self.declare_parameter("lens", True).value
        self.noise_read = float(self.declare_parameter("noise_read", 1.5).value)
        self.noise_gain = float(self.declare_parameter("noise_gain", 0.08).value)
        self.jpeg_quality = int(self.declare_parameter("jpeg_quality", 40).value)
        calibration = self.declare_parameter(
            "calibration_file", "/configs/siyi_a8_calibration.yaml").value
        self.width, self.height, self.k, self.d, self.p = load_calibration(calibration)

        self.maps = None  # built from the first sim camera_info
        self.sim_k = None
        self.rng = np.random.default_rng()
        self.pool = None
        sigma = np.sqrt(self.noise_read ** 2 + self.noise_gain * np.arange(256, dtype=np.float32))
        self.sigma_lut = sigma.astype(np.float32)
        self.stats = {"n": 0, "s": 0.0, "t": time.monotonic()}

        out_qos = QoSProfile(depth=5, reliability=ReliabilityPolicy.RELIABLE)
        self.image_pub = self.create_publisher(Image, "/camera/image_raw", out_qos)
        self.info_pub = self.create_publisher(CameraInfo, "/camera/camera_info", out_qos)
        self.create_subscription(CameraInfo, "/camera/camera_info_sim", self.on_info, qos_profile_sensor_data)
        self.create_subscription(Image, "/camera/image_sim", self.on_image, qos_profile_sensor_data)
        self.get_logger().info(
            f"camera_realism: /camera/image_sim -> /camera/image_raw; lens={self.lens} "
            f"noise(read={self.noise_read}, gain={self.noise_gain}) jpeg_quality={self.jpeg_quality}; "
            f"A8 K fx={self.k[0, 0]:.1f} cx={self.k[0, 2]:.1f} cy={self.k[1, 2]:.1f} D={self.d.round(4).tolist()}")

    def on_info(self, msg):
        if self.sim_k is None and msg.k[0] > 0:
            self.sim_k = np.array(msg.k, np.float64).reshape(3, 3)
            self.build_maps()

    def build_maps(self):
        """For each pixel of the A8 image, where it falls in the sim's pinhole image."""
        u, v = np.meshgrid(np.arange(self.width, dtype=np.float32), np.arange(self.height, dtype=np.float32))
        pts = np.stack([u.ravel(), v.ravel()], axis=1).reshape(-1, 1, 2)
        undistorted = cv2.undistortPoints(pts, self.k, self.d, P=self.sim_k).reshape(self.height, self.width, 2)
        self.maps = cv2.convertMaps(undistorted[..., 0], undistorted[..., 1], cv2.CV_16SC2)
        self.get_logger().info(
            f"lens map built: sim fx={self.sim_k[0, 0]:.1f} cx={self.sim_k[0, 2]:.1f} -> A8 model")

    def on_image(self, msg):
        if msg.encoding not in ("bgr8", "rgb8"):
            self.get_logger().warn(f"unsupported encoding {msg.encoding}", throttle_duration_sec=5.0)
            return
        if self.lens and self.maps is None:
            return  # waiting for the sim camera_info
        t0 = time.monotonic()
        img = np.frombuffer(msg.data, np.uint8).reshape(msg.height, msg.step)[:, : msg.width * 3]
        img = img.reshape(msg.height, msg.width, 3)

        if self.lens:
            img = cv2.remap(img, self.maps[0], self.maps[1], cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        if self.noise_read > 0 or self.noise_gain > 0:
            if self.pool is None or self.pool.shape[1:] != img.shape:
                self.pool = self.rng.standard_normal((NOISE_POOL,) + img.shape, dtype=np.float32)
            noise = self.pool[self.rng.integers(NOISE_POOL)]
            img = np.clip(img + self.sigma_lut[img] * noise, 0, 255).astype(np.uint8)
        if self.jpeg_quality > 0:
            ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality])
            if ok:
                img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
                if msg.encoding == "rgb8":
                    img = img[:, :, ::-1]

        out = Image()
        out.header = msg.header
        out.height, out.width = img.shape[:2]
        out.encoding = msg.encoding
        out.step = out.width * 3
        # array('B') takes rclpy's fast path; assigning bytes checks every
        # element in Python and costs about 120 ms for a 1280 x 720 frame.
        out.data = array.array("B", np.ascontiguousarray(img).tobytes())
        self.image_pub.publish(out)
        self.info_pub.publish(self.camera_info(msg.header, out.width, out.height))

        s = self.stats
        s["n"] += 1
        s["s"] += time.monotonic() - t0
        if time.monotonic() - s["t"] > 30.0:
            self.get_logger().info(f"{s['n'] / (time.monotonic() - s['t']):.1f} fps, "
                                   f"{1000 * s['s'] / max(1, s['n']):.1f} ms per frame")
            self.stats = {"n": 0, "s": 0.0, "t": time.monotonic()}

    def camera_info(self, header, width, height):
        info = CameraInfo()
        info.header = header
        info.width, info.height = width, height
        info.distortion_model = "plumb_bob"
        if self.lens:
            info.k = self.k.ravel().tolist()
            info.d = self.d.tolist()
            info.p = self.p.ravel().tolist()
        else:
            k = self.sim_k if self.sim_k is not None else self.k
            info.k = k.ravel().tolist()
            info.d = [0.0] * 5
            info.p = np.hstack([k, np.zeros((3, 1))]).ravel().tolist()
        info.r = np.eye(3).ravel().tolist()
        return info


def main():
    rclpy.init()
    node = CameraRealism()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
