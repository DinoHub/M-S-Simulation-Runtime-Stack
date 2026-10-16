#!/usr/bin/env python3
"""VIO navigation stack <-> TEVV adaptor (the stack's own code, not TEVV's).

The stack is two components of ours wired together: OpenVINS estimates the
vehicle's pose from the stereo pair and the IMU, and SUPER plans on that
estimate, not on ground truth. TEVV gives the stack its fixed interface
(tevv.interface.v1), namespaced by its drone (/tevv/<drone>, e.g. /tevv/Drone1;
written /tevv/... below); this adaptor fits both to it:

  OpenVINS config   written from interface.json (the stereo pair's calibration,
                    the IMU's rate and topic), as in the openvins-vio package
  /tevv/estimate    OpenVINS's /ov_msckf/odomimu (base_link in OpenVINS's world)
  SUPER's odometry  the same estimate, moved into TEVV's odom frame (below)
  SUPER's map       /tevv/sensors/point_cloud (TEVV's registered cloud, in odom)
  SUPER's goal      /tevv/mission/next (the point to reach now)
  /tevv/command     SUPER's PositionTarget, sent on as a velocity with yaw

Frames. OpenVINS's world is fixed where it initialised, with its own heading;
goals and commands are in TEVV's odom. So the stack takes one known start
pose, as a real mission gets its origin from the operator: the first ground
truth sample at the stamp of OpenVINS's first estimate gives T_odom_vio, once.
After that the ground truth subscription is closed, and SUPER flies on the
estimate alone: when OpenVINS drifts, SUPER's picture of where it is drifts
with it. That is what a stack test measures.
"""
import json
import math
import os
from pathlib import Path

INTERFACE = Path(os.environ.get("TEVV_INTERFACE", "/cfg/component/interface.json"))
OUT = Path("/config")
HERE = Path(__file__).resolve().parent


def tevv(name):
    """A TEVV topic for this drone: tevv("/gt/odom") -> /tevv/Drone1/gt/odom."""
    namespace = str(json.loads(INTERFACE.read_text()).get("namespace") or "/tevv").rstrip("/")
    return namespace + name


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


# --- the relay ------------------------------------------------------------------

SUPER_ODOM = "/px4vision_custom_0/mavros/local_position/odom"   # files/config/click.yaml
SUPER_CLOUD = "/px4vision_custom_0/lidar"
SUPER_GOAL = "/goal"
SUPER_SETPOINT = "/px4vision_custom_0/mavros/setpoint_raw/local"
VIO_ODOM = "/ov_msckf/odomimu"


def q_mul(a, b):
    """Hamilton product of quaternions (x, y, z, w)."""
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz)


def q_conj(q):
    return (-q[0], -q[1], -q[2], q[3])


def q_rotate(q, v):
    x, y, z, _ = q_mul(q_mul(q, (v[0], v[1], v[2], 0.0)), q_conj(q))
    return (x, y, z)


def pose_of(msg):
    p, o = msg.pose.pose.position, msg.pose.pose.orientation
    return (p.x, p.y, p.z), (o.x, o.y, o.z, o.w)


def stamp_s(msg):
    return msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9


def align(gt, vio):
    """T_odom_vio = T_odom_base (ground truth) * inverse(T_vio_base (estimate))."""
    (tg, qg), (tv, qv) = gt, vio
    q = q_mul(qg, q_conj(qv))
    r = q_rotate(q, tv)
    return (tg[0] - r[0], tg[1] - r[1], tg[2] - r[2]), q


def apply(transform, pose):
    (t, q), (tp, qp) = transform, pose
    r = q_rotate(q, tp)
    return (t[0] + r[0], t[1] + r[1], t[2] + r[2]), q_mul(q, qp)


def velocity_command(msg):
    """SUPER's PositionTarget -> /tevv/command: its velocity (odom ENU) and yaw."""
    from mavros_msgs.msg import PositionTarget
    out = PositionTarget()
    out.header.stamp = msg.header.stamp
    out.header.frame_id = "odom"
    out.coordinate_frame = PositionTarget.FRAME_LOCAL_NED  # MAVROS's name for its ENU frame
    out.type_mask = (PositionTarget.IGNORE_PX | PositionTarget.IGNORE_PY | PositionTarget.IGNORE_PZ
                     | PositionTarget.IGNORE_AFX | PositionTarget.IGNORE_AFY | PositionTarget.IGNORE_AFZ
                     | PositionTarget.IGNORE_YAW_RATE)
    out.velocity = msg.velocity
    out.yaw = msg.yaw
    return out


def run():
    import rclpy
    from geometry_msgs.msg import PoseStamped
    from mavros_msgs.msg import PositionTarget
    from nav_msgs.msg import Odometry
    from rclpy.node import Node
    from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
    from sensor_msgs.msg import PointCloud2

    latched = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                         durability=DurabilityPolicy.TRANSIENT_LOCAL)

    class Adaptor(Node):
        def __init__(self):
            super().__init__("vio_nav_stack_adaptor")
            self.estimate = self.create_publisher(Odometry, tevv("/estimate"), 10)
            self.super_odom = self.create_publisher(Odometry, SUPER_ODOM, 10)
            self.command = self.create_publisher(PositionTarget, tevv("/command"), 10)
            cloud = self.create_publisher(PointCloud2, SUPER_CLOUD, 10)
            goal = self.create_publisher(PoseStamped, SUPER_GOAL, latched)
            self.create_subscription(PointCloud2, tevv("/sensors/point_cloud"), cloud.publish, 10)
            self.create_subscription(PoseStamped, tevv("/mission/next"), goal.publish, latched)
            self.create_subscription(PositionTarget, SUPER_SETPOINT,
                                     lambda m: self.command.publish(velocity_command(m)), 10)
            self.create_subscription(Odometry, VIO_ODOM, self.on_vio, 10)
            self.truth = []          # recent ground truth, until the start pose is taken
            self.gt_sub = self.create_subscription(Odometry, tevv("/gt/odom"), self.on_truth, 10)
            self.transform = None
            self.get_logger().info(f"{VIO_ODOM} -> {tevv('/estimate')} and (aligned) {SUPER_ODOM}; "
                                   f"{tevv('/sensors/point_cloud')} -> {SUPER_CLOUD}; {tevv('/mission/next')} -> "
                                   f"{SUPER_GOAL}; {SUPER_SETPOINT} -> {tevv('/command')}")

        def on_truth(self, msg):
            self.truth.append((stamp_s(msg), pose_of(msg)))
            del self.truth[:-400]

        def on_vio(self, msg):
            # OpenVINS's IMU frame is TEVV's base_link (FLU); TEVV aligns worlds when it scores.
            msg.header.frame_id, msg.child_frame_id = "odom", "base_link"
            self.estimate.publish(msg)
            if self.transform is None:
                if not self.truth:
                    return
                t = stamp_s(msg)
                _, gt = min(self.truth, key=lambda item: abs(item[0] - t))
                self.transform = align(gt, pose_of(msg))
                # The start pose is taken: from here SUPER flies on the estimate alone.
                self.destroy_subscription(self.gt_sub)
                self.truth = []
                (x, y, z), q = self.transform
                self.get_logger().info(f"start pose taken: T_odom_vio t=({x:.2f}, {y:.2f}, {z:.2f}) "
                                       f"q=({q[0]:.3f}, {q[1]:.3f}, {q[2]:.3f}, {q[3]:.3f}); "
                                       "ground truth no longer read")
                # Kept with the run (TEVV's output folder for this package), so a
                # reader can see when ground truth was last read.
                out = Path("/out")
                if out.is_dir():
                    (out / "start_pose.json").write_text(json.dumps({
                        "taken_at_stamp": t, "T_odom_vio": {"t": [x, y, z], "q": list(q)},
                        "note": "the only ground truth the stack read; SUPER flew on OpenVINS's estimate after this"},
                        indent=2) + "\n")
            (x, y, z), q = apply(self.transform, pose_of(msg))
            out = Odometry()
            out.header.stamp = msg.header.stamp
            out.header.frame_id, out.child_frame_id = "odom", "base_link"
            p, o = out.pose.pose.position, out.pose.pose.orientation
            p.x, p.y, p.z = x, y, z
            o.x, o.y, o.z, o.w = q
            out.twist = msg.twist           # body-frame velocity: unchanged by a world transform
            self.super_odom.publish(out)

    rclpy.init()
    rclpy.spin(Adaptor())


if __name__ == "__main__":
    write_config()
    run()
