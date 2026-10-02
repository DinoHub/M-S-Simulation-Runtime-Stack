"""`mns-macvo-adaptor run`: MAC-VO on the platform's terms, at runtime.

Inbound, it feeds MAC-VO the stack's primary stereo pair under the topics
MAC-VO-ROS2 hard-codes (the ZED wrapper's left/right image_rect_color):
- paired by exact stamp (the bridge captures both cameras in one call);
- converted from bgr8 to the RGB MAC-VO assumes;
- throttled to what MAC-VO keeps up with.

Outbound, it turns MAC-VO's /macvo/pose (its left camera's pose relative to
the first frame, FRD "NED" camera axes, stamped with only the nanosecond
field of the input time) into the platform's estimate: nav_msgs/Odometry of
base_link in an ENU odom frame anchored at the first estimate, stamped with
the full sim time of the input it was computed from.
"""

from __future__ import annotations

import json
import os
import time
import numpy as np
import rclpy
from geometry_msgs.msg import PoseStamped
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from sensor_msgs.msg import Image
from std_msgs.msg import String

from . import contract as contract_mod
from . import frames
from .node_stamps import StampCache

MACVO_LEFT = "/zed/zed_node/left/image_rect_color"
MACVO_RIGHT = "/zed/zed_node/right/image_rect_color"
MACVO_POSE = "/macvo/pose"


def to_rgb(msg: Image) -> Image:
    """bgr8/bgra8 -> rgb8, keeping the header; rgb8 passes through."""
    if msg.encoding == "rgb8":
        return msg
    channels = {"bgr8": 3, "bgra8": 4}.get(msg.encoding)
    if channels is None:
        return msg
    data = np.frombuffer(msg.data, dtype=np.uint8).reshape(msg.height, msg.step // 1)[
        :, : msg.width * channels].reshape(msg.height, msg.width, channels)
    rgb = np.ascontiguousarray(data[..., 2::-1])
    out = Image()
    out.header = msg.header
    out.height, out.width = msg.height, msg.width
    out.encoding, out.is_bigendian, out.step = "rgb8", 0, msg.width * 3
    out.data = rgb.tobytes()
    return out


class Adaptor(Node):
    def __init__(self) -> None:
        super().__init__("macvo_adaptor")
        doc = contract_mod.load()
        vehicle = contract_mod.vehicle(doc)
        pair, left, right = contract_mod.stereo_pair(vehicle)
        self.t_bc = frames.mount_transform(left["T_base_cam"])
        self.estimate_topic = os.environ.get("MNS_ESTIMATE_TOPIC", vehicle["roles"]["estimate"]["topic"])
        self.min_period = 1.0 / max(0.1, float(os.environ.get("MNS_ADAPTOR_MAX_HZ", "10")))
        self.cache = StampCache()
        self.pending: dict[str, dict[int, Image]] = {"L": {}, "R": {}}
        self.last_relay = 0.0
        self.prev: tuple[float, np.ndarray] | None = None
        self.stats = {"in_pairs": 0, "relayed": 0, "dropped_throttle": 0, "poses": 0,
                      "stamp_misses": 0, "latency_s": None}

        reliable = QoSProfile(reliability=ReliabilityPolicy.RELIABLE,
                              history=HistoryPolicy.KEEP_LAST, depth=5)
        self.create_subscription(Image, left["image"], lambda m: self.on_image("L", m), reliable)
        self.create_subscription(Image, right["image"], lambda m: self.on_image("R", m), reliable)
        self.pub_left = self.create_publisher(Image, MACVO_LEFT, 1)
        self.pub_right = self.create_publisher(Image, MACVO_RIGHT, 1)
        self.create_subscription(PoseStamped, MACVO_POSE, self.on_pose, 10)
        self.pub_est = self.create_publisher(Odometry, self.estimate_topic, 10)
        self.pub_status = self.create_publisher(String, "/mns/component/mac_vo/status", 1)
        self.create_timer(2.0, self.publish_status)
        self.get_logger().info(
            f"stereo {left['image']} + {right['image']} (baseline {pair['baseline_m']} m) -> "
            f"{MACVO_LEFT}/{MACVO_RIGHT}; {MACVO_POSE} -> {self.estimate_topic}")

    @staticmethod
    def _key(msg: Image) -> int:
        return msg.header.stamp.sec * 1_000_000_000 + msg.header.stamp.nanosec

    def on_image(self, side: str, msg: Image) -> None:
        other = "R" if side == "L" else "L"
        key = self._key(msg)
        match = self.pending[other].pop(key, None)
        if match is None:
            self.pending[side][key] = msg
            for pending in self.pending.values():  # keep the waiting rooms small
                while len(pending) > 8:
                    pending.pop(next(iter(pending)))
            return
        left, right = (msg, match) if side == "L" else (match, msg)
        self.stats["in_pairs"] += 1
        now = time.monotonic()
        if now - self.last_relay < self.min_period:
            self.stats["dropped_throttle"] += 1
            return
        self.last_relay = now
        self.cache.add(left.header.stamp.sec, left.header.stamp.nanosec)
        self.pub_left.publish(to_rgb(left))
        self.pub_right.publish(to_rgb(right))
        self.stats["relayed"] += 1

    def on_pose(self, msg: PoseStamped) -> None:
        stamp = self.cache.restore(msg.header.stamp.sec, msg.header.stamp.nanosec)
        if stamp is None:
            self.stats["stamp_misses"] += 1
            return
        p, q = msg.pose.position, msg.pose.orientation
        t_cam = frames.pose_to_matrix((p.x, p.y, p.z), (q.x, q.y, q.z, q.w))
        t_base = frames.base_motion(t_cam, self.t_bc)

        out = Odometry()
        out.header.stamp.sec, out.header.stamp.nanosec = stamp
        out.header.frame_id, out.child_frame_id = "odom", "base_link"
        pos = t_base[:3, 3]
        out.pose.pose.position.x, out.pose.pose.position.y, out.pose.pose.position.z = (float(v) for v in pos)
        qx, qy, qz, qw = frames.rot_to_quat(t_base[:3, :3])
        o = out.pose.pose.orientation
        o.x, o.y, o.z, o.w = qx, qy, qz, qw
        out.pose.covariance[0] = -1.0   # unknown
        t_now = stamp[0] + stamp[1] * 1e-9
        if self.prev is not None and t_now > self.prev[0]:
            dt = t_now - self.prev[0]
            v_world = (pos - self.prev[1][:3, 3]) / dt
            v_body = t_base[:3, :3].T @ v_world
            out.twist.twist.linear.x, out.twist.twist.linear.y, out.twist.twist.linear.z = (
                float(v) for v in v_body)
        else:
            out.twist.covariance[0] = -1.0
        self.prev = (t_now, t_base)
        self.pub_est.publish(out)
        self.stats["poses"] += 1
        clock_now = self.get_clock().now().nanoseconds * 1e-9
        self.stats["latency_s"] = round(clock_now - t_now, 3) if clock_now > 0 else None

    def publish_status(self) -> None:
        self.pub_status.publish(String(data=json.dumps(self.stats)))


def main() -> int:
    rclpy.init()
    try:
        node = Adaptor()
    except contract_mod.ContractError as exc:
        print(f"mac-vo adaptor: {exc}")
        rclpy.shutdown()
        return 2
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    return 0
