#!/usr/bin/env python3
"""Write a ScenarioSpec with N copies of a scenario's first vehicle.

    make_spec.py <base ScenarioSpec.yaml> <n> --autopilot px4|ardupilot
                 [--no-mavros] [--spacing 4] [--columns 8] [--name NAME] [--out DIR]

The copies sit on a grid (spacing metres apart, columns per row) from the
base vehicle's start. Vehicle k is drone_k with runtime name Copterk on ROS
domain k. Random spawns and the measuring block are dropped and
QGroundControl is off, so the stack carries only what scales with the
vehicle count. --no-mavros turns the bridges' MAVROS off: the dashboard
flies vehicles over its own MAVLink link, and MAVROS is most of each
vehicle's CPU. The result is written to <out>/<name>/ScenarioSpec.yaml;
import it in the dashboard's Author step (or POST /api/scenario/import).
"""
import argparse
import copy
from pathlib import Path

import yaml

PROFILES = {"px4": "airsim_unreal_px4_docker", "ardupilot": "airsim_unreal_ardupilot_docker"}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("base", type=Path)
    ap.add_argument("n", type=int)
    ap.add_argument("--autopilot", choices=sorted(PROFILES), required=True)
    ap.add_argument("--no-mavros", action="store_true")
    ap.add_argument("--spacing", type=float, default=4.0)
    ap.add_argument("--columns", type=int, default=8)
    ap.add_argument("--name")
    ap.add_argument("--out", type=Path, default=Path("scenarios"))
    args = ap.parse_args()
    if not 1 <= args.n <= 232:
        ap.error("n must be 1..232 (one ROS domain per vehicle)")

    spec = yaml.safe_load(args.base.read_text())
    name = args.name or f"{'px4' if args.autopilot == 'px4' else 'ardu'}-swarm-{args.n}" + \
        ("-lean" if args.no_mavros else "")
    spec["id"] = spec["name"] = name
    spec["random_spawns"] = []
    (spec.get("extensions") or {}).pop("mns.metrics", None)

    runtime = spec.setdefault("runtime", {})
    runtime["profile"] = PROFILES[args.autopilot]
    if args.autopilot == "px4":
        runtime["autopilot"] = {"type": "px4", "managed": True, "endpoint": "docker",
                                "hostname_prefix": "px4-drone"}
    else:
        runtime.pop("autopilot", None)
    features = runtime.setdefault("features", {})
    features["qgroundcontrol"] = False
    features["mavros"] = not args.no_mavros

    base = spec["vehicles"][0]
    x0, y0 = float(base["start"].get("x", 0)), float(base["start"].get("y", 0))
    vehicles = []
    for i in range(args.n):
        v = copy.deepcopy(base)
        k = i + 1
        v["id"] = v["name"] = f"drone_{k}"
        v["runtime_name"] = f"Copter{k}"
        v["ros_domain_id"] = k
        v["start"].update(x=round(x0 + (i // args.columns) * args.spacing, 3),
                          y=round(y0 - (i % args.columns) * args.spacing, 3))
        v.get("components", {}).get("mns.unreal_authoring", {}).pop("spawn_source_actor", None)
        vehicles.append(v)
    spec["vehicles"] = vehicles

    out = args.out / name / "ScenarioSpec.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml.safe_dump(spec, sort_keys=False))
    print(out)


if __name__ == "__main__":
    main()
