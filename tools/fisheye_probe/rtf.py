"""Real-time factor and per-camera rates from OSMO run bags.

The recorder writes each message with its own wall clock (no use_sim_time), and
/clock carries sim time, so:

  RTF        = d(sim time on /clock) / d(wall receive time)   over the recording
  wall Hz    = messages / wall seconds   (what a consumer sees per real second)
  sim Hz     = messages / sim seconds    (what the estimator sees per sim second)

Usage: python3 rtf.py <runs-dir>... > rtf.json
"""
import json
import statistics
import sys
from pathlib import Path

import rosbag2_py
from rclpy.serialization import deserialize_message
from rosgraph_msgs.msg import Clock

WINDOW_S = 5.0


def read(bag: Path):
    reader = rosbag2_py.SequentialReader()
    reader.open(rosbag2_py.StorageOptions(uri=str(bag), storage_id="mcap"),
                rosbag2_py.ConverterOptions("cdr", "cdr"))
    types = {t.name: t.type for t in reader.get_all_topics_and_types()}
    clock, counts = [], {}
    while reader.has_next():
        topic, data, wall_ns = reader.read_next()
        counts[topic] = counts.get(topic, 0) + 1
        if topic == "/clock":
            c = deserialize_message(data, Clock).clock
            clock.append((wall_ns / 1e9, c.sec + c.nanosec / 1e9))
    return types, clock, counts


def windows(clock):
    """RTF over consecutive WINDOW_S wall-second windows."""
    out, i = [], 0
    for j in range(len(clock)):
        if clock[j][0] - clock[i][0] >= WINDOW_S:
            dw = clock[j][0] - clock[i][0]
            out.append((clock[j][1] - clock[i][1]) / dw)
            i = j
    return out


def main():
    rows = []
    for runs_dir in sys.argv[1:]:
        for bag in sorted(Path(runs_dir).glob("*/bag")):
            run = bag.parent
            types, clock, counts = read(bag)
            if len(clock) < 2:
                rows.append({"run": str(run), "error": "no /clock"})
                continue
            wall = clock[-1][0] - clock[0][0]
            sim = clock[-1][1] - clock[0][1]
            w = windows(clock)
            cams = {t: {"wall_hz": round(n / wall, 2), "sim_hz": round(n / sim, 2) if sim > 0 else None}
                    for t, n in counts.items() if types.get(t) == "sensor_msgs/msg/CameraInfo"}
            imu = counts.get("/imu/data", 0)
            rows.append({
                "run": str(run),
                "wall_s": round(wall, 1), "sim_s": round(sim, 1),
                "rtf": round(sim / wall, 3) if wall > 0 else None,
                "rtf_window_median": round(statistics.median(w), 3) if w else None,
                "rtf_window_min": round(min(w), 3) if w else None,
                "rtf_window_max": round(max(w), 3) if w else None,
                "clock_wall_hz": round(len(clock) / wall, 1),
                "imu_sim_hz": round(imu / sim, 1) if sim > 0 else None,
                "cameras": cams,
            })
    json.dump(rows, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
