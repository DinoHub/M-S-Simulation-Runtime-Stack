#!/usr/bin/env python3
"""Fly a launched PX4 swarm in formation and bring it home, over MAVLink.

    tools/swarm-stress/formation.py <scenario> [--alt 25] [--speed 4]
        [--route "0,-60;40,-60;40,0"] [--lead 20]

Every vehicle flies the same route as north,east metre offsets from its own
start, so the grid the swarm spawned in moves as one block and keeps its
formation; after the last leg RETURN_TO_LAUNCH flies each one back to its
start and lands it. The default route goes 60 m west, 40 m north, 60 m east
and then home.

The stack must be up (the dashboard's launch, or run_level.sh's). Each
vehicle's part runs inside its own PX4 container (formation_agent.py, over
the container's mavlink-router), all of them started together at a shared
wall-clock instant --lead seconds out. Progress goes to stderr; the last
line on stdout is a JSON summary.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import re
import subprocess
import sys
import time
from pathlib import Path

AGENT = Path(__file__).with_name("formation_agent.py")


def containers(scenario: str) -> tuple[str, list[str]]:
    """(autopilot, its containers in vehicle order) for the scenario's stack."""
    out = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True).stdout
    for autopilot in ("px4", "ardupilot"):
        found = [n for n in out.split() if scenario in n and re.search(rf"-{autopilot}-drone-\d+$", n)]
        if found:
            return autopilot, sorted(found, key=lambda n: int(n.rsplit("-", 1)[1]))
    return "", []


def fly(container: str, args: list[str], timeout: float) -> dict:
    proc = subprocess.run(["docker", "exec", "-i", container, "python3", "-", *args],
                          input=AGENT.read_text(), capture_output=True, text=True, timeout=timeout)
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("{")]
    try:
        return json.loads(lines[-1]) if lines else {"ok": False, "error": proc.stderr.strip()[-300:]}
    except ValueError:
        return {"ok": False, "error": lines[-1][:300]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scenario")
    ap.add_argument("--alt", type=float, default=25.0)
    ap.add_argument("--speed", type=float, default=4.0)
    ap.add_argument("--route", default="0,-60;40,-60;40,0")
    ap.add_argument("--lead", type=float, default=20.0,
                    help="seconds from now to the shared start, for every upload to land first")
    ap.add_argument("--timeout", type=float, default=900.0)
    ap.add_argument("--url", help="MAVLink URL inside each autopilot container (default: PX4's free "
                                  "MAVROS endpoint, udpout:127.0.0.1:14555)")
    a = ap.parse_args()

    autopilot, names = containers(a.scenario)
    if not names:
        print(f"no PX4 containers for {a.scenario}; is the stack up?", file=sys.stderr)
        return 2
    start_at = time.time() + a.lead
    args = ["--autopilot", autopilot] + (["--url", a.url] if a.url else []) + ["--alt", str(a.alt), "--speed", str(a.speed), "--route", a.route,
            "--start-at", str(start_at), "--timeout", str(a.timeout)]
    print(f"{len(names)} {autopilot} vehicles; route {a.route} at {a.alt:g} m, {a.speed:g} m/s; "
          f"start in {a.lead:g}s", file=sys.stderr, flush=True)
    t0 = time.time()
    results: dict[str, dict] = {}
    with cf.ThreadPoolExecutor(len(names)) as ex:
        futures = {ex.submit(fly, n, args, a.lead + a.timeout + 120): n for n in names}
        for fut in cf.as_completed(futures):
            name = futures[fut]
            try:
                results[name] = fut.result()
            except Exception as exc:  # noqa: BLE001
                results[name] = {"ok": False, "error": str(exc)}
            r = results[name]
            vid = name.rsplit("-", 1)[1]
            print(f"  drone {vid:>2}: {'landed' if r.get('ok') else 'FAILED'} "
                  f"{r.get('flight_s', '')}{'s' if r.get('flight_s') else ''} "
                  f"{r.get('error', '') or r.get('stage', '')}", file=sys.stderr, flush=True)
    ok = [n for n, r in results.items() if r.get("ok")]
    times = [r["flight_s"] for r in results.values() if r.get("flight_s")]
    summary = {
        "scenario": a.scenario, "vehicles": len(names), "landed_home": len(ok),
        "failed": {n.rsplit("-", 1)[1]: (r.get("stage"), r.get("error")) for n, r in results.items() if not r.get("ok")},
        "flight_s_min": min(times) if times else None, "flight_s_max": max(times) if times else None,
        "wall_s": round(time.time() - t0),
    }
    print(json.dumps(summary))
    return 0 if len(ok) == len(names) else 1


if __name__ == "__main__":
    sys.exit(main())
