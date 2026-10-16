#!/usr/bin/env python3
"""OpenVINS <-> TEVV adaptor (the component's own code, not TEVV's).

TEVV gives every component the same fixed interface (tevv.interface.v1),
namespaced by its drone (/tevv/<drone>, e.g. /tevv/Drone1):
  in:  /tevv/<drone>/sensors/<camera>/image_raw, /camera_info,
       /tevv/<drone>/sensors/imu, /tevv/<drone>/mission/*,
       /tevv/<drone>/control_state, /clock, /tf
       $TEVV_INTERFACE  (interface.json: the drone, its namespace, these
                         topics, frames and the calibration of the sensors
                         this package consumes)
  out: /tevv/<drone>/estimate (nav_msgs/Odometry: base_link FLU in a fixed world frame)

This adaptor does the two things OpenVINS needs:
 1. writes OpenVINS's config (/config) from interface.json, so it
    subscribes to TEVV's sensor topics with the right calibration;
 2. republishes OpenVINS's /ov_msckf/odomimu as /tevv/<drone>/estimate.
"""
import json
import math
import os
from pathlib import Path

INTERFACE = Path(os.environ.get("TEVV_INTERFACE", "/cfg/component/interface.json"))
OUT = Path("/config")
HERE = Path(__file__).resolve().parent


def rot_rpy_deg(roll, pitch, yaw):
    r, p, y = (math.radians(v) for v in (roll, pitch, yaw))
    cr, sr, cp, sp, cy, sy = math.cos(r), math.sin(r), math.cos(p), math.sin(p), math.cos(y), math.sin(y)
    return [[cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
            [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
            [-sp, cp * sr, cp * cr]]


def matmul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


# The camera's optical frame (z forward, x right, y down) in its body frame (x forward, FLU).
R_BODY_OPTICAL = [[0.0, 0.0, 1.0], [-1.0, 0.0, 0.0], [0.0, -1.0, 0.0]]


def t_imu_cam(cam):
    """T_imu_cam for OpenVINS. TEVV's IMU is on base_link (FLU), so it is the
    optical frame's pose in base_link."""
    mount = cam["T_base_cam"]
    r = matmul(rot_rpy_deg(*mount["rpy_deg"]), R_BODY_OPTICAL)
    x, y, z = mount["xyz"]
    rows = [r[0] + [x], r[1] + [y], r[2] + [z], [0.0, 0.0, 0.0, 1.0]]
    return "\n" + "\n".join(f"    - [{', '.join(f'{v:.9f}' for v in row)}]" for row in rows)


def camera_block(index, cam, other):
    k = cam["K"]
    return f"""cam{index}:
  T_imu_cam:{t_imu_cam(cam)}
  cam_overlaps: [{other}]
  camera_model: pinhole
  distortion_coeffs: [0.0, 0.0, 0.0, 0.0]
  distortion_model: radtan
  intrinsics: [{k[0]}, {k[4]}, {k[2]}, {k[5]}]
  resolution: [{cam['width']}, {cam['height']}]
  rostopic: {cam['image']}
  timeshift_cam_imu: 0.0
"""


def write_config():
    inputs = json.loads(INTERFACE.read_text())["inputs"]
    pair = inputs["stereo"][inputs["stereo_primary"]]
    left, right = pair["left"], pair["right"]
    cams = inputs["cameras"]
    imucam = "%YAML:1.0\n" + camera_block(0, cams[left], 1) + camera_block(1, cams[right], 0)
    imu = ((HERE / "kalibr_imu_chain.yaml").read_text()
           .replace("@IMU_RATE@", str(inputs["imu"].get("rate_hz", 200)))
           .replace("@IMU_TOPIC@", inputs["imu"]["topic"]))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "kalibr_imucam_chain.yaml").write_text(imucam)
    (OUT / "kalibr_imu_chain.yaml").write_text(imu)
    tmp = OUT / "estimator_config.yaml.tmp"
    tmp.write_text((HERE / "estimator_config.yaml").read_text())
    tmp.replace(OUT / "estimator_config.yaml")  # last: OpenVINS starts when this appears
    print(f"[openvins-adaptor] config written for stereo pair {left}/{right}", flush=True)


def relay_estimate():
    import rclpy
    from nav_msgs.msg import Odometry
    from rclpy.node import Node

    class Adaptor(Node):
        def __init__(self):
            super().__init__("openvins_tevv_adaptor")
            # This drone's estimate topic (interface.json's namespace, e.g. /tevv/Drone1).
            namespace = str(json.loads(INTERFACE.read_text()).get("namespace") or "/tevv").rstrip("/")
            self.pub = self.create_publisher(Odometry, f"{namespace}/estimate", 10)
            self.create_subscription(Odometry, "/ov_msckf/odomimu", self.on_odom, 10)

        def on_odom(self, msg):
            # OpenVINS's IMU frame is TEVV's base_link (FLU); its world is fixed
            # at start-up (TEVV aligns worlds when it scores).
            msg.header.frame_id = "odom"
            msg.child_frame_id = "base_link"
            self.pub.publish(msg)

    rclpy.init()
    rclpy.spin(Adaptor())


if __name__ == "__main__":
    write_config()
    relay_estimate()
