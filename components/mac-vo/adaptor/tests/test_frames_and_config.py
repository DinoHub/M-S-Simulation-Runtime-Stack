"""Frame conversion, stamp repair and config generation. numpy + yaml only."""

import math
import unittest

import numpy as np

from mns_macvo_adaptor import config_gen, frames
from mns_macvo_adaptor.contract import ContractError
from mns_macvo_adaptor.node_stamps import StampCache  # re-exported pure helper


def contract(stereo=True):
    left = {"image": "/camera/front/image_raw", "encoding": "bgr8", "width": 640, "height": 480,
            "K": [381.36, 0, 320.0, 0, 381.36, 240.0, 0, 0, 1], "rectified": True,
            "T_base_cam": {"xyz": [0.15, 0.0, 0.25], "rpy_deg": [0, 0, 0]}}
    right = dict(left, image="/camera/front_right/image_raw",
                 T_base_cam={"xyz": [0.15, -0.12, 0.25], "rpy_deg": [0, 0, 0]})
    roles = {"cameras": {"front": left, "front_right": right},
             "stereo": {"front": {"left": "front", "right": "front_right", "baseline_m": 0.12}} if stereo else {},
             "stereo_primary": "front" if stereo else None,
             "estimate": {"topic": "/mns/estimate/odom"}}
    return {"schema": "mns.stack.contract.v1", "vehicles": [{"index": 1, "roles": roles}]}


def yaw_motion(yaw_deg, xyz):
    return frames.make_transform(frames.rpy_deg_to_rot(0, 0, yaw_deg), xyz)


class FrameTests(unittest.TestCase):
    def test_quaternion_round_trip(self):
        r = frames.rpy_deg_to_rot(10, -20, 135)
        self.assertTrue(np.allclose(frames.quat_to_rot(frames.rot_to_quat(r)), r, atol=1e-9))

    def test_base_motion_recovers_the_true_trajectory(self):
        """Simulate what MAC-VO would report for a known base trajectory and
        check the adaptor turns it back into that trajectory."""
        t_bc = frames.mount_transform({"xyz": [0.15, 0.0, 0.25], "rpy_deg": [0, 25, 0]})
        s4 = np.eye(4)
        s4[:3, :3] = frames.S
        for yaw, xyz in [(0, (5, 0, 2)), (90, (5, 5, 2)), (-37, (-3, 2, 1.5))]:
            t_base = yaw_motion(yaw, xyz)                         # truth: base motion (FLU)
            t_cam_flu = np.linalg.inv(t_bc) @ t_base @ t_bc       # camera motion (FLU)
            t_cam_frd = s4 @ t_cam_flu @ s4                       # what MAC-VO reports (FRD)
            got = frames.base_motion(t_cam_frd, t_bc)
            self.assertTrue(np.allclose(got, t_base, atol=1e-9), (yaw, xyz))

    def test_forward_in_camera_is_forward_in_base(self):
        t_bc = frames.mount_transform({"xyz": [0.15, 0, 0.25], "rpy_deg": [0, 0, 0]})
        t_cam_frd = frames.make_transform(np.eye(3), (2.0, 0, 0))  # 2 m forward, FRD
        got = frames.base_motion(t_cam_frd, t_bc)
        self.assertTrue(np.allclose(got[:3, 3], (2.0, 0, 0)))
        # right in FRD (+y) is -y (left negative) in FLU
        got = frames.base_motion(frames.make_transform(np.eye(3), (0, 1.0, 0)), t_bc)
        self.assertTrue(np.allclose(got[:3, 3], (0, -1.0, 0)))
        # down in FRD (+z) is -z in FLU
        got = frames.base_motion(frames.make_transform(np.eye(3), (0, 0, 1.0)), t_bc)
        self.assertTrue(np.allclose(got[:3, 3], (0, 0, -1.0)))


class StampTests(unittest.TestCase):
    def test_restores_the_seconds_macvo_drops(self):
        cache = StampCache(size=3)
        cache.add(1790000123, 250_000_000)
        self.assertEqual(cache.restore(0, 250_000_000), (1790000123, 250_000_000))
        self.assertIsNone(cache.restore(0, 999))

    def test_a_whole_stamp_passes_through(self):
        self.assertEqual(StampCache().restore(5, 7), (5, 7))

    def test_cache_is_bounded(self):
        cache = StampCache(size=2)
        for n in (1, 2, 3):
            cache.add(10, n)
        self.assertIsNone(cache.restore(0, 1))
        self.assertEqual(cache.restore(0, 3), (10, 3))


class ConfigTests(unittest.TestCase):
    def test_camera_block_from_the_stereo_pair(self):
        config, check = config_gen.build_config({"Odometry": {"name": "x"}}, contract())
        self.assertEqual(config["Camera"], {"fx": 381.36, "fy": 381.36, "cx": 320.0, "cy": 240.0, "bl": 0.12})
        self.assertEqual(config["Odometry"], {"name": "x"})
        self.assertEqual(check["left"]["topic"], "/camera/front/image_raw")

    def test_no_stereo_pair_is_a_clear_error(self):
        with self.assertRaisesRegex(ContractError, "needs a stereo pair; this scenario has cameras: front, front_right"):
            config_gen.build_config({}, contract(stereo=False))


if __name__ == "__main__":
    unittest.main()
