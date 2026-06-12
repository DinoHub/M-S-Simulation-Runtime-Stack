#!/usr/bin/env python3
"""Regenerate scenario files from Jinja templates.

Single source of truth: the runtime-stack root .env. Reads NUM_DRONES,
VEHICLE_PREFIX, port bases and similar scenario-shape keys, builds a
per-scenario context, and renders the production files registered in
SCENARIOS.

Usage:
  python3 tools/generate_scenario.py                       # regenerate ALL scenarios
  python3 tools/generate_scenario.py --scenario px4-xfs    # one scenario (repeatable)
  python3 tools/generate_scenario.py --check               # exit 1 if outputs drift from .env+templates
  python3 tools/generate_scenario.py --self-test           # invariants (no write, no .env read)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from dotenv import dotenv_values
from jinja2 import Environment, FileSystemLoader, StrictUndefined

REPO_ROOT = Path(__file__).resolve().parent.parent

MIN_DRONES = 1
MAX_DRONES = 16

# scenario -> [(template_path, output_path)], both relative to REPO_ROOT.
# Registered scenarios must have their templates on disk.
SCENARIOS: dict[str, list[tuple[str, str]]] = {
    "ardupilot-xfs": [
        (
            "compose/ardupilot-xfs/templates/docker-compose.yml.j2",
            "compose/ardupilot-xfs/docker-compose.yml",
        ),
        (
            "compose/ardupilot-xfs/templates/docker-compose.mavros-test.yml.j2",
            "compose/ardupilot-xfs/docker-compose.mavros-test.yml",
        ),
        (
            "config/unreal-airsim/xfs/templates/settings-ardupilot.json.j2",
            "config/unreal-airsim/xfs/settings-ardupilot.json",
        ),
    ],
    "px4-xfs": [
        (
            "compose/px4-xfs/templates/docker-compose.yml.j2",
            "compose/px4-xfs/docker-compose.yml",
        ),
        (
            "config/unreal-airsim/xfs/templates/settings-px4.json.j2",
            "config/unreal-airsim/xfs/settings-px4.json",
        ),
    ],
    "px4-condo": [
        (
            "compose/px4-condo/templates/docker-compose.yml.j2",
            "compose/px4-condo/docker-compose.yml",
        ),
        (
            "config/unreal-airsim/condo/templates/settings-px4.json.j2",
            "config/unreal-airsim/condo/settings-px4.json",
        ),
    ],
    "ardupilot-condo": [
        (
            "compose/ardupilot-condo/templates/docker-compose.yml.j2",
            "compose/ardupilot-condo/docker-compose.yml",
        ),
        (
            "config/unreal-airsim/condo/templates/settings-ardupilot.json.j2",
            "config/unreal-airsim/condo/settings-ardupilot.json",
        ),
    ],
}


@dataclass(frozen=True)
class Drone:
    n: int                 # 1-indexed drone number
    instance: int          # 0-indexed SITL instance number (n - 1)
    vehicle: str           # AirSim vehicle key (e.g. "Copter1")
    domain_id: int         # ROS_DOMAIN_ID default
    mavlink_tcp: int       # MAVLink TCP port (host)
    fdm_tcp: int           # FDM TCP port (AirSim <-> SITL)
    fdm_udp: int           # FDM UDP port (SITL -> AirSim)
    subnet_octet: int      # third octet of agent_internal-N subnet
    x_offset: float        # spawn X (m) in AirSim NED frame


@dataclass(frozen=True)
class Px4Drone:
    n: int                 # 1-indexed drone number
    instance: int          # 0-indexed PX4 SITL instance (n - 1)
    cpuset: int            # dedicated host cpu core
    stagger_s: int         # startup sleep before SITL launch (s)
    domain_id: int         # ROS_DOMAIN_ID default (Phase 2 bridges)
    mavros_local: int      # MAVROS bind port (14560 + instance, the px4 image's
                           # mavlink-router MAVROS endpoint; AirSim owns 14540+i,
                           # QGC owns 14550+i)
    tcp_port: int          # AirSim <-> PX4 SITL lockstep TCP (4560 + instance)
    control_local: int     # AirSim ControlPortLocal (14540 + instance, mavlink-router's AirSim endpoint)
    control_remote: int    # AirSim ControlPortRemote (14580 + instance, router's AirSim_Inbound)
    x_offset: float        # spawn X (m), instance * DRONE_X_SPACING_M


def _int(env: dict, key: str, default: int) -> int:
    v = env.get(key)
    if v is None or v == "":
        return default
    try:
        return int(v)
    except ValueError as e:
        raise SystemExit(f"{key} must be an integer, got {v!r}") from e


def _float(env: dict, key: str, default: float) -> float:
    v = env.get(key)
    if v is None or v == "":
        return default
    try:
        return float(v)
    except ValueError as e:
        raise SystemExit(f"{key} must be a number, got {v!r}") from e


def load_env(repo_root: Path) -> dict:
    """Read .env (single source of truth) into a dict.

    Falls back to the calling shell's os.environ for keys absent from
    the file — same precedence as docker compose's own .env loading.
    """
    path = repo_root / ".env"
    base = dict(dotenv_values(path)) if path.is_file() else {}
    # Shell overrides file (matches `docker compose --env-file` semantics).
    for k in (
        "NUM_DRONES",
        "VEHICLE_PREFIX",
        "DRONE_X_SPACING_M",
        "MAVLINK_PORT_BASE",
        "MAVLINK_PORT_STRIDE",
        "FDM_TCP_PORT_BASE",
        "FDM_UDP_PORT_BASE",
        "FDM_PORT_STRIDE",
        "AGENT_INTERNAL_SUBNET_BASE",
        "PX4_CPUSET_BASE",
        "CAMERA_ENABLE",
        "CAMERA_NAME",
        "CAMERA_WIDTH",
        "CAMERA_HEIGHT",
        "CAMERA_FOV",
    ):
        if k in os.environ:
            base[k] = os.environ[k]
    return base


def _num_drones(env: dict) -> int:
    n = _int(env, "NUM_DRONES", 4)
    if not MIN_DRONES <= n <= MAX_DRONES:
        raise SystemExit(
            f"NUM_DRONES must be in [{MIN_DRONES}, {MAX_DRONES}], got {n}"
        )
    return n


def build_context_ardupilot_xfs(env: dict) -> dict:
    """Assemble the Jinja render context for ardupilot-xfs."""
    n = _num_drones(env)
    vehicle_prefix = env.get("VEHICLE_PREFIX") or "Copter"
    x_spacing = _float(env, "DRONE_X_SPACING_M", 8.0)
    mavlink_base = _int(env, "MAVLINK_PORT_BASE", 5760)
    mavlink_stride = _int(env, "MAVLINK_PORT_STRIDE", 10)
    fdm_tcp_base = _int(env, "FDM_TCP_PORT_BASE", 9002)
    fdm_udp_base = _int(env, "FDM_UDP_PORT_BASE", 9003)
    fdm_stride = _int(env, "FDM_PORT_STRIDE", 10)
    subnet_base = env.get("AGENT_INTERNAL_SUBNET_BASE") or "172.28"

    drones: list[Drone] = []
    for k in range(1, n + 1):
        instance = k - 1
        drones.append(Drone(
            n=k,
            instance=instance,
            vehicle=env.get(f"VEHICLE_{k}_NAME") or f"{vehicle_prefix}{k}",
            domain_id=_int(env, f"DRONE_{k}_DOMAIN_ID", k),
            mavlink_tcp=mavlink_base + instance * mavlink_stride,
            fdm_tcp=fdm_tcp_base + instance * fdm_stride,
            fdm_udp=fdm_udp_base + instance * fdm_stride,
            subnet_octet=k,
            x_offset=instance * x_spacing,
        ))

    return {
        "num_drones": n,
        "vehicle_prefix": vehicle_prefix,
        "drones": drones,
        "subnet_base": subnet_base,
        "x_spacing": x_spacing,
        "mavlink_port_base": mavlink_base,
        "fdm_tcp_port_base": fdm_tcp_base,
        "camera": _camera_context(env),
    }


def _camera_context(env: dict) -> dict:
    """Per-vehicle camera block for settings.json (CAMERA_* keys in .env).

    Off by default — the bridge auto-discovers cameras from settings
    (auto_discover_cameras defaults true), so flipping CAMERA_ENABLE=true and
    regenerating is all it takes to get /<vehicle>/<name>_Scene/image topics.
    Resolution defaults stay modest: every enabled camera is rendered for
    EVERY drone, and large captures load both the GPU and AirSim's
    single-threaded RPC server.
    """
    return {
        "enable": (env.get("CAMERA_ENABLE") or "false").strip().lower() == "true",
        "name": env.get("CAMERA_NAME") or "Camera1",
        "width": _int(env, "CAMERA_WIDTH", 1280),
        "height": _int(env, "CAMERA_HEIGHT", 720),
        "fov": _float(env, "CAMERA_FOV", 81.0),
    }


def build_context_px4_xfs(env: dict) -> dict:
    """Assemble the Jinja render context for px4-xfs."""
    n = _num_drones(env)
    vehicle_prefix = env.get("VEHICLE_PREFIX") or "Copter"
    cpuset_base = _int(env, "PX4_CPUSET_BASE", 8)
    x_spacing = _float(env, "DRONE_X_SPACING_M", 8.0)
    # Startup stagger: d1=d2=20s (both early starters wait for the same AirSim
    # init window), then +5s per drone: d3=25, d4=30, ...
    drones = [
        Px4Drone(
            n=k,
            instance=k - 1,
            cpuset=cpuset_base + k - 1,
            stagger_s=20 + 5 * max(0, k - 2),
            domain_id=_int(env, f"DRONE_{k}_DOMAIN_ID", k),
            # The px4 image's mavlink-router opens a dedicated MAVROS UDP
            # endpoint per instance, sending to 127.0.0.1:14560+i. NOT the
            # 14540/14580 pair (AirSim binds ControlPortLocal/Remote) and
            # NOT 14550+i (QGC's listener).
            mavros_local=14560 + k - 1,
            tcp_port=4560 + k - 1,
            control_local=14540 + k - 1,
            control_remote=14580 + k - 1,
            x_offset=(k - 1) * x_spacing,
        )
        for k in range(1, n + 1)
    ]
    return {
        "num_drones": n,
        "vehicle_prefix": vehicle_prefix,
        "px4_drones": drones,
        "camera": _camera_context(env),
    }


def build_context_condo(env: dict) -> dict:
    """Condo scenarios are single-drone by design; pinned, not NUM_DRONES-driven."""
    return {"num_drones": 1, "camera": _camera_context(env)}


# scenario -> context builder. Tasks registering new scenarios add entries.
CONTEXT_BUILDERS: dict[str, Callable[[dict], dict]] = {
    "ardupilot-xfs": build_context_ardupilot_xfs,
    "px4-xfs": build_context_px4_xfs,
    "px4-condo": build_context_condo,
    "ardupilot-condo": build_context_condo,
}


def make_env() -> Environment:
    """Jinja Environment with strict undefined and trim/lstrip block."""
    return Environment(
        loader=FileSystemLoader(str(REPO_ROOT)),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        undefined=StrictUndefined,
    )


def render(env: Environment, template_rel: str, ctx: dict) -> str:
    """Render a template; prepend a 'do not edit' banner (YAML/shell comment). JSON templates get no banner — the template must ship its own header."""
    tmpl = env.get_template(template_rel)
    body = tmpl.render(**ctx)
    banner_lines = [
        "GENERATED FILE — do not edit by hand.",
        "Regenerate: python3 tools/generate_scenario.py",
        f"Source:    {template_rel}",
    ]
    if template_rel.endswith(".json.j2"):
        # JSON has no comment syntax; rely on the template's own header.
        return body
    # YAML / shell-style banner
    banner = "\n".join(f"# {line}" for line in banner_lines)
    return f"# =============================================================================\n{banner}\n# =============================================================================\n{body}"


def write_outputs(j_env: Environment, scenario: str, ctx: dict, dry_run: bool = False) -> dict[Path, str]:
    """Render one scenario's templates. Returns {output_path: content}; writes when not dry-run."""
    out: dict[Path, str] = {}
    for tmpl_rel, out_rel in SCENARIOS[scenario]:
        content = render(j_env, tmpl_rel, ctx)
        out_path = REPO_ROOT / out_rel
        out[out_path] = content
        if not dry_run:
            out_path.write_text(content, encoding="utf-8")
    return out


def check_drift(scenarios: list[str]) -> int:
    """Render to memory, compare against on-disk. Exit 0 if match, 1 if drift."""
    env = load_env(REPO_ROOT)
    j_env = make_env()
    drift = []
    for scenario in scenarios:
        ctx = CONTEXT_BUILDERS[scenario](env)
        rendered = write_outputs(j_env, scenario, ctx, dry_run=True)
        for path, content in rendered.items():
            existing = path.read_text(encoding="utf-8") if path.exists() else ""
            if existing != content:
                drift.append(path.relative_to(REPO_ROOT))
    if drift:
        print("drift detected in:", file=sys.stderr)
        for p in drift:
            print(f"  - {p}", file=sys.stderr)
        return 1
    return 0


def _self_test_ardupilot_xfs() -> None:
    """Idempotency + invariant checks for several drone counts."""
    base_env = {
        "NUM_DRONES": "4",
        "VEHICLE_PREFIX": "Copter",
        "DRONE_X_SPACING_M": "8",
        "MAVLINK_PORT_BASE": "5760",
        "MAVLINK_PORT_STRIDE": "10",
        "FDM_TCP_PORT_BASE": "9002",
        "FDM_UDP_PORT_BASE": "9003",
        "FDM_PORT_STRIDE": "10",
        "AGENT_INTERNAL_SUBNET_BASE": "172.28",
    }
    j_env = make_env()
    pairs = SCENARIOS["ardupilot-xfs"]

    for n in (1, 2, 4, 8, 16):
        env = dict(base_env, NUM_DRONES=str(n))
        ctx = build_context_ardupilot_xfs(env)

        # Idempotency: render twice, compare.
        renders = [render(j_env, t, ctx) for t, _ in pairs]
        renders2 = [render(j_env, t, ctx) for t, _ in pairs]
        for a, b in zip(renders, renders2):
            assert a == b, f"render not idempotent for N={n}"

        compose_yaml, mavros_yaml, settings_json = renders

        # Per-drone service / vehicle counts.
        assert compose_yaml.count("ardupilot-xfs-drone-") >= n, \
            f"expected at least {n} SITL containers in compose for N={n}"
        for d in ctx["drones"]:
            assert f"ardupilot-drone-{d.instance}:" in compose_yaml, \
                f"missing SITL service for drone instance {d.instance}"
            assert f"airsim_bridge_d{d.n}:" in compose_yaml, \
                f"missing bridge service for drone {d.n}"
            assert f"agent_internal-{d.n}" in compose_yaml, \
                f"missing agent_internal-{d.n} attachment"
            assert f"mavros_d{d.n}:" in mavros_yaml, \
                f"missing mavros service for drone {d.n}"
            assert f'"{d.vehicle}":' in settings_json, \
                f"missing {d.vehicle} in settings.json"

        # No vehicle past N.
        if n < MAX_DRONES:
            assert f"airsim_bridge_d{n + 1}:" not in compose_yaml, \
                f"unexpected bridge_d{n + 1} in compose for N={n}"

        # Port arithmetic — spot-check first and last drone.
        first, last = ctx["drones"][0], ctx["drones"][-1]
        assert first.mavlink_tcp == 5760
        assert last.mavlink_tcp == 5760 + 10 * last.instance
        assert first.fdm_tcp == 9002
        assert last.fdm_tcp == 9002 + 10 * last.instance
        assert last.fdm_udp == last.fdm_tcp + 1

        # Coordination disabled on every bridge — coordination_node's /clock
        # publisher races multirotor_node's /clock and triggers tf2 buffer
        # resets that wipe the registered cloud. Each multirotor_node is its
        # own /clock publisher on its agent_internal-N network.
        assert "enable_coordination:=true" not in compose_yaml, \
            f"enable_coordination must be false on every bridge for N={n}"
        assert compose_yaml.count("enable_coordination:=false") == n, \
            f"expected {n} bridges with enable_coordination:=false, got {compose_yaml.count('enable_coordination:=false')} for N={n}"

        # No unsubstituted Jinja tokens.
        for label, body in (("compose", compose_yaml), ("mavros", mavros_yaml), ("settings", settings_json)):
            assert "{{" not in body and "{%" not in body, \
                f"unsubstituted Jinja in {label} output for N={n}"

    # Camera block: off by default; per-vehicle and valid JSON when enabled.
    settings_tmpl = pairs[2][0]
    off = render(j_env, settings_tmpl,
                 build_context_ardupilot_xfs(dict(base_env, NUM_DRONES="2")))
    assert '"Cameras": {}' in off, "cameras must default to empty"
    json.loads(off)
    on = render(j_env, settings_tmpl,
                build_context_ardupilot_xfs(dict(base_env, NUM_DRONES="2",
                                                 CAMERA_ENABLE="true",
                                                 CAMERA_WIDTH="640")))
    assert on.count('"Camera1":') == 2, "camera block must appear on every vehicle"
    assert '"Width": 640' in on
    assert '"Cameras": {}' not in on
    json.loads(on)


def _self_test_px4_xfs() -> None:
    j_env = make_env()
    pairs = SCENARIOS["px4-xfs"]
    for n in (1, 2, 4, 8):
        ctx = build_context_px4_xfs({"NUM_DRONES": str(n), "PX4_CPUSET_BASE": "8"})
        renders = [render(j_env, t, ctx) for t, _ in pairs]
        renders2 = [render(j_env, t, ctx) for t, _ in pairs]
        for a, b in zip(renders, renders2):
            assert a == b, f"px4-xfs render not idempotent for N={n}"
        compose_yaml, settings_json = renders
        for d in ctx["px4_drones"]:
            assert f"px4-bridge-drone-{d.n}:" in compose_yaml, \
                f"missing px4-bridge-drone-{d.n} for N={n}"
            assert f'cpuset: "{d.cpuset}"' in compose_yaml, \
                f"missing cpuset {d.cpuset} for drone {d.n}, N={n}"
            assert f"PX4_INSTANCE: {d.instance}" in compose_yaml, \
                f"missing PX4_INSTANCE {d.instance} for N={n}"
            # Settings checks
            assert f'"{ctx["vehicle_prefix"]}{d.n}":' in settings_json, \
                f"missing vehicle {ctx['vehicle_prefix']}{d.n} in settings for N={n}"
            assert f'"TcpPort": {d.tcp_port}' in settings_json, \
                f"missing TcpPort {d.tcp_port} for drone {d.n}, N={n}"
        assert f"px4-bridge-drone-{n + 1}:" not in compose_yaml, \
            f"unexpected drone {n + 1} for N={n}"
        assert f'"{ctx["vehicle_prefix"]}{n + 1}":' not in settings_json, \
            f"unexpected vehicle {n + 1} in settings for N={n}"
        json.loads(settings_json), f"settings not valid JSON for N={n}"
        assert '"PX4Multirotor"' in settings_json, \
            f"VehicleType PX4Multirotor missing in settings for N={n}"
        assert '"Cameras": {}' in settings_json, \
            f"cameras must default to empty in settings for N={n}"
        # Stagger arithmetic: d1=20, d2=20, d3=25, d4=30, ...
        assert ctx["px4_drones"][0].stagger_s == 20
        if n >= 2:
            assert ctx["px4_drones"][1].stagger_s == 20, "d2 intentionally ties d1"
        if n >= 3:
            assert ctx["px4_drones"][2].stagger_s == 25
        for d in ctx["px4_drones"]:
            assert f"airsim_bridge_d{d.n}:" in compose_yaml, \
                f"missing bridge for drone {d.n}, N={n}"
            assert f"mavros_d{d.n}:" in compose_yaml, \
                f"missing mavros for drone {d.n}, N={n}"
            assert f"udp://:{d.mavros_local}@}}" in compose_yaml, \
                f"bad mavros bind port for drone {d.n}, N={n}"
            assert d.mavros_local == 14560 + d.instance, \
                f"mavros endpoint must be 14560+i (router conf), drone {d.n}"
        assert f"airsim_bridge_d{n + 1}:" not in compose_yaml, \
            f"unexpected bridge {n + 1} for N={n}"
        assert "ros2-x11-node:" not in compose_yaml, "legacy monolith still present in px4-xfs"
        assert "tevv-airstack-ros2-x11-node" not in compose_yaml
        assert compose_yaml.count("enable_coordination:=false") == n
        assert "enable_coordination:=true" not in compose_yaml
        assert compose_yaml.count("enable_dds_cleanup:=true") == 1, \
            f"dds cleanup must run on d1 only, N={n}"
        assert "{{" not in compose_yaml and "{%" not in compose_yaml, \
            f"unsubstituted Jinja in px4-xfs output for N={n}"

    # Camera-enabled render for N=2: each vehicle gets a camera block
    ctx2 = build_context_px4_xfs({"NUM_DRONES": "2", "PX4_CPUSET_BASE": "8",
                                   "CAMERA_ENABLE": "true"})
    _, settings_cam = [render(j_env, t, ctx2) for t, _ in pairs]
    assert settings_cam.count('"Camera1":') == 2, \
        "camera block must appear on every vehicle (N=2, camera enabled)"
    assert '"Cameras": {}' not in settings_cam
    json.loads(settings_cam)


def _self_test_ardupilot_condo() -> None:
    j_env = make_env()
    ctx = build_context_condo({})
    pairs = SCENARIOS["ardupilot-condo"]
    a = [render(j_env, t, ctx) for t, _ in pairs]
    b = [render(j_env, t, ctx) for t, _ in pairs]
    assert a == b, "ardupilot-condo render not idempotent"
    compose_yaml, settings_json = a
    for svc in ("ardupilot-drone-0:", "airsim-condo:", "airsim_bridge_d1:",
                "mavros_d1:", "qgroundcontrol-x11:"):
        assert svc in compose_yaml, f"missing service {svc} in ardupilot-condo"
    assert "ros2-x11-node:" not in compose_yaml, "legacy monolith still present in ardupilot-condo"
    assert "tevv-airstack-ros2-x11-node" not in compose_yaml, "monolith image still referenced"
    assert "mavros_config:=mavros_ardupilot.yaml" in compose_yaml
    assert "enable_coordination:=false" in compose_yaml
    assert "{{" not in compose_yaml and "{%" not in compose_yaml
    # Settings checks
    json.loads(settings_json)
    assert '"ArduCopter"' in settings_json, "VehicleType ArduCopter missing"
    assert '"UdpPort": 9003' in settings_json, "FDM UdpPort 9003 missing"
    assert '"Cameras": {}' in settings_json, "cameras must default to empty"
    # Camera-enabled render
    ctx_cam = build_context_condo({"CAMERA_ENABLE": "true"})
    _, body = [render(j_env, t, ctx_cam) for t, _ in pairs]
    assert '"Camera1":' in body, "camera block missing when enabled"
    json.loads(body)


def _self_test_px4_condo() -> None:
    j_env = make_env()
    ctx = build_context_condo({})
    pairs = SCENARIOS["px4-condo"]
    a = [render(j_env, t, ctx) for t, _ in pairs]
    b = [render(j_env, t, ctx) for t, _ in pairs]
    assert a == b, "px4-condo render not idempotent"
    compose_yaml, settings_json = a
    for svc in ("airsim-condo:", "px4-drone-1:", "airsim_bridge_d1:",
                "mavros_d1:", "qgroundcontrol-x11:", "pixel-streaming-signalling:"):
        assert svc in compose_yaml, f"missing service {svc} in px4-condo"
    assert "ros2-x11-node:" not in compose_yaml, "legacy monolith still present in px4-condo"
    assert "enable_coordination:=false" in compose_yaml
    assert "enable_coordination:=true" not in compose_yaml
    assert "tevv-airstack-ros2-x11-node" not in compose_yaml, "monolith image still referenced"
    assert "{{" not in compose_yaml and "{%" not in compose_yaml
    # Settings checks
    json.loads(settings_json)
    assert '"PX4Multirotor"' in settings_json, "VehicleType PX4Multirotor missing"
    assert '"Cameras": {}' in settings_json, "cameras must default to empty"
    # Camera-enabled render
    ctx_cam = build_context_condo({"CAMERA_ENABLE": "true"})
    _, body = [render(j_env, t, ctx_cam) for t, _ in pairs]
    assert '"Camera1":' in body, "camera block missing when enabled"
    json.loads(body)


# scenario -> self-test function. Tasks registering new scenarios add entries.
SELF_TESTS: dict[str, Callable[[], None]] = {
    "ardupilot-xfs": _self_test_ardupilot_xfs,
    "px4-xfs": _self_test_px4_xfs,
    "px4-condo": _self_test_px4_condo,
    "ardupilot-condo": _self_test_ardupilot_condo,
}

_missing_builders = SCENARIOS.keys() - CONTEXT_BUILDERS.keys()
_missing_tests = SCENARIOS.keys() - SELF_TESTS.keys()
assert not _missing_builders, f"No CONTEXT_BUILDERS entry for: {_missing_builders}"
assert not _missing_tests, f"No SELF_TESTS entry for: {_missing_tests}"


def _self_test(scenarios: list[str]) -> None:
    for name in sorted(scenarios):
        SELF_TESTS[name]()
        print(f"self_test[{name}]: OK")
    print("self_test: OK")


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--scenario", action="append", choices=sorted(SCENARIOS),
                   help="limit to one scenario (repeatable; default: all)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--check", action="store_true",
                   help="exit 1 if outputs differ from a fresh render (no write)")
    g.add_argument("--self-test", action="store_true",
                   help="run invariant checks and exit (no write, no .env read; respects --scenario)")
    args = p.parse_args(argv)

    scenarios = args.scenario or sorted(SCENARIOS)

    if args.self_test:
        _self_test(scenarios)
        return 0

    if args.check:
        return check_drift(scenarios)

    env = load_env(REPO_ROOT)
    j_env = make_env()
    total = 0
    for scenario in scenarios:
        ctx = CONTEXT_BUILDERS[scenario](env)
        write_outputs(j_env, scenario, ctx)
        total += len(SCENARIOS[scenario])
        print(f"Regenerated {scenario} ({len(SCENARIOS[scenario])} files).",
              file=sys.stderr)
    print(f"Regenerated {total} files total.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
