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
    }


# scenario -> context builder. Tasks registering new scenarios add entries.
CONTEXT_BUILDERS: dict[str, Callable[[dict], dict]] = {
    "ardupilot-xfs": build_context_ardupilot_xfs,
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


# scenario -> self-test function. Tasks registering new scenarios add entries.
SELF_TESTS: dict[str, Callable[[], None]] = {
    "ardupilot-xfs": _self_test_ardupilot_xfs,
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
