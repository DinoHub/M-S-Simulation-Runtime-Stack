"""Per-run stats over the flight window only.

Flight window = first to last ground-truth sample more than 0.5 m from the
start position (receive time, the recorder's wall clock). Inside it:
per-camera delivered Hz (camera_info count / window), RTF (/clock sim time
over wall time), and path length flown.

    python3 flight_stats.py <runs-dir>  > stats.json
"""
import json
import math
import sys
from pathlib import Path

import rosbag2_py
from nav_msgs.msg import Odometry
from rclpy.serialization import deserialize_message
from rosgraph_msgs.msg import Clock

rows = []
for bag in sorted(Path(sys.argv[1]).glob("*/bag")):
    r = rosbag2_py.SequentialReader()
    r.open(rosbag2_py.StorageOptions(uri=str(bag), storage_id="mcap"), rosbag2_py.ConverterOptions("cdr", "cdr"))
    types = {t.name: t.type for t in r.get_all_topics_and_types()}
    gt, clock, cams = [], [], {}
    while r.has_next():
        topic, data, t_ns = r.read_next()
        t = t_ns / 1e9
        if topic == "/ground_truth/odom":
            p = deserialize_message(data, Odometry).pose.pose.position
            gt.append((t, p.x, p.y, p.z))
        elif topic == "/clock":
            c = deserialize_message(data, Clock).clock
            clock.append((t, c.sec + c.nanosec * 1e-9))
        elif types.get(topic) == "sensor_msgs/msg/CameraInfo":
            cams.setdefault(topic, []).append(t)
    if not gt:
        rows.append({"run": bag.parent.name, "error": "no ground truth"}); continue
    x0, y0, z0 = gt[0][1:]
    away = [g for g in gt if math.dist(g[1:], (x0, y0, z0)) > 0.5]
    if not away:
        rows.append({"run": bag.parent.name, "error": "never left the start"}); continue
    w0, w1 = away[0][0], away[-1][0]
    inside = [g for g in gt if w0 <= g[0] <= w1]
    path = sum(math.dist(a[1:], b[1:]) for a, b in zip(inside, inside[1:]))
    cw = [c for c in clock if w0 <= c[0] <= w1]
    rtf = (cw[-1][1] - cw[0][1]) / (cw[-1][0] - cw[0][0]) if len(cw) > 1 else None
    hz = {k.split("/")[1]: round(sum(w0 <= t <= w1 for t in v) / (w1 - w0), 2) for k, v in sorted(cams.items())}
    rows.append({"run": bag.parent.name, "flight_s": round(w1 - w0, 1), "path_m": round(path, 1),
                 "max_alt_m": round(max(g[3] for g in inside) - z0, 2),
                 "rtf": round(rtf, 3) if rtf else None, "camera_hz": hz, "window": [w0, w1]})
json.dump(rows, sys.stdout, indent=2)
