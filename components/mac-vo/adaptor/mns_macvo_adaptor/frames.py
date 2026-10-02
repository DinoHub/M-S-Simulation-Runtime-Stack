"""Frame conventions between MAC-VO and the platform. Pure numpy, no ROS.

MAC-VO reports the pose of its left camera relative to that camera's first
pose, in its own camera convention: x forward, y right, z down (TartanAir's
"NED" camera frame, FRD). The platform scores an estimate of base_link in an
ENU world anchored at the first estimate, base_link FLU (REP 103/105).

With S = diag(1, -1, -1) (FRD <-> FLU), a camera-frame motion T_c0_ct in FRD
is S T S in FLU. The camera sits on base_link at T_bc (from the stack
contract's T_base_cam), so the base motion is

    T_b0_bt = T_bc  (S T_c0_ct S)  T_bc^-1

which is the base_link pose in an odom frame placed at the first estimate.
"""

from __future__ import annotations

import math

import numpy as np

S = np.diag([1.0, -1.0, -1.0])


def quat_to_rot(q) -> np.ndarray:
    """(x, y, z, w) unit quaternion -> 3x3 rotation."""
    x, y, z, w = (float(v) for v in q)
    n = math.sqrt(x * x + y * y + z * z + w * w) or 1.0
    x, y, z, w = x / n, y / n, z / n, w / n
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])


def rot_to_quat(r: np.ndarray) -> tuple[float, float, float, float]:
    """3x3 rotation -> (x, y, z, w), w >= 0."""
    m = np.asarray(r, dtype=float)
    trace = m[0, 0] + m[1, 1] + m[2, 2]
    if trace > 0:
        s = math.sqrt(trace + 1.0) * 2
        w, x = 0.25 * s, (m[2, 1] - m[1, 2]) / s
        y, z = (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = math.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2
        w, x = (m[2, 1] - m[1, 2]) / s, 0.25 * s
        y, z = (m[0, 1] + m[1, 0]) / s, (m[0, 2] + m[2, 0]) / s
    elif m[1, 1] > m[2, 2]:
        s = math.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2
        w, x = (m[0, 2] - m[2, 0]) / s, (m[0, 1] + m[1, 0]) / s
        y, z = 0.25 * s, (m[1, 2] + m[2, 1]) / s
    else:
        s = math.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2
        w, x = (m[1, 0] - m[0, 1]) / s, (m[0, 2] + m[2, 0]) / s
        y, z = (m[1, 2] + m[2, 1]) / s, 0.25 * s
    q = np.array([x, y, z, w])
    q /= np.linalg.norm(q)
    if q[3] < 0:
        q = -q
    return tuple(float(v) for v in q)


def rpy_deg_to_rot(roll: float, pitch: float, yaw: float) -> np.ndarray:
    """Intrinsic Z-Y-X (yaw, pitch, roll) in degrees, REP 103."""
    r, p, y = (math.radians(v) for v in (roll, pitch, yaw))
    rx = np.array([[1, 0, 0], [0, math.cos(r), -math.sin(r)], [0, math.sin(r), math.cos(r)]])
    ry = np.array([[math.cos(p), 0, math.sin(p)], [0, 1, 0], [-math.sin(p), 0, math.cos(p)]])
    rz = np.array([[math.cos(y), -math.sin(y), 0], [math.sin(y), math.cos(y), 0], [0, 0, 1]])
    return rz @ ry @ rx


def make_transform(rotation: np.ndarray, translation) -> np.ndarray:
    t = np.eye(4)
    t[:3, :3] = rotation
    t[:3, 3] = np.asarray(translation, dtype=float)
    return t


def mount_transform(t_base_cam: dict) -> np.ndarray:
    """The contract's camera mount (FLU xyz, rpy degrees) as a 4x4 T_bc."""
    return make_transform(rpy_deg_to_rot(*t_base_cam["rpy_deg"]), t_base_cam["xyz"])


def frd_to_flu(t: np.ndarray) -> np.ndarray:
    """A motion expressed in FRD axes, re-expressed in FLU axes."""
    s4 = np.eye(4)
    s4[:3, :3] = S
    return s4 @ t @ s4


def base_motion(t_cam_frd: np.ndarray, t_bc: np.ndarray) -> np.ndarray:
    """MAC-VO's camera motion (FRD) -> base_link motion (FLU) since the first frame."""
    return t_bc @ frd_to_flu(t_cam_frd) @ np.linalg.inv(t_bc)


def pose_to_matrix(position, orientation) -> np.ndarray:
    return make_transform(quat_to_rot(orientation), position)
