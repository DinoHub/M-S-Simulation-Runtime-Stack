"""Report whether the vehicle stayed in the world.

Free fall through a level is the specific failure this catches:
ground truth descending steadily with no horizontal motion, forever,
because there is no floor under the spawn point. It is reported, not
judged -- the aggregate decides what a run with a bad spawn is worth.
"""
import json
import os
import sys

import rosbag2_py
from rclpy.serialization import deserialize_message
from nav_msgs.msg import Odometry

def main(bag_path, out_path):
    reader = rosbag2_py.SequentialReader()
    reader.open(
        rosbag2_py.StorageOptions(uri=bag_path, storage_id="mcap"),
        rosbag2_py.ConverterOptions("cdr", "cdr"))

    samples = []
    while reader.has_next():
        topic, data, stamp = reader.read_next()
        if topic == "/ground_truth/odom":
            pos = deserialize_message(data, Odometry).pose.pose.position
            samples.append((stamp / 1e9, pos.x, pos.y, pos.z))

    if len(samples) < 2:
        report = {"spawn_ok": False,
                  "reason": "no ground truth in the bag",
                  "samples": len(samples)}
    else:
        span = samples[-1][0] - samples[0][0]
        drop = samples[0][3] - samples[-1][3]
        fall_rate = drop / span if span > 0 else 0.0
        horizontal = max(
            abs(s[1] - samples[0][1]) + abs(s[2] - samples[0][2])
            for s in samples)
        limit = float(os.environ.get("MAX_FALL_MS", 0.5))
        # Two ways to leave the world. Free fall from spawn: steady
        # descent with nothing moving horizontally. And sinking after
        # a flight: the vehicle flew, landed where the level has no
        # floor, and ended far below where it started -- PX4 keeps
        # commanding its land speed into nothing. Horizontal travel
        # does not excuse the second, which is why it is a separate
        # test.
        falling = fall_rate > limit and horizontal < 1.0
        sank = (samples[0][3] - samples[-1][3]) > 5.0 and not falling
        # A third: fell through and was put back. On a World
        # Partition level the terrain streams in around the vehicle
        # after it spawns; if physics starts first the vehicle drops
        # through where the ground will be, and something returns
        # it within seconds. First and last samples then agree, so
        # the two tests above pass -- but the flight controller's
        # EKF has just seen a 47 m round trip (XFS run 41: PX4's
        # local origin read 36 m before takeoff) and that run's VIO
        # scored 10.4 m against 1.0-1.5 m for the runs without it.
        # Judged against where the vehicle rests, since the bag can
        # begin mid-drop.
        zs = sorted(s[3] for s in samples)
        resting = zs[len(zs) // 2]
        dropped = (resting - zs[0]) > 5.0 and not falling and not sank
        ok = not falling and not sank and not dropped
        report = {
            "spawn_ok": ok,
            "reason": ("free fall: descending %.2f m/s with %.2f m of "
                       "horizontal motion" % (fall_rate, horizontal)
                       if falling else
                       "sank %.1f m below its start after flying %.1f m: "
                       "landed where the level has no floor"
                       % (samples[0][3] - samples[-1][3], horizontal)
                       if sank else
                       "fell %.1f m below its resting height and was put "
                       "back: the terrain was not there yet when physics "
                       "started" % (resting - zs[0])
                       if dropped else "vehicle stayed in the world"),
            "z_first_m": round(samples[0][3], 3),
            "z_last_m": round(samples[-1][3], 3),
            "z_min_m": round(min(s[3] for s in samples), 3),
            "fall_rate_ms": round(fall_rate, 3),
            "horizontal_travel_m": round(horizontal, 3),
            "duration_s": round(span, 2),
            "samples": len(samples),
        }

    with open(out_path, "w") as handle:
        json.dump({"spawn": report}, handle, indent=2)
    print(json.dumps(report, indent=2))
    # Reports; does not veto. The aggregate decides.
    return 0

sys.exit(main(sys.argv[1], sys.argv[2]))
