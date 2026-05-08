#!/usr/bin/env python3
"""Regenerate the ardupilot-xfs scenario files from Jinja templates.

Single source of truth: the runtime-stack root .env. Reads NUM_DRONES,
VEHICLE_PREFIX, port bases and similar scenario-shape keys, builds a
context, and renders three production files:

  compose/ardupilot-xfs/docker-compose.yml
  compose/ardupilot-xfs/docker-compose.mavros-test.yml
  config/unreal-airsim/xfs/settings-ardupilot.json

Usage:
  python3 tools/generate_scenario.py              # regenerate (write)
  python3 tools/generate_scenario.py --check      # exit 1 if outputs drift from .env+templates
  python3 tools/generate_scenario.py --self-test  # invariants for N in {1,2,4,8,16}
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values
from jinja2 import Environment, FileSystemLoader, StrictUndefined

REPO_ROOT = Path(__file__).resolve().parent.parent

MIN_DRONES = 1
MAX_DRONES = 16

# (template_path, output_path) — both relative to REPO_ROOT.
TEMPLATE_OUTPUTS: list[tuple[str, str]] = [
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
]


@dataclass(frozen=True)
class Drone:
    n: int                 # 1-indexed drone number
    instance: int          # 0-indexed SITL instance number (n - 1)
    vehicle: str           # AirSim vehicle key (e.g. "Copter1")
    domain_id: int         # ROS_DOMAIN_ID default
    mavlink_tcp: int       # MAVLink TCP port (host)
    fdm_tcp: int           # FDM TCP port (AirSim ↔ SITL)
    fdm_udp: int           # FDM UDP port (SITL → AirSim)
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
    ):
        if k in os.environ:
            base[k] = os.environ[k]
    return base


def build_context(env: dict) -> dict:
    """Assemble the Jinja render context from env."""
    n = _int(env, "NUM_DRONES", 4)
    if not MIN_DRONES <= n <= MAX_DRONES:
        raise SystemExit(
            f"NUM_DRONES must be in [{MIN_DRONES}, {MAX_DRONES}], got {n}"
        )

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
    """Render a template; prepend a 'do not edit' banner appropriate for the file type."""
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


def write_outputs(ctx: dict, dry_run: bool = False) -> dict[Path, str]:
    """Render all templates. Returns {output_path: content}; writes when not dry-run."""
    env = make_env()
    out: dict[Path, str] = {}
    for tmpl_rel, out_rel in TEMPLATE_OUTPUTS:
        content = render(env, tmpl_rel, ctx)
        out_path = REPO_ROOT / out_rel
        out[out_path] = content
        if not dry_run:
            out_path.write_text(content, encoding="utf-8")
    return out


def check_drift() -> int:
    """Render to memory, compare against on-disk. Exit 0 if match, 1 if drift."""
    env = load_env(REPO_ROOT)
    ctx = build_context(env)
    rendered = write_outputs(ctx, dry_run=True)
    drift = []
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


def _self_test() -> None:
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

    for n in (1, 2, 4, 8, 16):
        env = dict(base_env, NUM_DRONES=str(n))
        ctx = build_context(env)

        # Idempotency: render twice, compare.
        renders = [render(j_env, t, ctx) for t, _ in TEMPLATE_OUTPUTS]
        renders2 = [render(j_env, t, ctx) for t, _ in TEMPLATE_OUTPUTS]
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

        # Coordination: drone-1 is the clock master, exactly one such.
        assert "enable_coordination:=true" in compose_yaml
        assert compose_yaml.count("enable_coordination:=true") == 1, \
            f"expected exactly one clock master, got {compose_yaml.count('enable_coordination:=true')} for N={n}"

        # No unsubstituted Jinja tokens.
        for label, body in (("compose", compose_yaml), ("mavros", mavros_yaml), ("settings", settings_json)):
            assert "{{" not in body and "{%" not in body, \
                f"unsubstituted Jinja in {label} output for N={n}"

    print("self_test: OK")


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    g = p.add_mutually_exclusive_group()
    g.add_argument("--check", action="store_true",
                   help="exit 1 if outputs differ from a fresh render (no write)")
    g.add_argument("--self-test", action="store_true",
                   help="run invariant checks and exit (no write, no .env read)")
    args = p.parse_args(argv)

    if args.self_test:
        _self_test()
        return 0

    if args.check:
        return check_drift()

    env = load_env(REPO_ROOT)
    ctx = build_context(env)
    write_outputs(ctx)
    print(
        f"Regenerated {len(TEMPLATE_OUTPUTS)} files for NUM_DRONES={ctx['num_drones']} "
        f"(vehicle prefix '{ctx['vehicle_prefix']}', spacing {ctx['x_spacing']}m).",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
