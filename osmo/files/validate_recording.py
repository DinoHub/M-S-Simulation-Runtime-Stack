#!/usr/bin/env python3
"""Is this recording sound? Asked before any score from it is trusted.

A campaign's evaluator scores a trajectory. That is a different question, and it
assumes the recording underneath is sound. These gates check that assumption.
Every one of them is a defect this project actually shipped, turned into a
check:

  image_stamps       monotonic, no stall, fast enough          (capture-time stamping)
  fresh_frames       consecutive frames differ                 (a stalled publisher)
  stereo_skew        left and right stamped together           (unpaired stereo)
  truth_valid        one pose per stamp, no jumps, good rate   (collider displacement)
  truth_not_estimate truth is the physics body, not the
                     flight controller's own estimate          (a whole bridge release)
  fc_nominal         no failsafe or estimator reset            (a wall, then a blind land)
  imu_gravity        gravity on the right axis at rest         (frame sign)
  imu_flu            gyro axes agree with the truth twist      (a doubly-rotated twist)
  twist_body_frame   odometry twist is body-frame (REP 105)
  intrinsics         camera_info matches the calibration

Two of these caught real bugs whose results had already been written up as
findings, which is the argument for running them on every run rather than when
something looks wrong.

Topics come from the run's role map, not from this file: a campaign names its
own ground truth, estimate and inertial topics, and cameras are whatever Image
streams the recording actually holds. So a rig with one camera, two, or five is
checked the same way without editing anything here.

A gate whose inputs were not recorded is skipped, never failed. Skipping is not
passing and says so.

    validate_recording.py <bundle-dir> [--topics topics.yaml] [--vehicle NAME]
                          [--calib kalibr_imucam_chain.yaml] [--json OUT]

Writes <bundle>/validation.json. Exit 0 if nothing failed, 1 if something did,
2 if the recording could not be read at all.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path

import numpy as np
import yaml

IMAGE_TYPE = "sensor_msgs/msg/Image"
INFO_TYPE = "sensor_msgs/msg/CameraInfo"
IMU_TYPE = "sensor_msgs/msg/Imu"
ODOM_TYPE = "nav_msgs/msg/Odometry"
STATUSTEXT_TYPE = "mavros_msgs/msg/StatusText"

# Anything in a flight controller's status stream that means the flight was not
# nominal, so a score from it is measuring the failure rather than the estimator.
NOT_NOMINAL = ("Failsafe", "failsafe", "invalid setpoints", "EKF", "reset", "Land now")

# Below this the tracker is being starved rather than tested. Two cameras halve
# the per-camera render rate, so the floor is per camera, not per recording.
MIN_IMAGE_HZ = 10.0
MAX_IMAGE_GAP_S = 0.5
# Stereo pairs are requested in one batch and should share a stamp. Half a frame
# at the floor rate is already far looser than a working rig produces.
MAX_STEREO_SKEW_S = 0.05
MIN_TRUTH_HZ = 30.0
# Nothing in these campaigns flies near this; a step above it is a teleport.
MAX_TRUTH_SPEED = 8.0


def stamp(header) -> float:
    return header.stamp.sec + header.stamp.nanosec * 1e-9


def yaw_of(q) -> float:
    return math.atan2(2 * (q.w * q.z + q.x * q.y), 1 - 2 * (q.y * q.y + q.z * q.z))


def bag_topics(bundle: Path) -> dict[str, str]:
    """Topic name to message type, from the recording's own metadata."""
    meta = yaml.safe_load((bundle / "metadata.yaml").read_text())
    info = meta.get("rosbag2_bagfile_information") or meta
    out = {}
    for entry in info.get("topics_with_message_count") or []:
        topic = entry.get("topic_metadata") or {}
        if topic.get("name"):
            out[str(topic["name"])] = str(topic.get("type") or "")
    return out


def resolve_role(pattern: str, present: dict[str, str], vehicle: str,
                 fallback_type: str | None = None) -> tuple[str | None, str]:
    """A role map entry to a topic that is actually in the recording.

    Role maps carry a `{v}` vehicle placeholder, which is empty on a flat
    namespace and a prefix on a per-vehicle one, so try both and take whichever
    the recording has.

    When none of them match, fall back to the message type: if the recording
    holds exactly one stream of the right kind, that is the one, and the caller
    is told the map did not match. A stale role map should not silently turn a
    gate into "zero samples", which reads as a broken sensor rather than a
    mismatched name. Returns the topic and how it was found.
    """
    if pattern:
        candidates = [pattern.replace("{v}", ""), pattern.replace("{v}", f"{vehicle}/")]
        if vehicle:
            candidates.append(pattern.replace("{v}", f"{vehicle}/mavros/"))
        for candidate in candidates:
            if candidate in present:
                return candidate, "role map"
    if fallback_type:
        matches = sorted(t for t, kind in present.items() if kind == fallback_type)
        if len(matches) == 1:
            return matches[0], f"by type, role map {pattern!r} matched nothing"
    return None, "not found"


def read_recording(bundle: Path, roles: dict[str, str | None], present: dict[str, str]):
    """One pass over the bag, keeping only what the gates need."""
    import rosbag2_py
    from rclpy.serialization import deserialize_message
    from nav_msgs.msg import Odometry
    from sensor_msgs.msg import CameraInfo, Image, Imu
    try:
        # Only present where a flight controller's status stream was recorded;
        # its absence skips a gate rather than failing the whole validation.
        from mavros_msgs.msg import StatusText
    except ImportError:
        StatusText = None

    cameras = sorted(t for t, kind in present.items() if kind == IMAGE_TYPE)
    infos = sorted(t for t, kind in present.items() if kind == INFO_TYPE)
    # A flight controller's own odometry, if it was recorded: any Odometry
    # stream that is not the declared ground truth.
    fc_odom = sorted(t for t, kind in present.items()
                     if kind == ODOM_TYPE and t != roles.get("ground_truth"))
    texts = sorted(t for t, kind in present.items() if kind == STATUSTEXT_TYPE)

    images: dict[str, list] = {t: [] for t in cameras}
    info_of: dict[str, tuple] = {}
    imu: list = []
    truth: list = []
    fc_pose: list = []
    fc_text: list = []

    reader = rosbag2_py.SequentialReader()
    storage = yaml.safe_load((bundle / "metadata.yaml").read_text())
    storage_id = ((storage.get("rosbag2_bagfile_information") or storage)
                  .get("storage_identifier") or "sqlite3")
    reader.open(rosbag2_py.StorageOptions(uri=str(bundle), storage_id=str(storage_id)),
                rosbag2_py.ConverterOptions("", ""))
    while reader.has_next():
        topic, data, _ = reader.read_next()
        if topic in images:
            m = deserialize_message(data, Image)
            # A cheap content fingerprint: enough to see a frame repeat, far
            # cheaper than hashing every pixel of every frame.
            images[topic].append(
                (stamp(m.header),
                 hashlib.blake2b(bytes(m.data[::97]), digest_size=8).hexdigest()))
        elif topic in infos and topic not in info_of:
            m = deserialize_message(data, CameraInfo)
            info_of[topic] = (m.k[0], m.k[4], m.k[2], m.k[5], m.width, m.height)
        elif topic == roles.get("imu"):
            m = deserialize_message(data, Imu)
            a, w = m.linear_acceleration, m.angular_velocity
            imu.append((stamp(m.header), a.x, a.y, a.z, w.x, w.y, w.z))
        elif topic == roles.get("ground_truth"):
            m = deserialize_message(data, Odometry)
            p, q = m.pose.pose.position, m.pose.pose.orientation
            v, w = m.twist.twist.linear, m.twist.twist.angular
            truth.append((stamp(m.header), p.x, p.y, p.z, yaw_of(q),
                          v.x, v.y, v.z, w.x, w.y, w.z,
                          m.header.frame_id, m.child_frame_id))
        elif topic in fc_odom:
            m = deserialize_message(data, Odometry)
            p = m.pose.pose.position
            fc_pose.append((stamp(m.header), p.x, p.y, p.z))
        elif topic in texts and StatusText is not None:
            m = deserialize_message(data, StatusText)
            fc_text.append((int(m.severity), str(m.text)))
    return images, info_of, np.array(imu), truth, np.array(fc_pose), fc_text


def gate_images(check, images: dict[str, list]) -> None:
    if not images:
        check("image_stamps", None, "skipped: no image streams recorded")
        check("fresh_frames", None, "skipped: no image streams recorded")
        return
    for topic, frames in sorted(images.items()):
        label = topic.strip("/").replace("/", ".")
        if len(frames) <= 20:
            check(f"image_stamps[{label}]", False, f"only {len(frames)} images")
            continue
        ts = np.array([t for t, _ in frames])
        dt = np.diff(ts)
        mono = bool((dt > 0).all())
        rate = 1 / dt.mean() if dt.mean() > 0 else 0.0
        jitter = float(dt.std() / dt.mean()) if dt.mean() > 0 else 9.0
        check(f"image_stamps[{label}]",
              mono and dt.max() < MAX_IMAGE_GAP_S and rate >= MIN_IMAGE_HZ,
              f"n={len(frames)} rate={rate:.1f} Hz jitter={100*jitter:.0f}% "
              f"max_gap={dt.max():.2f} s monotonic={mono}")
        repeated = sum(1 for (_, a), (_, b) in zip(frames, frames[1:]) if a == b)
        check(f"fresh_frames[{label}]", repeated / len(frames) < 0.02,
              f"repeated consecutive frames {repeated}/{len(frames)}")


def gate_stereo(check, images: dict[str, list]) -> None:
    """Were the cameras stamped together?

    Unpaired stereo is invisible in either stream on its own: both look healthy,
    and the estimator quietly gets two views of different moments.
    """
    if len(images) < 2:
        check("stereo_skew", None,
              f"skipped: {len(images)} camera stream(s), nothing to pair")
        return
    (left, lf), (right, rf) = sorted(images.items())[:2]
    if len(lf) < 20 or len(rf) < 20:
        check("stereo_skew", False, "too few frames to pair")
        return
    lt = np.array([t for t, _ in lf])
    rt = np.array([t for t, _ in rf])
    idx = np.searchsorted(rt, lt).clip(1, len(rt) - 1)
    nearest = np.minimum(np.abs(rt[idx] - lt), np.abs(rt[idx - 1] - lt))
    worst, median = float(nearest.max()), float(np.median(nearest))
    check("stereo_skew", worst < MAX_STEREO_SKEW_S,
          f"{left} vs {right}: median {1000*median:.1f} ms, worst {1000*worst:.1f} ms "
          f"({len(lf)} and {len(rf)} frames)")


def gate_truth(check, truth: list) -> None:
    if len(truth) <= 50:
        check("truth_valid", False, f"only {len(truth)} truth samples")
        return
    t = np.array([r[0] for r in truth])
    P = np.array([r[1:4] for r in truth])
    by: dict[float, set] = {}
    for r in truth:
        by.setdefault(r[0], set()).add(tuple(round(v, 4) for v in r[1:4]))
    ambiguous = sum(1 for v in by.values() if len(v) > 1) / len(by)
    step = np.linalg.norm(np.diff(P, axis=0), axis=1)
    dt = np.diff(t)
    speed = np.divide(step, dt, out=np.zeros_like(step), where=dt > 0)
    rate = 1 / np.median(dt) if np.median(dt) > 0 else 0.0
    jumps = int((speed > MAX_TRUTH_SPEED).sum())
    check("truth_valid",
          ambiguous < 0.01 and jumps == 0 and rate > MIN_TRUTH_HZ,
          f"rate={rate:.0f} Hz ambiguous={100*ambiguous:.1f}% "
          f"jumps(>{MAX_TRUTH_SPEED:.0f} m/s)={jumps} max_step={step.max():.2f} m "
          f"frames={truth[0][11]}->{truth[0][12]}")


def gate_truth_is_not_the_estimate(check, truth: list, fc_pose: np.ndarray) -> None:
    """Truth must be the simulator, not the flight controller's own estimate.

    For one bridge release ground truth was built from the flight controller's
    filter. The two agree to a few centimetres in nominal flight, so the gate is
    identity rather than closeness: interpolated onto the truth stamps, the two
    must differ by more than a couple of millimetres on most samples.
    """
    if fc_pose.size == 0 or len(truth) <= 50 or len(fc_pose) <= 50:
        check("truth_not_estimate", None,
              "skipped: no flight-controller odometry recorded")
        return
    t = np.array([r[0] for r in truth])
    P = np.array([r[1:4] for r in truth])
    inside = (t > fc_pose[0, 0]) & (t < fc_pose[-1, 0])
    if not inside.any():
        check("truth_not_estimate", None, "skipped: no overlapping samples")
        return
    interp = np.column_stack([np.interp(t[inside], fc_pose[:, 0], fc_pose[:, k])
                              for k in (1, 2, 3)])
    d = np.linalg.norm(P[inside] - interp, axis=1)
    same = float((d < 0.002).mean())
    check("truth_not_estimate", same < 0.5,
          f"samples within 2 mm of the flight controller's position: {100*same:.0f}%, "
          f"median separation {np.median(d):.3f} m")


def gate_fc_nominal(check, fc_text: list) -> None:
    """Was the flight itself nominal?

    A failsafe or an estimator reset means a score is measuring the failure
    rather than the estimator. Without the status stream recorded there is no
    way to tell, which is a skip, not a pass.
    """
    if not fc_text:
        check("fc_nominal", None, "skipped: no flight-controller status recorded")
        return
    bad = [text for _sev, text in fc_text if any(k in text for k in NOT_NOMINAL)]
    check("fc_nominal", not bad,
          f"{len(fc_text)} status messages" + (f"; first concerning: {bad[0]!r}" if bad else ""))


def gate_imu(check, imu: np.ndarray, truth: list) -> None:
    if imu.size == 0 or len(imu) <= 200:
        check("imu_gravity", False, f"only {len(imu)} inertial samples")
        check("imu_flu", None, "skipped: too few inertial samples")
        return
    rest = imu[imu[:, 0] < imu[0, 0] + 3.0]
    g = rest[:, 1:4].mean(0)
    span = imu[-1, 0] - imu[0, 0]
    check("imu_gravity",
          abs(g[2] - 9.81) < 0.3 and abs(g[0]) < 0.3 and abs(g[1]) < 0.3,
          f"rest accel mean=({g[0]:.2f},{g[1]:.2f},{g[2]:.2f}) "
          f"rate={len(imu)/span if span > 0 else 0:.0f} Hz")

    if len(truth) <= 50:
        check("imu_flu", None, "skipped: no truth to compare axes against")
        return
    t = np.array([r[0] for r in truth])
    W = np.array([r[8:11] for r in truth])
    gyro = np.column_stack([np.interp(t, imu[:, 0], imu[:, k]) for k in (4, 5, 6)])
    turning = np.linalg.norm(W, axis=1) > 0.05
    if turning.sum() <= 50:
        check("imu_flu", None, "skipped: too little rotation to test axes")
        return
    C = np.corrcoef(np.hstack([gyro[turning], W[turning]]).T)[:3, 3:]
    diag = np.diag(C)
    off = C - np.diag(diag)
    check("imu_flu", bool((diag > 0.8).all() and (np.abs(off) < 0.5).all()),
          "gyro against truth twist, correlation diag="
          + ",".join(f"{v:.2f}" for v in diag)
          + f" max_offdiag={np.abs(off).max():.2f}")


def gate_twist_frame(check, truth: list) -> None:
    """Odometry twist is body-frame, per REP 105."""
    if len(truth) <= 50:
        check("twist_body_frame", None, "skipped: too few truth samples")
        return
    t = np.array([r[0] for r in truth])
    P = np.array([r[1:4] for r in truth])
    yaw = np.array([r[4] for r in truth])
    V = np.array([r[5:8] for r in truth])
    k = max(1, int(0.1 * len(t) / max(t[-1] - t[0], 1e-6)))
    if len(t) <= 2 * k + 2:
        check("twist_body_frame", None, "skipped: recording too short")
        return
    world = (P[2 * k:] - P[:-2 * k]) / (t[2 * k:] - t[:-2 * k])[:, None]
    V2, y2 = V[k:-k], yaw[k:-k]
    speed = np.linalg.norm(world[:, :2], axis=1)
    moving = speed > 0.5
    if moving.sum() <= 20:
        check("twist_body_frame", None, "skipped: never exceeded 0.5 m/s")
        return
    c, s = np.cos(y2[moving]), np.sin(y2[moving])
    body = np.stack([c * world[moving, 0] + s * world[moving, 1],
                     -s * world[moving, 0] + c * world[moving, 1],
                     world[moving, 2]], 1)
    r_world = np.median(np.linalg.norm(V2[moving] - world[moving], axis=1) / speed[moving])
    r_body = np.median(np.linalg.norm(V2[moving] - body, axis=1) / speed[moving])
    check("twist_body_frame", r_body < r_world and r_body < 0.3,
          f"residual body={r_body:.2f} world={r_world:.2f} of speed")


def parse_chain(text: str) -> dict[str, dict]:
    """The camN blocks of a kalibr camera chain: intrinsics, resolution, topic.

    A deliberate re-implementation of check_calibration.parse_chain, which this
    cannot import: this file is piped into the bridge container on stdin, so it
    has no siblings there. Both are regex rather than YAML because a kalibr
    chain is OpenCV YAML (`%YAML:1.0`), which PyYAML will not load.
    """
    blocks = re.split(r"^cam(\d+):", "\n" + text, flags=re.MULTILINE)
    out: dict[str, dict] = {}
    for i in range(1, len(blocks) - 1, 2):
        body = blocks[i + 1]
        k = re.search(r"intrinsics:\s*\[([^\]]+)\]", body)
        res = re.search(r"resolution:\s*\[([^\]]+)\]", body)
        topic = re.search(r"rostopic:\s*(\S+)", body)
        if not (k and res):
            continue
        out[f"cam{blocks[i]}"] = {
            "intrinsics": [float(x) for x in k.group(1).split(",")],
            "resolution": [int(float(x)) for x in res.group(1).split(",")],
            "rostopic": topic.group(1) if topic else None,
        }
    return out


def info_topic_for(image_topic: str) -> str:
    """The camera_info topic paired with an image topic, by ROS convention:
    same namespace, last segment replaced."""
    return image_topic.rsplit("/", 1)[0] + "/camera_info"


def gate_intrinsics(check, info_of: dict[str, tuple], calib: str | None) -> None:
    """Every calibrated camera against the camera_info it actually published.

    Per camera, not per recording. The previous version compared the first
    camera_info in the bag against the first `intrinsics:` in the file, so a
    stereo rig was half checked, and it returned PASS when handed no
    calibration at all -- a pass that had compared nothing, which is worse than
    the skip it should have been.
    """
    if not info_of:
        check("intrinsics", None, "skipped: no camera_info recorded")
        return
    if not calib:
        seen = ", ".join(f"{t} {info_of[t][4]}x{info_of[t][5]}" for t in sorted(info_of))
        check("intrinsics", None, f"skipped: no calibration given ({seen})")
        return
    chain = parse_chain(Path(calib).read_text())
    if not chain:
        check("intrinsics", None, f"skipped: no camera blocks in {calib}")
        return
    for cam in sorted(chain):
        entry = chain[cam]
        name = f"intrinsics[{cam}]"
        topic = entry["rostopic"]
        if not topic:
            check(name, None, "skipped: no rostopic in the calibration")
            continue
        info = info_of.get(info_topic_for(topic))
        if info is None:
            # The calibration names a camera whose camera_info is not in the
            # bag. Either it was not recorded or -- the defect this catches --
            # the calibration names a topic nothing publishes, in which case
            # the estimator subscribed to nothing for this camera.
            check(name, False,
                  f"{topic}: no camera_info recorded for it "
                  f"(recorded: {', '.join(sorted(info_of)) or 'none'})")
            continue
        fx, fy, cx, cy, w, h = info
        cfx, cfy, ccx, ccy = entry["intrinsics"]
        cw, ch = entry["resolution"]
        ok = (abs(fx - cfx) / cfx < 0.01 and abs(fy - cfy) / cfy < 0.01
              and abs(cx - ccx) < 2 and abs(cy - ccy) < 2 and w == cw and h == ch)
        check(name, ok,
              f"{topic} K=({fx:.1f},{fy:.1f},{cx:.1f},{cy:.1f}) {w}x{h} "
              f"against calibration ({cfx},{cfy},{ccx},{ccy}) {cw}x{ch}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("bundle", help="the run directory holding the recording")
    ap.add_argument("--topics", help="role map YAML (default: <bundle>/topics.yaml)")
    ap.add_argument("--vehicle", default="", help="vehicle name, for {v} in the role map")
    ap.add_argument("--calib", help="kalibr camera chain, to check intrinsics against")
    ap.add_argument("--json", help="write results here (default: <bundle>/validation.json)")
    a = ap.parse_args(argv)

    bundle = Path(a.bundle)
    try:
        present = bag_topics(bundle)
    except (OSError, yaml.YAMLError) as exc:
        print(f"cannot read the recording: {exc}", file=sys.stderr)
        return 2
    if not present:
        print(f"no topics in {bundle}/metadata.yaml", file=sys.stderr)
        return 2

    role_file = Path(a.topics) if a.topics else bundle / "topics.yaml"
    raw_roles: dict[str, str] = {}
    if role_file.is_file():
        doc = yaml.safe_load(role_file.read_text()) or {}
        raw_roles = doc.get("sim") or doc
    # Ground truth and the inertial stream have a type each, so a stale role map
    # degrades to discovery rather than to an empty gate.
    by_type = {"ground_truth": ODOM_TYPE, "imu": IMU_TYPE, "estimate": ODOM_TYPE}
    roles, how = {}, {}
    for name in ("ground_truth", "estimate", "imu", "clock"):
        # Only ground truth may be discovered by type when two Odometry streams
        # exist; the estimate is whichever the campaign declared.
        fallback = by_type.get(name) if name != "estimate" else None
        roles[name], how[name] = resolve_role(
            str(raw_roles.get(name) or ""), present, a.vehicle, fallback)
    for name, note in how.items():
        if note.startswith("by type"):
            print(f"  NOTE  {name}: using {roles[name]} ({note})")

    try:
        images, info_of, imu, truth, fc_pose, fc_text = read_recording(bundle, roles, present)
    except Exception as exc:  # noqa: BLE001 - an unreadable bag is a result, not a crash
        print(f"cannot read the recording: {exc}", file=sys.stderr)
        return 2

    checks: dict[str, dict] = {}

    def check(name: str, ok: bool | None, detail: str) -> None:
        checks[name] = {"pass": None if ok is None else bool(ok), "detail": detail}
        mark = "SKIP" if ok is None else ("PASS" if ok else "FAIL")
        print(f"  {mark}  {name:28s} {detail}")

    gate_images(check, images)
    gate_stereo(check, images)
    gate_truth(check, truth)
    gate_truth_is_not_the_estimate(check, truth, fc_pose)
    gate_fc_nominal(check, fc_text)
    gate_imu(check, imu, truth)
    gate_twist_frame(check, truth)
    gate_intrinsics(check, info_of, a.calib)

    # Skipped is not passed, and it is not failed either. Only a real failure
    # invalidates a recording.
    failed = [name for name, c in checks.items() if c["pass"] is False]
    skipped = [name for name, c in checks.items() if c["pass"] is None]
    valid = not failed
    out = Path(a.json) if a.json else bundle / "validation.json"
    out.write_text(json.dumps({
        "recording": str(bundle),
        "valid": valid,
        "failed_checks": failed,
        "skipped_checks": skipped,
        "roles": roles,
        "role_resolution": how,
        "checks": checks,
    }, indent=1) + "\n")
    print(f"  {'VALID' if valid else 'INVALID'}  -> {out}"
          + (f"  ({len(skipped)} skipped)" if skipped else ""))
    return 0 if valid else 1


if __name__ == "__main__":
    sys.exit(main())
