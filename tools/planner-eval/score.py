#!/usr/bin/env python3
"""Score local-planner runs from their bags: one JSON per run, one table.

A planner course (CampaignSpec mission.goals) records the goals the pilot
published (/goal), sim truth (/ground_truth/odom) and, in the first run, the
registered cloud. Per goal (a leg starts when a new goal appears):

  reached        sim truth came within the tolerance before the next goal
  time_s         from the goal's first message to reaching it
  path_m         distance flown during the leg; straight_m the straight line
  efficiency     straight_m / path_m (1 = no detour)
  clearance_m    closest approach to an obstacle point higher than 0.5 m above
                 the local ground (the static yard map, below)
  speed          mean and max ground speed; jerk_rms from truth velocity

The yard is static, so obstacles come from a map: every cloud point of the
reference bag (the one run that kept its clouds), voxelised. Later runs drop
their clouds (recording.keep: first) and are scored against the same map.

    tools/planner-eval/score.py --map <bag with clouds> <bag> [<bag> ...] --out results.json
"""
from __future__ import annotations

import argparse
import json
import math
import statistics as st
import sys
from pathlib import Path

import numpy as np
from rosbags.rosbag2 import Reader
from rosbags.typesys import Stores, get_typestore
from scipy.spatial import cKDTree

TS = get_typestore(Stores.ROS2_HUMBLE)
CLOUD_TOPICS = ("/Copter1/registered_point_cloud", "/registered_point_cloud")


def read_bag(bag: Path, with_clouds: bool = False):
    odom, goals, clouds = [], [], []
    reader = Reader(bag)
    reader.open()
    wanted = {"/ground_truth/odom", "/goal", *(CLOUD_TOPICS if with_clouds else ())}
    for conn, _, raw in reader.messages([c for c in reader.connections if c.topic in wanted]):
        msg = TS.deserialize_cdr(raw, conn.msgtype)
        t = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
        if conn.topic == "/ground_truth/odom":
            p, v = msg.pose.pose.position, msg.twist.twist.linear
            odom.append((t, p.x, p.y, p.z, v.x, v.y, v.z))
        elif conn.topic == "/goal":
            p = msg.pose.position
            goals.append((t, p.x, p.y, p.z))
        else:
            n = msg.width * msg.height
            if not n:
                continue
            fields = {f.name: f.offset for f in msg.fields}
            buf = np.frombuffer(bytes(msg.data), dtype=np.uint8).reshape(n, msg.point_step)
            xyz = np.stack([buf[:, fields[a]:fields[a] + 4].copy().view(np.float32)[:, 0]
                            for a in ("x", "y", "z")], axis=1)
            clouds.append(xyz[np.isfinite(xyz).all(axis=1)])
    reader.close()
    return np.array(odom), np.array(goals), clouds


def build_map(bag: Path, voxel: float = 0.2):
    _, _, clouds = read_bag(bag, with_clouds=True)
    if not clouds:
        raise SystemExit(f"{bag}: no registered clouds recorded; pass the run that kept them")
    pts = np.unique(np.round(np.vstack(clouds) / voxel).astype(np.int64), axis=0) * voxel
    # Ground: the lowest points of each 2 m column; obstacles stand 0.5 m above it.
    cols = np.floor(pts[:, :2] / 2.0).astype(np.int64)
    keys, inverse = np.unique(cols, axis=0, return_inverse=True)
    ground = np.full(len(keys), np.inf)
    np.minimum.at(ground, inverse, pts[:, 2])
    obstacles = pts[pts[:, 2] > ground[inverse] + 0.5]
    return cKDTree(obstacles), len(obstacles)


def legs(goals: np.ndarray) -> list[tuple[float, np.ndarray]]:
    """(start time, goal) for each distinct goal, in order."""
    out: list[tuple[float, np.ndarray]] = []
    for t, *g in goals:
        g = np.array(g)
        if not out or np.linalg.norm(out[-1][1] - g) > 0.05:
            out.append((t, g))
    return out


def score_run(bag: Path, tree, tolerance: float) -> dict:
    odom, goals, _ = read_bag(bag)
    if not len(odom) or not len(goals):
        return {"bag": str(bag), "error": "no /ground_truth/odom or /goal in the bag"}
    t, pos, vel = odom[:, 0], odom[:, 1:4], odom[:, 4:7]
    clearance = tree.query(pos)[0] if tree is not None else np.full(len(t), np.nan)
    speed = np.linalg.norm(vel[:, :2], axis=1)
    out_legs = []
    plan = legs(goals)
    for i, (t0, goal) in enumerate(plan):
        t1 = plan[i + 1][0] if i + 1 < len(plan) else t[-1]
        m = (t >= t0) & (t <= t1)
        if m.sum() < 2:
            continue
        p, d = pos[m], np.linalg.norm(pos[m] - goal, axis=1)
        hit = np.nonzero(d <= tolerance)[0]
        end = hit[0] + 1 if len(hit) else len(p)
        flown = float(np.linalg.norm(np.diff(p[:end], axis=0), axis=1).sum())
        straight = float(np.linalg.norm(goal - p[0]))
        tm, vm = t[m][:end], vel[m][:end]
        jerk = (np.diff(vm, n=2, axis=0) / np.diff(tm)[1:, None] ** 2) if len(tm) > 3 else np.zeros((1, 3))
        out_legs.append({
            "goal": [round(float(v), 2) for v in goal],
            "reached": bool(len(hit)),
            "time_s": round(float(tm[-1] - t0), 2) if len(hit) else None,
            "closest_m": round(float(d.min()), 2),
            "path_m": round(flown, 2),
            "straight_m": round(straight, 2),
            "efficiency": round(straight / flown, 3) if len(hit) and flown > 0 else None,
            "clearance_min_m": round(float(np.nanmin(clearance[m][:end])), 2),
            "speed_mean": round(float(speed[m][:end].mean()), 2),
            "speed_max": round(float(speed[m][:end].max()), 2),
            "jerk_rms": round(float(np.sqrt((jerk ** 2).sum(axis=1).mean())), 2),
        })
    reached = [g for g in out_legs if g["reached"]]
    return {
        "bag": str(bag),
        "goals": len(out_legs),
        "reached": len(reached),
        "time_to_all_s": round(sum(g["time_s"] for g in reached), 1) if len(reached) == len(out_legs) else None,
        "clearance_min_m": min((g["clearance_min_m"] for g in out_legs), default=None),
        "contact": any(g["clearance_min_m"] < 0.3 for g in out_legs),
        "legs": out_legs,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("bags", nargs="+", type=Path)
    ap.add_argument("--map", type=Path, help="the run that kept its registered clouds")
    ap.add_argument("--tolerance", type=float, default=1.0)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    tree, n = build_map(args.map) if args.map else (None, 0)
    runs = [score_run(b, tree, args.tolerance) for b in args.bags]
    result = {"tolerance_m": args.tolerance, "map": str(args.map) if args.map else None,
              "map_obstacle_points": n, "runs": runs}
    if args.out:
        args.out.write_text(json.dumps(result, indent=2) + "\n")
    for r in runs:
        print(f"{Path(r['bag']).parent.name:>12}: {r.get('reached')}/{r.get('goals')} goals, "
              f"all in {r.get('time_to_all_s')} s, min clearance {r.get('clearance_min_m')} m")
        for i, g in enumerate(r.get("legs", [])):
            print(f"    goal {i} {g['goal']}: reached={g['reached']} t={g['time_s']} closest={g['closest_m']} "
                  f"path={g['path_m']}/{g['straight_m']} eff={g['efficiency']} clear={g['clearance_min_m']} "
                  f"v={g['speed_mean']}/{g['speed_max']} jerk={g['jerk_rms']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
