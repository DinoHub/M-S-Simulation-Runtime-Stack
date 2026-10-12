#!/usr/bin/env python3
"""Refuse to run OpenVINS on a stack its calibration does not describe.

The calibration in config/ is the frozen one of the reference stereo rig
(calibration-campaign-v1): kalibr chains with fixed topics, intrinsics and
camera-IMU extrinsics. Attached to another rig, OpenVINS would start, and
diverge or drift for reasons that look like the estimator's. This compares
the stack contract (/cfg/stack/contract.json) with the chains and exits 1
naming each mismatch.
"""
import json
import math
import os
import sys

import yaml

CONFIG = os.environ.get("OPENVINS_CONFIG", "/cfg/component/config")
CONTRACT = os.environ.get("MNS_CONTRACT", "/cfg/stack/contract.json")
INDEX = int(os.environ.get("MNS_VEHICLE_INDEX", "1"))


def kalibr(name):
    text = open(os.path.join(CONFIG, name)).read()
    return yaml.safe_load(text.replace("%YAML:1.0", "", 1))


def main():
    contract = json.load(open(CONTRACT))
    vehicle = next(v for v in contract["vehicles"] if v["index"] == INDEX)
    roles = vehicle["roles"]
    problems = []
    pair = roles["stereo"].get(roles.get("stereo_primary") or "")
    if not pair:
        print("openvins check: this stack has no stereo pair; the shipped calibration is a stereo rig")
        return 1
    cams = [roles["cameras"][pair["left"]], roles["cameras"][pair["right"]]]
    chain = kalibr("kalibr_imucam_chain.yaml")
    for i, cam in enumerate(cams):
        k = chain[f"cam{i}"]
        if k["rostopic"] != cam["image"]:
            problems.append(f"cam{i}: calibration reads {k['rostopic']}, the stack publishes {cam['image']}")
        if list(k["resolution"]) != [cam.get("width"), cam.get("height")]:
            problems.append(f"cam{i}: calibration is {k['resolution']}, the stack renders "
                            f"{[cam.get('width'), cam.get('height')]}")
        K = cam.get("K") or []
        if len(K) == 9:
            want = [K[0], K[4], K[2], K[5]]
            if any(abs(a - b) > 0.5 for a, b in zip(k["intrinsics"], want)):
                problems.append(f"cam{i}: calibration intrinsics {k['intrinsics']}, the stack's "
                                f"{[round(v, 2) for v in want]}")
    t0 = [row[3] for row in chain["cam0"]["T_imu_cam"][:3]]
    t1 = [row[3] for row in chain["cam1"]["T_imu_cam"][:3]]
    baseline = math.dist(t0, t1)
    if abs(baseline - float(pair["baseline_m"])) > 0.005:
        problems.append(f"baseline: calibration {baseline:.3f} m, the stack {pair['baseline_m']} m")
    imu = kalibr("kalibr_imu_chain.yaml")["imu0"]["rostopic"]
    if roles.get("imu", {}).get("topic") != imu:
        problems.append(f"imu: calibration reads {imu}, the stack publishes {roles.get('imu', {}).get('topic')}")
    if problems:
        print("openvins check: the shipped calibration does not describe this stack:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(f"openvins check: calibration matches {pair['left']} + {pair['right']} "
          f"(baseline {pair['baseline_m']} m) and {imu}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
