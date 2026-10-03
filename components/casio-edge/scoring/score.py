#!/usr/bin/env python3
"""casio-edge's scorer: the platform runs it once after the stack stops.

Runs in the casio-node image (rclpy, rosbag2_py, casio_interfaces, numpy,
matplotlib). It rebuilds, from the run's bag, the CSVs the square-flight bench
records live (bench/square_flight.py), adds the level's people as the truth
sidecar logged them (/score/component/gt_objects.csv, bench/gt_objects.py),
runs the bench's own analyser (bench/analyse_flight.py) on them, and turns
its summary into the platform's result (mns.component.result.v1 at
$MNS_RESULT). Truth and casio's map share a frame: ENU metres from where the
drone rested before take-off, which is PX4's home.

Without the bag there is nothing to score (the platform marks the run
skipped before this starts). Without the truth file the detector and the
uplink are still measured; localisation and tracking are not.
"""
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile

LANDED = {0: "undefined", 1: "on_ground", 2: "in_air", 3: "takeoff", 4: "landing"}
TOPICS = {
    "/mavros/state": "state", "/mavros/extended_state": "ext", "/mavros/statustext/recv": "text",
    "/mavros/global_position/global": "fix", "/mavros/global_position/local": "ekf",
    "/ground_truth/odom": "gt", "/camera/camera_info": "cam", "/detections": "dets",
    "/objects/position": "pos", "/objects/tracked": "trk", "/target/gps": "gps",
    "/surveillance/goal_valid": "valid", "/surveillance/goal": "goal",
}
HEADERS = {
    "events": ["t", "kind", "value"], "ekf": ["t", "x_e", "y_n", "z_u", "yaw", "vx", "vy", "vz"],
    "gt_odom": ["t", "frame", "child", "x", "y", "z", "yaw"], "frames": ["t", "n_detections"],
    "detections": ["t", "class", "score", "cx", "cy", "w", "h"],
    "positions": ["t", "frame", "id", "class", "x", "y", "z", "var_x", "var_y", "var_z", "bbox_h", "conf",
                  "hold", "veh_x", "veh_y", "veh_z"],
    "tracked": ["t", "frame", "id", "class", "x", "y", "z", "var_x", "var_y", "var_z", "bbox_h", "conf",
                "hold", "veh_x", "veh_y", "veh_z"],
    "target_gps": ["t", "track_id", "class", "lat", "lon", "alt", "distance_m", "conf", "held"],
    "surveillance": ["t", "kind", "valid", "x", "y", "z"], "camera": ["t", "fx", "fy", "cx", "cy"],
}


def stamp(header, fallback):
    t = header.stamp.sec + header.stamp.nanosec * 1e-9
    return t if t > 0 else fallback


def yaw_of(q):
    return math.atan2(2 * (q.w * q.z + q.x * q.y), 1 - 2 * (q.y * q.y + q.z * q.z))


def bag_to_run(bag, out):
    """The bench recorder's CSVs, from the bag (bench/square_flight.py's rows)."""
    import csv

    import rosbag2_py
    from rclpy.serialization import deserialize_message
    from rosidl_runtime_py.utilities import get_message

    reader = rosbag2_py.SequentialReader()
    reader.open(rosbag2_py.StorageOptions(uri=bag, storage_id=""),
                rosbag2_py.ConverterOptions("cdr", "cdr"))
    types = {t.name: t.type for t in reader.get_all_topics_and_types()}
    reader.set_filter(rosbag2_py.StorageFilter(topics=[t for t in TOPICS if t in types]))
    files = {k: open(f"{out}/{k}.csv", "w", newline="") for k in HEADERS}
    w = {k: csv.writer(f) for k, f in files.items()}
    for k, h in HEADERS.items():
        w[k].writerow(h)

    def row(k, *values):
        w[k].writerow([f"{v:.6f}" if isinstance(v, float) else v for v in values])

    msg_types = {t: get_message(types[t]) for t in TOPICS if t in types}
    state = ext = None
    home = None
    last = {"ekf": -1.0, "gt": -1.0}
    cam_seen = 0
    while reader.has_next():
        topic, raw, recv_ns = reader.read_next()
        m = deserialize_message(raw, msg_types[topic])
        recv = recv_ns * 1e-9
        kind = TOPICS[topic]
        if kind == "state":
            key = (m.mode, m.armed, m.connected)
            if state != key:
                row("events", stamp(m.header, recv), "state", f"mode={m.mode} armed={m.armed} connected={m.connected}")
            state = key
        elif kind == "ext":
            if ext != m.landed_state:
                row("events", stamp(m.header, recv), "landed_state", LANDED.get(m.landed_state, m.landed_state))
            ext = m.landed_state
        elif kind == "text":
            row("events", stamp(m.header, recv), "px4", m.text)
        elif kind == "fix":
            # Home: the last fix before the first take-off, where the drone rested.
            if ext in (None, 0, 1) and m.status.status >= 0:
                home = {"lat": m.latitude, "lon": m.longitude, "alt": m.altitude}
        elif kind in ("ekf", "gt"):
            t = stamp(m.header, recv)
            if t - last[kind] < 0.1:
                continue
            last[kind] = t
            p, q = m.pose.pose.position, m.pose.pose.orientation
            if kind == "ekf":
                v = m.twist.twist.linear
                row("ekf", t, p.x, p.y, p.z, yaw_of(q), v.x, v.y, v.z)
            else:
                row("gt_odom", t, m.header.frame_id, m.child_frame_id, p.x, p.y, p.z, yaw_of(q))
        elif kind == "cam":
            cam_seen += 1
            if cam_seen % 20 == 1:
                row("camera", stamp(m.header, recv), m.k[0], m.k[4], m.k[2], m.k[5])
        elif kind == "dets":
            t = stamp(m.header, recv)
            row("frames", t, len(m.detections))
            for d in m.detections:
                cls, score = ("", 0.0)
                if d.results:
                    cls, score = d.results[0].hypothesis.class_id, d.results[0].hypothesis.score
                c = d.bbox.center
                cx, cy = (c.position.x, c.position.y) if hasattr(c, "position") else (c.x, c.y)
                row("detections", t, cls, score, cx, cy, d.bbox.size_x, d.bbox.size_y)
        elif kind in ("pos", "trk"):
            p, c, vp = m.pose.pose.position, m.pose.covariance, m.vehicle_position
            row("positions" if kind == "pos" else "tracked", stamp(m.header, recv), m.header.frame_id,
                m.detection_id, m.class_id, p.x, p.y, p.z, c[0], c[7], c[14], m.bbox[3], m.confidence,
                m.hold_position, vp.x, vp.y, vp.z)
        elif kind == "gps":
            row("target_gps", stamp(m.header, recv), m.track_id, m.class_id, m.latitude, m.longitude,
                m.altitude, m.distance_m, m.confidence, m.position_held)
        elif kind == "valid":
            row("surveillance", recv, "valid", m.data, "", "", "")
        elif kind == "goal":
            p = m.pose.position
            row("surveillance", stamp(m.header, recv), "goal", "", p.x, p.y, p.z)
    for f in files.values():
        f.close()
    with open(f"{out}/mission.json", "w") as f:
        json.dump({"args": {}, "home": home or {}}, f)
    return sorted(types)


def num(v):
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else round(float(v), 4)


def metric(value, unit, label, better):
    return {"value": num(value) if not isinstance(value, int) or isinstance(value, bool) else value,
            "unit": unit, "label": label, "better": better}


def main():
    result_path = os.environ["MNS_RESULT"]
    bag = os.environ.get("MNS_BAG")
    options = json.loads(os.environ.get("MNS_SCORING_OPTIONS") or "{}")
    if not bag:
        json.dump({"status": "skipped", "reason": "the run was not recorded", "metrics": {}},
                  open(result_path, "w"))
        return 0
    work = tempfile.mkdtemp(prefix="casio-score-")
    topics = bag_to_run(bag, work)
    truth_dir = os.environ.get("MNS_COMPONENT_OUT", "")
    have_truth = False
    for name in ("gt_objects.csv", "gt_vehicle.csv"):
        src = os.path.join(truth_dir, name)
        if truth_dir and os.path.isfile(src):
            shutil.copyfile(src, os.path.join(work, name))
            have_truth = have_truth or name == "gt_objects.csv"
    match_m = float(options.get("match_m", 5.0))
    here = os.path.dirname(os.path.abspath(__file__))
    analyser = os.environ.get("CASIO_ANALYSER", os.path.join(here, "analyse_flight"))
    proc = subprocess.run([sys.executable, analyser, work, "--match-m", str(match_m)],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        print(proc.stdout[-2000:], proc.stderr[-2000:], file=sys.stderr)
        raise SystemExit(f"analyse_flight exited {proc.returncode}")
    s = json.load(open(f"{work}/summary.json"))
    det, loc, trk, up, surv = s["detections"], s["localiser"], s["tracker"], s["uplink"], s["surveillance"]
    in_flight = det["frames_in_flight"]
    people = trk["people_in_level"]
    m = {
        "camera_fps": metric(s["camera_fps"], "Hz", "Camera frames reaching the detector", "higher"),
        "detection_frame_share": metric(det["frames_with_detection_in_flight"] / in_flight if in_flight else None,
                                        "ratio", "Frames with a detection, in flight", "higher"),
        "detections": metric(det["total"], "count", "Detections", "higher"),
        "target_gps_fixes": metric(up["target_gps"], "count", "Target fixes sent (/target/gps)", "higher"),
        "surveillance_valid_share": metric(surv["valid_share"], "ratio", "Surveillance goal valid", "higher"),
    }
    if have_truth and people:
        m.update({
            "localiser_err_median_m": metric(loc["horizontal_err_median_m"], "m",
                                             "Localiser horizontal error, median", "lower"),
            "localiser_err_p90_m": metric(loc["horizontal_err_p90_m"], "m", "Localiser horizontal error, p90",
                                          "lower"),
            "localiser_within_match": metric(loc["within_match_m"], "ratio",
                                             f"Localiser outputs within {match_m:g} m of a person", "higher"),
            "people_recall": metric(len(trk["people_found"]) / people, "ratio",
                                    f"People found by a track within {match_m:g} m", "higher"),
            "false_tracks": metric(trk["false_tracks"], "count",
                                   f"Tracks more than {2 * match_m:g} m from any person", "lower"),
            "gps_err_median_m": metric(up["gps_err_median_m"], "m", "Target fix error, median", "lower"),
            "gps_err_p90_m": metric(up["gps_err_p90_m"], "m", "Target fix error, p90", "lower"),
        })
    detail = {k: s[k] for k in ("flight", "pose_ekf_vs_truth", "detections", "localiser", "uplink",
                                 "surveillance")}
    detail["tracker"] = {k: v for k, v in trk.items() if k != "tracks_detail"}
    detail["truth"] = "sidecar gt_objects.csv" if have_truth else "none: localisation and tracking not scored"
    detail["topics_in_bag"] = topics
    json.dump({"status": "ok", "metrics": m, "detail": detail}, open(result_path, "w"), indent=1,
              default=float)
    shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
