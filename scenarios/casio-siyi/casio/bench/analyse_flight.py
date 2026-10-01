#!/usr/bin/env python3
"""Score one square-flight run against sim truth; write summary.md, summary.json and plots.

  analyse_flight.py RUN_DIR [--match-m 5]

Runs in the casio-node image (numpy + matplotlib). Reads what square_flight.py,
gt_objects.py and rc3_sink.py wrote into RUN_DIR. Truth is ENU metres from
home, the same frame as casio's map, so no alignment is fitted: an offset
between them is part of what is measured.
"""
import argparse
import csv
import glob
import json
import math
import os
from collections import Counter, defaultdict

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

EARTH_R = 6378137.0


def read(path):
    """Rows of a CSV, each message once. The recorder's DDS subscription can
    receive a message twice (stamps then differ only in the last printed
    digit), which would double every count."""
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        rows, seen = [], set()
        for r in csv.DictReader(f):
            key = tuple((k, f"{float(v):.4f}" if k == "t" else v) for k, v in r.items())
            if key not in seen:
                seen.add(key)
                rows.append(r)
        return rows


def fl(rows, *keys):
    return np.array([[float(r[k]) for k in keys] for r in rows]) if rows else np.zeros((0, len(keys)))


def pct(values, q):
    return float(np.percentile(values, q)) if len(values) else float("nan")


def short(name):
    return ("mannequin-" if "Mannequin" in name else "pawn-") + name.rsplit("_", 1)[-1][-4:]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("--match-m", type=float, default=5.0, help="horizontal radius that counts as found")
    a = ap.parse_args()
    run = a.run
    mission = json.load(open(f"{run}/mission.json")) if os.path.exists(f"{run}/mission.json") else {}
    events = read(f"{run}/events.csv")
    perturb = json.load(open(f"{run}/perturb.json")) if os.path.exists(f"{run}/perturb.json") else {}
    realism = os.path.exists(f"{run}/realism.json")

    # --- truth ------------------------------------------------------------
    gto = read(f"{run}/gt_objects.csv")
    tracks_gt = defaultdict(list)
    for r in gto:
        tracks_gt[r["name"]].append((float(r["wall"]), float(r["e"]), float(r["n"]), float(r["u"])))
    truth = {n: np.array(v) for n, v in tracks_gt.items()}
    moving = {n for n, v in truth.items() if np.ptp(v[:, 1:3], axis=0).max() > 1.0}
    static_pos = {n: np.median(v[:, 1:4], axis=0) for n, v in truth.items()}

    def truth_at(t):
        """Every truth object's ENU nearest in time to t (wall ~ sim clock here)."""
        out = {}
        for n, v in truth.items():
            if n in moving:
                out[n] = v[np.abs(v[:, 0] - t).argmin(), 1:4]
            else:
                out[n] = static_pos[n]
        return out

    def nearest(t, xyz):
        best = (None, float("inf"), float("nan"))
        for n, p in truth_at(t).items():
            dh = math.hypot(xyz[0] - p[0], xyz[1] - p[1])
            if dh < best[1]:
                best = (n, dh, xyz[2] - p[2])
        return best

    # --- flight -----------------------------------------------------------
    ekf = fl(read(f"{run}/ekf.csv"), "t", "x_e", "y_n", "z_u", "yaw")
    # Airborne from the first in_air to the first on_ground after the last
    # in_air (PX4 flickers takeoff/on_ground in the first second).
    in_air = [float(e["t"]) for e in events if e["kind"] == "landed_state" and e["value"] == "in_air"]
    landed = [float(e["t"]) for e in events if e["kind"] == "landed_state" and e["value"] == "on_ground"]
    t_end = ekf[-1, 0] if len(ekf) else 0.0
    t_off = min(in_air) if in_air else (ekf[0, 0] if len(ekf) else 0.0)
    t_land = min([t for t in landed if in_air and t > max(in_air)], default=t_end)
    # Seconds of take-off to in_air are part of the flight too.
    takeoff = [float(e["t"]) for e in events if e["kind"] == "landed_state" and e["value"] == "takeoff"]
    t_off = max([t for t in takeoff if t < t_off], default=t_off)
    fly = ekf[(ekf[:, 0] >= t_off) & (ekf[:, 0] <= t_land)] if len(ekf) else ekf
    path_m = float(np.sum(np.linalg.norm(np.diff(fly[:, 1:4], axis=0), axis=1))) if len(fly) > 1 else 0.0

    # EKF vs truth: gt_odom is on the same sim clock; its axis convention is
    # picked from the data (ENU as is, or NED swapped), start offset removed.
    gt = read(f"{run}/gt_odom.csv")
    pose_err = {}
    if gt and len(ekf):
        g = fl(gt, "t", "x", "y", "z")
        cands = {"enu": g[:, 1:4], "ned": np.c_[g[:, 2], g[:, 1], -g[:, 3]]}
        best = None
        for name, xyz in cands.items():
            xyz = xyz - xyz[0]
            e = np.c_[[np.interp(g[:, 0], ekf[:, 0], ekf[:, i]) for i in (1, 2, 3)]].T
            e = e - e[0]
            m = (g[:, 0] >= t_off) & (g[:, 0] <= t_land)
            if m.sum() < 2:
                continue
            d = e[m] - xyz[m]
            rms = float(np.sqrt(np.mean(np.sum(d ** 2, axis=1))))
            if best is None or rms < best[1]:
                best = (name, rms, d)
        if best:
            name, rms, d = best
            h = np.hypot(d[:, 0], d[:, 1])
            pose_err = {"gt_axes": name, "horizontal_median_m": pct(h, 50), "horizontal_p95_m": pct(h, 95),
                        "horizontal_max_m": float(h.max()), "vertical_median_m": pct(np.abs(d[:, 2]), 50)}

    # --- perception -------------------------------------------------------
    frames = fl(read(f"{run}/frames.csv"), "t", "n_detections")
    dets = read(f"{run}/detections.csv")
    in_flight = (frames[:, 0] >= t_off) & (frames[:, 0] <= t_land) if len(frames) else np.zeros(0, bool)
    fps = (len(frames) - 1) / (frames[-1, 0] - frames[0, 0]) if len(frames) > 1 else float("nan")
    det_h = np.array([float(d["h"]) for d in dets]) if dets else np.zeros(0)

    def score_ranges(rows):
        out = []
        for r in rows:
            t = float(r["t"])
            xyz = (float(r["x"]), float(r["y"]), float(r["z"]))
            veh = (float(r["veh_x"]), float(r["veh_y"]), float(r["veh_z"]))
            n, dh, dv = nearest(t, xyz)
            out.append({"t": t, "id": int(r["id"]), "xyz": xyz, "veh": veh, "truth": n, "dh": dh, "dv": dv,
                        "range_h": math.hypot(xyz[0] - veh[0], xyz[1] - veh[1]), "bbox_h": float(r["bbox_h"]),
                        "hold": r["hold"] == "True"})
        return out

    pos = score_ranges(read(f"{run}/positions.csv"))
    trk = score_ranges(read(f"{run}/tracked.csv"))
    fresh = [p for p in pos if not p["hold"]]
    dh = np.array([p["dh"] for p in fresh])

    by_track = defaultdict(list)
    for p in trk:
        by_track[p["id"]].append(p)
    track_rows = []
    for tid, ps in sorted(by_track.items()):
        last = ps[-1]
        med = np.median([q["xyz"] for q in ps], axis=0)
        n, d, dv = nearest(last["t"], med)
        track_rows.append({"id": tid, "updates": len(ps), "first_t": ps[0]["t"] - t_off, "last_t": last["t"] - t_off,
                           "median_xyz": med.tolist(), "truth": n, "dh": d, "dv": dv})
    found = {r["truth"] for r in track_rows if r["dh"] <= a.match_m}
    false_tracks = [r for r in track_rows if r["dh"] > 2 * a.match_m]

    # --- uplink -----------------------------------------------------------
    home = mission.get("home", {})
    gps = read(f"{run}/target_gps.csv")
    gps_err = []
    if home and gps:
        lat0, lon0 = home["lat"], home["lon"]
        for r in gps:
            e = math.radians(float(r["lon"]) - lon0) * EARTH_R * math.cos(math.radians(lat0))
            n_ = math.radians(float(r["lat"]) - lat0) * EARTH_R
            _, d, _ = nearest(float(r["t"]), (e, n_, 0.0))
            gps_err.append(d)
    posts = sorted(glob.glob(f"{run}/rc3/posts/*.json"))
    post_dets = [len(json.load(open(p)).get("metadata", {}).get("detections", [])) for p in posts]
    surv = read(f"{run}/surveillance.csv")
    valid = [r["valid"] == "True" for r in surv if r["kind"] == "valid"]

    summary = {
        "run": os.path.basename(os.path.abspath(run)),
        "mission": mission.get("args", {}),
        "perturbation": perturb,
        "camera_realism": realism,
        "flight": {"airborne_s": t_land - t_off, "path_m": path_m,
                   "max_alt_m": float(fly[:, 3].max()) if len(fly) else float("nan"),
                   "completed": any(e["kind"] == "flight_done" for e in events),
                   "errors": [e["kind"] + ": " + e["value"] for e in events if e["kind"] in ("error", "timeout", "collision")],
                   "failsafes": [e["value"] for e in events if e["kind"] == "px4" and "ailsafe" in e["value"]]},
        "pose_ekf_vs_truth": pose_err,
        "camera_fps": fps,
        "detections": {"frames": int(len(frames)), "frames_in_flight": int(in_flight.sum()),
                       "frames_with_detection_in_flight": int((frames[in_flight, 1] > 0).sum()) if len(frames) else 0,
                       "total": len(dets), "classes": dict(Counter(d["class"] for d in dets)),
                       "bbox_h_px_median": pct(det_h, 50), "bbox_h_px_p10": pct(det_h, 10), "bbox_h_px_p90": pct(det_h, 90)},
        "localiser": {"outputs": len(pos), "fresh": len(fresh), "held": len(pos) - len(fresh),
                      "horizontal_err_median_m": pct(dh, 50), "horizontal_err_p90_m": pct(dh, 90),
                      "within_match_m": float(np.mean(dh <= a.match_m)) if len(dh) else float("nan"),
                      "vertical_err_median_m": pct(np.array([p["dv"] for p in fresh]), 50)},
        "tracker": {"tracks": len(track_rows), "people_in_level": len(truth), "people_found": sorted(found),
                    "false_tracks": len(false_tracks), "tracks_detail": track_rows},
        "uplink": {"target_gps": len(gps), "gps_err_median_m": pct(np.array(gps_err), 50),
                   "gps_err_p90_m": pct(np.array(gps_err), 90), "rc3_posts": len(posts),
                   "rc3_detections": int(sum(post_dets))},
        "surveillance": {"samples": len(valid), "valid_share": float(np.mean(valid)) if valid else float("nan")},
    }
    with open(f"{run}/summary.json", "w") as f:
        json.dump(summary, f, indent=1, default=float)

    # --- plots ------------------------------------------------------------
    fig, ax = plt.subplots(figsize=(9, 9))
    if len(ekf):
        ax.plot(ekf[:, 1], ekf[:, 2], "-", color="0.3", lw=1, label="drone (EKF)")
    for n, v in truth.items():
        if n in moving:
            ax.plot(v[:, 1], v[:, 2], ":", color="tab:orange", lw=1)
            ax.plot(v[-1, 1], v[-1, 2], "s", color="tab:orange", ms=6)
        else:
            ax.plot(*static_pos[n][:2], "o", mfc="none", mec="tab:green", ms=11, mew=2)
    ax.plot([], [], "o", mfc="none", mec="tab:green", ms=11, mew=2, label="mannequin (truth)")
    ax.plot([], [], "s", color="tab:orange", label="moving pawn (truth)")
    if fresh:
        xy = np.array([p["xyz"][:2] for p in fresh])
        sc = ax.scatter(xy[:, 0], xy[:, 1], c=np.clip(dh, 0, 20), cmap="viridis_r", s=6, label="localiser output")
        fig.colorbar(sc, ax=ax, shrink=0.6, label="horizontal error to nearest truth (m)")
    for r in track_rows:
        ax.plot(*r["median_xyz"][:2], "x", color="tab:red", ms=10, mew=2)
        ax.annotate(str(r["id"]), r["median_xyz"][:2], color="tab:red", fontsize=8, xytext=(3, 3), textcoords="offset points")
    ax.plot([], [], "x", color="tab:red", ms=10, mew=2, label="track (median)")
    ax.set_aspect("equal")
    ax.set_xlabel("east (m)")
    ax.set_ylabel("north (m)")
    ax.grid(alpha=0.3)
    ax.legend(loc="upper right", fontsize=8)
    ax.set_title(f"{summary['run']}: top-down, map frame (home = 0, 0)")
    fig.tight_layout()
    fig.savefig(f"{run}/map.png", dpi=110)
    plt.close(fig)

    fig, axs = plt.subplots(3, 1, figsize=(10, 7), sharex=True)
    if len(ekf):
        axs[0].plot(ekf[:, 0] - t_off, ekf[:, 3])
    axs[0].set_ylabel("altitude (m)")
    if len(frames):
        axs[1].plot(frames[:, 0] - t_off, frames[:, 1], lw=0.7)
    axs[1].set_ylabel("detections / frame")
    if fresh:
        axs[2].plot([p["t"] - t_off for p in fresh], dh, ".", ms=3)
    axs[2].set_ylabel("localiser err (m)")
    axs[2].set_xlabel("seconds from take-off")
    for x in axs:
        x.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(f"{run}/timeline.png", dpi=110)
    plt.close(fig)

    # --- summary.md -------------------------------------------------------
    s, fmt = summary, (lambda v, d=1: "n/a" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v:.{d}f}")
    lines = [
        f"# {s['run']}",
        "",
        f"Mission: {json.dumps(s['mission'])}",
        "",
        f"Perturbation: {json.dumps(perturb) if perturb else 'none'} · camera realism: {'on' if realism else 'off'}",
        "",
        "| | |", "| --- | --- |",
        f"| airborne | {fmt(s['flight']['airborne_s'], 0)} s, {fmt(s['flight']['path_m'], 0)} m flown, max {fmt(s['flight']['max_alt_m'])} m |",
        f"| completed | {s['flight']['completed']} {'; '.join(s['flight']['errors'])} |",
        f"| PX4 failsafes | {len(s['flight']['failsafes'])} |",
        f"| EKF vs truth | horizontal median {fmt(pose_err.get('horizontal_median_m'), 2)} m, p95 {fmt(pose_err.get('horizontal_p95_m'), 2)} m, vertical median {fmt(pose_err.get('vertical_median_m'), 2)} m |",
        f"| camera | {fmt(s['camera_fps'])} fps |",
        f"| frames with a detection (in flight) | {s['detections']['frames_with_detection_in_flight']} / {s['detections']['frames_in_flight']} |",
        f"| detections | {s['detections']['total']} {s['detections']['classes']}; bbox height p10/median/p90 {fmt(s['detections']['bbox_h_px_p10'], 0)}/{fmt(s['detections']['bbox_h_px_median'], 0)}/{fmt(s['detections']['bbox_h_px_p90'], 0)} px |",
        f"| localiser | {s['localiser']['fresh']} fresh + {s['localiser']['held']} held; horizontal error median {fmt(s['localiser']['horizontal_err_median_m'])} m, p90 {fmt(s['localiser']['horizontal_err_p90_m'])} m; {fmt(100 * s['localiser']['within_match_m'], 0)} % within {a.match_m:g} m |",
        f"| tracker | {s['tracker']['tracks']} tracks; {len(found)} of {len(truth)} truth objects found within {a.match_m:g} m; {len(false_tracks)} tracks > {2 * a.match_m:g} m from any |",
        f"| /target/gps | {s['uplink']['target_gps']} fixes; error median {fmt(s['uplink']['gps_err_median_m'])} m, p90 {fmt(s['uplink']['gps_err_p90_m'])} m |",
        f"| RC3 posts | {s['uplink']['rc3_posts']} posts, {s['uplink']['rc3_detections']} detections |",
        f"| surveillance goal valid | {fmt(100 * s['surveillance']['valid_share'], 0)} % of {s['surveillance']['samples']} samples |",
        "",
        "## Tracks", "",
        "| id | updates | seen (s after take-off) | nearest truth | horizontal err (m) | vertical err (m) |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in track_rows:
        lines.append(f"| {r['id']} | {r['updates']} | {fmt(r['first_t'], 0)}-{fmt(r['last_t'], 0)} | {short(r['truth']) if r['truth'] else '-'} | {fmt(r['dh'])} | {fmt(r['dv'])} |")
    lines += ["", "![map](map.png)", "", "![timeline](timeline.png)", ""]
    with open(f"{run}/summary.md", "w") as f:
        f.write("\n".join(lines))
    print("\n".join(lines[:18]))


if __name__ == "__main__":
    main()
