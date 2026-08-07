#!/usr/bin/env python3
"""Print the ROS 2 topics a stack's bridges will publish — BEFORE it is started.

    ./tools/preview_topics.py ardupilot-xfs          # a scenario in compose/
    ./tools/preview_topics.py generated/xfs-fisheye  # a generated stack
    ./tools/preview_topics.py ardupilot-xfs --json   # machine-readable

Why this exists
---------------
The published names are not written down anywhere you can read. They are the
product of four inputs that only meet at runtime:

    settings.json Sensors/Cameras  ->  which sensor topics exist at all
    topic_names.yaml topic_renames ->  explicit renames
    topic_prefix / TOPIC_PREFIX    ->  '{vehicle}/' (default) or '/' (flat)
    the bridge's fixed topic list  ->  odom, imu, takeoff, ...

So the usual way to find out what a stack publishes is to start it, wait for
Unreal to load, and run `ros2 topic list` — and if the answer is wrong, edit and
do it again. This resolves the same four inputs up front.

Truthfulness
------------
The names are computed by the BRIDGE IMAGE'S OWN launch code, not by a copy of
its rules here: this script reads the compose file for each bridge's launch
arguments and mounted config, then calls _canonical_vehicle_topics(),
load_topic_renames() and _final_topic() out of the image's
rpc_dynamic_vehicles.launch.py (which single_vehicle.launch.py wraps via
airsim_bringup.launch.py). A change to the naming rules in a new bridge image
changes this output with it — there is no second implementation to drift.

Costs one `docker compose config` plus one throwaway container (~2s). Needs the
bridge image locally, which any run of the stack needs anyway.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LAUNCH_FILE = ("/ws/install/airsim_ros2_bridge/share/airsim_ros2_bridge"
               "/launch/rpc_dynamic_vehicles.launch.py")

# Runs INSIDE the bridge image. Reads a request on stdin, writes results on
# stdout. Deliberately thin: every naming decision is delegated to the launch
# module loaded from the image.
DRIVER = r'''
import importlib.util, json, sys

spec = importlib.util.spec_from_file_location("bridge_launch", LAUNCH_FILE_PATH)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

req = json.load(sys.stdin)
out = []

for b in req["bridges"]:
    settings = {}
    if b.get("settings_path"):
        try:
            with open(b["settings_path"]) as f:
                settings = (json.load(f).get("Vehicles") or {}).get(b["vehicle"], {}) or {}
        except Exception as e:
            b["settings_error"] = str(e)

    rename_map = mod.load_topic_renames(b.get("topic_names_path") or "")
    prefix = b.get("topic_prefix") or mod._DEFAULT_TOPIC_PREFIX
    active = bool(rename_map) or prefix != mod._DEFAULT_TOPIC_PREFIX

    rels = mod._canonical_vehicle_topics(settings or None)
    fixed = set(mod._FIXED_VEHICLE_TOPICS)

    topics = [{"rel": r,
               "topic": mod._final_topic(b["vehicle"], r, active, rename_map, prefix),
               "source": "fixed" if r in fixed else "settings.json",
               "renamed": r in rename_map and rename_map[r] != r}
              for r in rels]

    # Conditional extras the launch adds when those features are on. Same
    # naming path, so they belong in the preview rather than as a surprise.
    for rel, on in (("registered_point_cloud", b.get("enable_local_obs")),
                    ("scan", b.get("enable_laserscan"))):
        if on:
            topics.append({"rel": rel,
                           "topic": mod._final_topic(b["vehicle"], rel, active, rename_map, prefix),
                           "source": "derived",
                           "renamed": False})

    # Rename rules for topics this vehicle never publishes: harmless, but a
    # typo'd key in topic_names.yaml looks exactly like this, so say so.
    orphans = sorted(k for k in rename_map if k not in {t["rel"] for t in topics})

    out.append({**b, "topics": topics, "orphan_renames": orphans,
                "rename_active": active, "resolved_prefix": prefix.replace("{vehicle}", b["vehicle"]),
                "settings_found": bool(settings)})

json.dump(out, sys.stdout)
'''


def die(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


MISSING_VAR = re.compile(r"required variable (\w+) is missing a value")


def compose_config(compose_file: Path, project_dir: Path):
    """Fully resolved compose model — the same interpolation `up` would do.

    Returns (model, stubbed_vars). Generated stacks declare `${VAR:?...}` for
    values their launcher supplies at run time (SIM2REAL_RUNS_DIR and friends),
    and `config` refuses to render without them. None of those reach a topic
    name — they are bind-mount sources on unrelated services — so rather than
    demand the caller reconstruct the launcher's environment just to read a
    list of topics, stub each one `config` complains about and try again. The
    stubbed names are reported, so a variable that DID matter would be visible.
    """
    env = dict(os.environ)
    # CONFIG_ROOT only when the stack does not set its own: a process variable
    # OUTWEIGHS the project .env, so defaulting it unconditionally would point a
    # generated stack (CONFIG_ROOT=./config, relative to its own directory) at
    # the repo's config tree and silently preview the wrong settings.json.
    dotenv = project_dir / ".env"
    stack_sets_config_root = (
        dotenv.is_file()
        and any(line.startswith("CONFIG_ROOT=") for line in dotenv.read_text().splitlines())
    )
    if not stack_sets_config_root:
        env.setdefault("CONFIG_ROOT", str(REPO / "config"))
    env.setdefault("MSRS_ROOT", str(REPO))
    env.setdefault("HOST_UID", str(os.getuid()))
    env.setdefault("HOST_GID", str(os.getgid()))
    env.setdefault("UID", str(os.getuid()))
    env.setdefault("GID", str(os.getgid()))
    # Only affects which SERVICES appear, never a topic name; ask for all of
    # them so a profile left off here cannot hide a bridge.
    cmd = ["docker", "compose", "--project-directory", str(project_dir),
           "-f", str(compose_file), "--profile", "*", "config", "--format", "json"]

    stubbed = []
    for _ in range(40):                       # one var per attempt; bounded
        p = subprocess.run(cmd, capture_output=True, text=True, env=env)
        if p.returncode == 0:
            return json.loads(p.stdout), stubbed
        m = MISSING_VAR.search(p.stderr)
        if not m or m.group(1) in env:
            die(f"`docker compose config` failed:\n{p.stderr.strip()}")
        env[m.group(1)] = f"/nonexistent/preview-stub/{m.group(1)}"
        stubbed.append(m.group(1))
    die("`docker compose config` still missing variables after 40 substitutions")


def host_path_for(service: dict, container_path: str):
    """Map a path inside a container back to its bind source on the host."""
    for vol in service.get("volumes") or []:
        if vol.get("target") == container_path and vol.get("source"):
            return vol["source"]
    return None


def find_settings_json(model: dict):
    """The sim's settings.json, wherever it is mounted. This is the file that
    decides which sensor and camera topics exist at all."""
    for svc in model.get("services", {}).values():
        for vol in svc.get("volumes") or []:
            tgt, src = vol.get("target") or "", vol.get("source")
            if tgt.endswith("/settings.json") and src and Path(src).is_file():
                return src
    return None


# `key:=value`, optionally quoted. Scanned WITHIN each command element rather
# than treating elements as tokens: the scenario stacks pass exec-form argv (one
# arg per element) but the generated stacks pass a whole `bash -lc` script as a
# single element, with the `ros2 launch` line and its arguments inside it.
LAUNCH_ARG = re.compile(r'([A-Za-z_][A-Za-z0-9_]*):=(?:"([^"]*)"|(\S+))')

# Shell parameter expansion as it appears in those scripts. `$$` is compose's
# escape for a literal `$`, so what reaches the shell is ${VAR} / ${VAR:-default}.
SHELL_VAR = re.compile(r'^\$\$?\{(\w+)(?::-([^}]*))?\}$')


def _resolve(value: str, env: dict) -> str:
    """Resolve a launch-argument value that is a shell expansion of a service
    environment variable. Returns it unchanged when it is a literal, or when
    nothing in the environment matches and there is no default."""
    m = SHELL_VAR.match(value.strip())
    if not m:
        return value.strip().strip('"')
    name, default = m.group(1), m.group(2)
    return str(env.get(name, default if default is not None else value))


def parse_launch_args(cmd, env: dict) -> dict:
    args = {}
    for element in cmd or []:
        if not isinstance(element, str):
            continue
        for m in LAUNCH_ARG.finditer(element):
            raw = m.group(2) if m.group(2) is not None else m.group(3)
            args[m.group(1)] = _resolve(raw.rstrip('\\').strip(), env)
    return args


def collect_bridges(model: dict, settings_path):
    bridges = []
    for name, svc in sorted((model.get("services") or {}).items()):
        cmd = svc.get("command") or []
        if not any(isinstance(t, str) and "vehicle_name:=" in t for t in cmd):
            continue
        env = svc.get("environment") or {}
        if isinstance(env, list):                       # compose normalises to a
            env = dict(e.split("=", 1) for e in env if "=" in e)   # map, but be safe
        env = {k: "" if v is None else str(v) for k, v in env.items()}
        args = parse_launch_args(cmd, env)

        cfg = args.get("topic_names_config")
        bridges.append({
            "service": name,
            "container": svc.get("container_name") or name,
            "vehicle": args.get("vehicle_name", ""),
            "topic_prefix": args.get("topic_prefix"),
            "ros_domain_id": env.get("ROS_DOMAIN_ID", ""),
            "topic_names_path": host_path_for(svc, cfg) if cfg else None,
            "topic_names_container_path": cfg,
            "settings_path": settings_path,
            "enable_local_obs": args.get("enable_local_obs", "false") == "true",
            "enable_laserscan": args.get("enable_laserscan", "false") == "true",
        })
    return bridges


def run_in_bridge_image(image: str, bridges, mounts) -> list:
    with tempfile.TemporaryDirectory() as tmp:
        driver = Path(tmp) / "driver.py"
        driver.write_text(DRIVER.replace("LAUNCH_FILE_PATH", repr(LAUNCH_FILE)))
        cmd = ["docker", "run", "--rm", "-i", "--entrypoint", "bash",
               "-v", f"{driver}:/driver.py:ro"]
        for m in mounts:
            cmd += ["-v", f"{m}:{m}:ro"]
        cmd += [image, "-lc",
                "source /opt/ros/humble/setup.bash >/dev/null 2>&1; "
                "source /ws/install/setup.bash >/dev/null 2>&1; "
                "python3 /driver.py"]
        p = subprocess.run(cmd, input=json.dumps({"bridges": bridges}),
                           capture_output=True, text=True)
        if p.returncode != 0:
            die(f"topic resolution failed inside {image}:\n{p.stderr.strip()}")
        try:
            return json.loads(p.stdout)
        except json.JSONDecodeError:
            die(f"unexpected output from {image}:\n{p.stdout[-2000:]}\n{p.stderr[-2000:]}")


def report(results, settings_path, brief=False):
    if not results:
        print("No AirSim bridge services found in this stack.")
        return

    print()
    print(f"settings.json: {settings_path or 'NOT FOUND — fixed topics only'}")
    print()

    shown = results[:1] if brief else results
    for b in shown:
        head = f"{b['container']}  (vehicle {b['vehicle']}"
        if b["ros_domain_id"]:
            head += f", ROS_DOMAIN_ID={b['ros_domain_id']}"
        head += ")"
        print(head)
        print("  " + "-" * (len(head) - 2))
        shown = b['topic_prefix'] if b['topic_prefix'] else "{vehicle}/ (bridge default)"
        print(f"  topic_prefix: {shown} -> {b['resolved_prefix']!r}"
              f"{'' if b['rename_active'] else '   (no remapping in effect)'}")
        if not b["settings_found"]:
            why = b.get("settings_error") or f"no Vehicles['{b['vehicle']}'] entry"
            print(f"  WARNING: sensor/camera topics unknown ({why}); showing fixed topics only.")
        print()

        by_source = {}
        for t in b["topics"]:
            by_source.setdefault(t["source"], []).append(t)
        labels = {"fixed": "always published", "settings.json": "from settings.json sensors/cameras",
                  "derived": "derived nodes (enabled by launch args)"}
        for src in ("settings.json", "derived", "fixed"):
            group = by_source.get(src)
            if not group:
                continue
            print(f"  {labels[src]}:")
            for t in group:
                print(f"    {t['topic']}{'   [renamed]' if t['renamed'] else ''}")
            print()

        if b["orphan_renames"] and not brief:
            print("  topic_names.yaml keys with no matching topic on this vehicle")
            print("  (harmless for a sensor this scenario does not run — but this is")
            print("  also exactly what a typo'd key looks like):")
            for k in b["orphan_renames"]:
                print(f"    {k}")
            print()

    if brief and len(results) > 1:
        rest = ", ".join(f"{b['vehicle']} (domain {b['ros_domain_id']})" for b in results[1:])
        print(f"  Same set under each remaining vehicle's own prefix: {rest}")
        print("  Full listing: ./tools/preview_topics.py <stack>")
        print()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", help="scenario name (compose/<name>) or a stack directory")
    ap.add_argument("--json", action="store_true", help="emit the raw result")
    ap.add_argument("--brief", action="store_true",
                    help="first vehicle in full, the rest summarised (pre-launch display)")
    ap.add_argument("--image", default=os.environ.get("AIRSIM_BRIDGE_IMAGE",
                    "dhdevspace/auto_mns:airsim-ros2-bridge"),
                    help="bridge image whose launch code resolves the names")
    args = ap.parse_args()

    target = Path(args.target)
    if (REPO / "compose" / args.target / "docker-compose.yml").is_file():
        compose_file = REPO / "compose" / args.target / "docker-compose.yml"
        project_dir = REPO                       # scenario stacks bind ./config
    elif (target / "docker-compose.yml").is_file():
        compose_file = target / "docker-compose.yml"
        project_dir = target                     # generated stacks carry their own .env
    elif target.is_file():
        compose_file, project_dir = target, target.parent
    else:
        die(f"no compose file for '{args.target}' "
            f"(tried compose/{args.target}/docker-compose.yml and {target}/docker-compose.yml)")

    model, stubbed = compose_config(compose_file, project_dir)
    settings_path = find_settings_json(model)
    bridges = collect_bridges(model, settings_path)
    if not bridges:
        print("No AirSim bridge services found in this stack.")
        return

    # Mount only what the driver reads, read-only.
    mounts = sorted({p for p in [settings_path] + [b["topic_names_path"] for b in bridges]
                     if p and Path(p).is_file()})
    results = run_in_bridge_image(args.image, bridges, mounts)

    if args.json:
        json.dump(results, sys.stdout, indent=2)
        print()
    else:
        report(results, settings_path, brief=args.brief)
        if stubbed:
            print("  Note: these compose variables were unset and stubbed for this")
            print("  preview (they feed bind mounts, not topic names): "
                  + ", ".join(stubbed))
            print()


if __name__ == "__main__":
    main()
