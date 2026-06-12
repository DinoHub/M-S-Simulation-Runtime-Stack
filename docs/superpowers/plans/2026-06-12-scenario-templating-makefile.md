# Scenario Templating, Root Makefile, Image Standardisation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** All 4 launch.sh scenarios generated from Jinja templates (root `.env` as single source of truth), a root Makefile wrapping launch.sh, and every scenario on the standardised `airsim-ros2-bridge` image.

**Architecture:** Two phases. Phase 1 (Tasks 1–6) is mechanical: `tools/generate_scenario.py` becomes scenario-aware, three new templates render byte-identical (modulo the generated-file banner) to the committed compose files, launch.sh's regen block goes generic, a root Makefile is added. Phase 2 (Tasks 7–9) deliberately changes behavior: the monolith `ros2-x11-node` is replaced per scenario with `airsim_bridge_dN` + `mavros_dN` on the standalone image, following the existing ardupilot-xfs pattern (commit c71c3d0).

**Tech Stack:** Python 3 + jinja2 + python-dotenv (already used), GNU Make, docker compose v2.

**Spec:** `docs/superpowers/specs/2026-06-12-scenario-templating-makefile-design.md`

**Spec amendment (approved rationale):** root `.env` already sets `AIRSIM_IMAGE=dhdevspace/auto_mns:xfs-latest` globally. A soft `${AIRSIM_IMAGE:-…}` default in condo templates would therefore pull the XFS sim image into condo scenarios. Condo scenarios use `AIRSIM_CONDO_IMAGE` instead (default `dhdevspace/auto_mns:tevv-airsim-condo-latest-ceilingless`).

**Testing model:** the generator's `--self-test` is the test suite (pure functions, no docker needed). Every task runs it. Golden checks (`diff` against pre-task snapshots, `docker compose config`) prove Phase 1 changes nothing.

---

### Task 1: Scenario-aware generator refactor (behavior identical)

**Files:**
- Modify: `tools/generate_scenario.py` (full rewrite, code below)

- [ ] **Step 1: Baseline — verify the existing self-test and drift check pass**

```bash
cd /home/mnsuser/M-S-Simulation-Runtime-Stack
python3 tools/generate_scenario.py --self-test
python3 tools/generate_scenario.py --check && echo "no drift"
```

Expected: `self_test: OK` and `no drift`. If `--check` fails, run `python3 tools/generate_scenario.py` once and commit the regenerated files separately BEFORE this task ("chore: regenerate ardupilot-xfs from current .env").

- [ ] **Step 2: Rewrite `tools/generate_scenario.py`**

Replace the whole file with the version below. It is the existing code restructured around a `SCENARIOS` registry; the ardupilot-xfs context builder, render banner, drift check, and self-test assertions are unchanged.

```python
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


def write_outputs(scenario: str, ctx: dict, dry_run: bool = False) -> dict[Path, str]:
    """Render one scenario's templates. Returns {output_path: content}; writes when not dry-run."""
    env = make_env()
    out: dict[Path, str] = {}
    for tmpl_rel, out_rel in SCENARIOS[scenario]:
        content = render(env, tmpl_rel, ctx)
        out_path = REPO_ROOT / out_rel
        out[out_path] = content
        if not dry_run:
            out_path.write_text(content, encoding="utf-8")
    return out


def check_drift(scenarios: list[str]) -> int:
    """Render to memory, compare against on-disk. Exit 0 if match, 1 if drift."""
    env = load_env(REPO_ROOT)
    drift = []
    for scenario in scenarios:
        ctx = CONTEXT_BUILDERS[scenario](env)
        rendered = write_outputs(scenario, ctx, dry_run=True)
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


def _self_test() -> None:
    for name in sorted(SELF_TESTS):
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
                   help="run invariant checks and exit (no write, no .env read)")
    args = p.parse_args(argv)

    scenarios = args.scenario or sorted(SCENARIOS)

    if args.self_test:
        _self_test()
        return 0

    if args.check:
        return check_drift(scenarios)

    env = load_env(REPO_ROOT)
    total = 0
    for scenario in scenarios:
        ctx = CONTEXT_BUILDERS[scenario](env)
        write_outputs(scenario, ctx)
        total += len(SCENARIOS[scenario])
        print(f"Regenerated {scenario} ({len(SCENARIOS[scenario])} files).",
              file=sys.stderr)
    print(f"Regenerated {total} files total.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

- [ ] **Step 3: Verify identical behavior**

```bash
python3 tools/generate_scenario.py --self-test
python3 tools/generate_scenario.py --check && echo "no drift"
python3 tools/generate_scenario.py --scenario ardupilot-xfs --check && echo "scenario flag ok"
git diff --stat compose/ config/   # must be empty
```

Expected: `self_test[ardupilot-xfs]: OK`, `self_test: OK`, `no drift`, `scenario flag ok`, empty diff.

- [ ] **Step 4: Commit**

```bash
git add tools/generate_scenario.py
git commit -m "refactor(tools): make generate_scenario.py scenario-aware (registry + --scenario)"
```

---

### Task 2: px4-xfs template + context builder

**Files:**
- Create: `compose/px4-xfs/templates/docker-compose.yml.j2`
- Modify: `tools/generate_scenario.py` (register scenario; add `Px4Drone`, builder, self-test)
- Regenerate: `compose/px4-xfs/docker-compose.yml`

- [ ] **Step 1: Snapshot the current file for the golden check**

```bash
cp compose/px4-xfs/docker-compose.yml /tmp/px4-xfs-before.yml
```

- [ ] **Step 2: Create the template from the current file**

```bash
mkdir -p compose/px4-xfs/templates
cp compose/px4-xfs/docker-compose.yml compose/px4-xfs/templates/docker-compose.yml.j2
```

Then edit `compose/px4-xfs/templates/docker-compose.yml.j2`:

(a) In the header comment, replace the 4 hardcoded drone lines and the ros2 line (current lines 13–17):

```
{% for d in px4_drones %}
#   - px4-bridge-drone-{{ d.n }}  : PX4 SITL instance {{ d.instance }}  (cpu core {{ d.cpuset }})
{% endfor %}
#   - ros2-x11-node       : ROS2 + MAVROS with X11 forwarding (cpu cores {{ ros2_cpuset }})
```

(b) Replace the FOUR `px4-bridge-drone-{1..4}` service blocks (current lines ~100–284) with ONE loop block:

```
{% for d in px4_drones %}
  # ===========================================================================
  # PX4 SITL — Drone {{ d.n }}  (instance {{ d.instance }}, cpu core {{ d.cpuset }})
  # ===========================================================================
  px4-bridge-drone-{{ d.n }}:
    image: dhdevspace/auto_mns:px4-airsim-px4
    pull_policy: if_not_present
    container_name: px4-drone-{{ d.n }}
    hostname: px4-drone-{{ d.n }}

    network_mode: "host"
    ipc: host

    cpuset: "{{ d.cpuset }}"

    command: >
      bash -c "
        echo 'Using PX4_SIM_HOSTNAME: '$$PX4_SIM_HOSTNAME' (host network mode)' &&
        echo 'Waiting for AirSim simulation to fully initialize{{ '' if d.n == 1 else ' (staggered)' }}...' &&
        sleep {{ d.stagger_s }} &&
        cd /px4_workspace/PX4-Autopilot &&
        echo 'Starting PX4 SITL for drone {{ d.n }} (instance {{ d.instance }}) on HOST NETWORK...' &&
        exec ./Scripts/run_airsim_sitl.sh {{ d.instance }}
      "

    environment:
      PX4_SIM_HOSTNAME: ${PX4_SIM_HOSTNAME:-localhost}
      PX4_SIM_MODEL: ${PX4_SIM_MODEL:-none_iris}
      PX4_SIMULATOR: none
      PX4_HOME_LAT: ${PX4_HOME_LAT:-42.76919401}
      PX4_HOME_LON: ${PX4_HOME_LON:--115.59330958}
      PX4_HOME_ALT: ${PX4_HOME_ALT:-0.0}
      PX4_SYS_AUTOSTART: ${PX4_SYS_AUTOSTART:-10016}
      PX4_INSTANCE: {{ d.instance }}
      MAV_0_BROADCAST: 1
      MAV_1_BROADCAST: 1
      MAV_2_BROADCAST: 1
      SWARM_ID: ${SWARM_ID:-1}
      SWARM_SIZE: ${SWARM_SIZE:-{{ num_drones }}}
      MAVLINK_MODE: ${MAVLINK_MODE:-router}
      MAVLINK_TARGET: ${MAVLINK_TARGET:-localhost}
      ROUTER_LOG_LEVEL: ${ROUTER_LOG_LEVEL:-info}
      ROUTER_DEDUPE_PERIOD: ${ROUTER_DEDUPE_PERIOD:-500}
      HOST_NETWORK_MODE: "true"

    restart: unless-stopped

{% endfor %}
```

Byte-identical caveats vs the current file (check each against `/tmp/px4-xfs-before.yml`): drone 1's wait message has no `(staggered)` suffix (handled by the inline if); drone 1's first comment line says `(instance 0, cpu core 8)` (loop produces it); the SITL start comment for drone 1 lacks `cpu core` wording differences — compare the rendered output, not assumptions. Note: drone 1's `MAVLINK_MODE` comment line (`# MAVLink router — use 'router' for optimised multi-drone routing`) appears ONLY in drone 1's block in the current file; add it inside the loop guarded with `{% if d.n == 1 %}…{% endif %}` immediately above the `MAVLINK_MODE:` line to stay byte-identical.

(c) In the `ros2-x11-node` block: `cpuset: "12-15"` → `cpuset: "{{ ros2_cpuset }}"`, and `MAVROS_NUM_DRONES: ${MAVROS_NUM_DRONES:-4}` → `MAVROS_NUM_DRONES: ${MAVROS_NUM_DRONES:-{{ num_drones }}}`. Also the comment `(cpu cores 12-15)` above the service → `(cpu cores {{ ros2_cpuset }})`.

- [ ] **Step 3: Register the scenario in `tools/generate_scenario.py`**

Add to `SCENARIOS`:

```python
    "px4-xfs": [
        (
            "compose/px4-xfs/templates/docker-compose.yml.j2",
            "compose/px4-xfs/docker-compose.yml",
        ),
    ],
```

Add below the `Drone` dataclass:

```python
@dataclass(frozen=True)
class Px4Drone:
    n: int                 # 1-indexed drone number
    instance: int          # 0-indexed PX4 SITL instance (n - 1)
    cpuset: int            # dedicated host cpu core
    stagger_s: int         # startup sleep before SITL launch (s)
    domain_id: int         # ROS_DOMAIN_ID default (Phase 2 bridges)
    mavros_local: int      # MAVROS local udp port (14540 + instance)
    mavros_remote: int     # MAVROS remote udp port (14580 + instance)


def build_context_px4_xfs(env: dict) -> dict:
    """Assemble the Jinja render context for px4-xfs."""
    n = _num_drones(env)
    vehicle_prefix = env.get("VEHICLE_PREFIX") or "Copter"
    cpuset_base = _int(env, "PX4_CPUSET_BASE", 8)
    drones = [
        Px4Drone(
            n=k,
            instance=k - 1,
            cpuset=cpuset_base + k - 1,
            stagger_s=20 + 5 * max(0, k - 2),
            domain_id=_int(env, f"DRONE_{k}_DOMAIN_ID", k),
            mavros_local=14540 + k - 1,
            mavros_remote=14580 + k - 1,
        )
        for k in range(1, n + 1)
    ]
    ros2_first = cpuset_base + n
    return {
        "num_drones": n,
        "vehicle_prefix": vehicle_prefix,
        "px4_drones": drones,
        "ros2_cpuset": f"{ros2_first}-{ros2_first + 3}",
    }
```

Register the builder and self-test:

```python
CONTEXT_BUILDERS["px4-xfs"] = build_context_px4_xfs
```

Add the self-test function and register it in `SELF_TESTS`:

```python
def _self_test_px4_xfs() -> None:
    j_env = make_env()
    pairs = SCENARIOS["px4-xfs"]
    for n in (1, 2, 4, 8):
        ctx = build_context_px4_xfs({"NUM_DRONES": str(n), "PX4_CPUSET_BASE": "8"})
        renders = [render(j_env, t, ctx) for t, _ in pairs]
        renders2 = [render(j_env, t, ctx) for t, _ in pairs]
        for a, b in zip(renders, renders2):
            assert a == b, f"px4-xfs render not idempotent for N={n}"
        (compose_yaml,) = renders
        for d in ctx["px4_drones"]:
            assert f"px4-bridge-drone-{d.n}:" in compose_yaml, \
                f"missing px4-bridge-drone-{d.n} for N={n}"
            assert f'cpuset: "{d.cpuset}"' in compose_yaml, \
                f"missing cpuset {d.cpuset} for drone {d.n}, N={n}"
            assert f"PX4_INSTANCE: {d.instance}" in compose_yaml, \
                f"missing PX4_INSTANCE {d.instance} for N={n}"
        assert f"px4-bridge-drone-{n + 1}:" not in compose_yaml, \
            f"unexpected drone {n + 1} for N={n}"
        # Stagger arithmetic: d1=20, d2=20, d3=25, d4=30, ...
        assert ctx["px4_drones"][0].stagger_s == 20
        if n >= 3:
            assert ctx["px4_drones"][2].stagger_s == 25
        assert "{{" not in compose_yaml and "{%" not in compose_yaml, \
            f"unsubstituted Jinja in px4-xfs output for N={n}"


SELF_TESTS["px4-xfs"] = _self_test_px4_xfs
```

- [ ] **Step 4: Regenerate and run the golden check**

```bash
python3 tools/generate_scenario.py --scenario px4-xfs
# Generated file = 6-line banner + body; body must equal the snapshot exactly.
diff <(tail -n +6 compose/px4-xfs/docker-compose.yml) /tmp/px4-xfs-before.yml
```

Expected: empty diff. If not, fix the TEMPLATE (never the generated file) until it is. Requires `.env` `NUM_DRONES=4` (current value); if it differs, run with `NUM_DRONES=4 python3 tools/generate_scenario.py --scenario px4-xfs` for the golden check, then regenerate normally.

Then semantic equivalence:

```bash
docker compose --project-directory . -f /tmp/px4-xfs-before.yml config > /tmp/a.yml
docker compose --project-directory . -f compose/px4-xfs/docker-compose.yml config > /tmp/b.yml
diff /tmp/a.yml /tmp/b.yml && echo "compose-config identical"
```

- [ ] **Step 5: Self-test + drift check**

```bash
python3 tools/generate_scenario.py --self-test
python3 tools/generate_scenario.py --check && echo "no drift"
```

Expected: `self_test[ardupilot-xfs]: OK`, `self_test[px4-xfs]: OK`, `no drift`.

- [ ] **Step 6: Commit**

```bash
git add tools/generate_scenario.py compose/px4-xfs/
git commit -m "feat(px4-xfs): generate docker-compose.yml from jinja template (NUM_DRONES-driven)"
```

---

### Task 3: px4-condo template (verbatim)

**Files:**
- Create: `compose/px4-condo/templates/docker-compose.yml.j2`
- Modify: `tools/generate_scenario.py`
- Regenerate: `compose/px4-condo/docker-compose.yml`

- [ ] **Step 1: Snapshot + create verbatim template**

```bash
cp compose/px4-condo/docker-compose.yml /tmp/px4-condo-before.yml
mkdir -p compose/px4-condo/templates
cp compose/px4-condo/docker-compose.yml compose/px4-condo/templates/docker-compose.yml.j2
```

No template edits in Phase 1 (single-drone scenario; parameterisation lands in Task 7).

- [ ] **Step 2: Register in `tools/generate_scenario.py`**

Add to `SCENARIOS`:

```python
    "px4-condo": [
        (
            "compose/px4-condo/templates/docker-compose.yml.j2",
            "compose/px4-condo/docker-compose.yml",
        ),
    ],
```

Add the shared condo builder (used by Task 4 too) and registrations:

```python
def build_context_condo(env: dict) -> dict:
    """Condo scenarios are single-drone by design; pinned, not NUM_DRONES-driven."""
    return {"num_drones": 1}


CONTEXT_BUILDERS["px4-condo"] = build_context_condo
```

Self-test:

```python
def _self_test_px4_condo() -> None:
    j_env = make_env()
    ctx = build_context_condo({})
    pairs = SCENARIOS["px4-condo"]
    a = [render(j_env, t, ctx) for t, _ in pairs]
    b = [render(j_env, t, ctx) for t, _ in pairs]
    assert a == b, "px4-condo render not idempotent"
    (compose_yaml,) = a
    for svc in ("airsim-condo:", "px4-drone-1:", "qgroundcontrol-x11:",
                "pixel-streaming-signalling:"):
        assert svc in compose_yaml, f"missing service {svc} in px4-condo"
    assert "{{" not in compose_yaml and "{%" not in compose_yaml


SELF_TESTS["px4-condo"] = _self_test_px4_condo
```

(Phase 1 keeps `ros2-x11-node:` in the file but the self-test deliberately does not assert it — Task 7 removes it.)

- [ ] **Step 3: Regenerate, golden check, self-test**

```bash
python3 tools/generate_scenario.py --scenario px4-condo
diff <(tail -n +6 compose/px4-condo/docker-compose.yml) /tmp/px4-condo-before.yml
python3 tools/generate_scenario.py --self-test
python3 tools/generate_scenario.py --check && echo "no drift"
```

Expected: empty diff; all self-tests OK; no drift.

Note: the template contains literal `${VAR:-default}` compose interpolations and `!reset` YAML tags — Jinja passes both through untouched (they are not Jinja syntax). The `{{` self-test assert proves it.

- [ ] **Step 4: Commit**

```bash
git add tools/generate_scenario.py compose/px4-condo/
git commit -m "feat(px4-condo): generate docker-compose.yml from jinja template"
```

---

### Task 4: ardupilot-condo template (verbatim)

**Files:**
- Create: `compose/ardupilot-condo/templates/docker-compose.yml.j2`
- Modify: `tools/generate_scenario.py`
- Regenerate: `compose/ardupilot-condo/docker-compose.yml`

- [ ] **Step 1: Snapshot + verbatim template**

```bash
cp compose/ardupilot-condo/docker-compose.yml /tmp/ardupilot-condo-before.yml
mkdir -p compose/ardupilot-condo/templates
cp compose/ardupilot-condo/docker-compose.yml compose/ardupilot-condo/templates/docker-compose.yml.j2
```

- [ ] **Step 2: Register in `tools/generate_scenario.py`**

```python
    "ardupilot-condo": [
        (
            "compose/ardupilot-condo/templates/docker-compose.yml.j2",
            "compose/ardupilot-condo/docker-compose.yml",
        ),
    ],
```

```python
CONTEXT_BUILDERS["ardupilot-condo"] = build_context_condo
```

```python
def _self_test_ardupilot_condo() -> None:
    j_env = make_env()
    ctx = build_context_condo({})
    pairs = SCENARIOS["ardupilot-condo"]
    a = [render(j_env, t, ctx) for t, _ in pairs]
    b = [render(j_env, t, ctx) for t, _ in pairs]
    assert a == b, "ardupilot-condo render not idempotent"
    (compose_yaml,) = a
    for svc in ("ardupilot-drone-0:", "airsim-condo:", "qgroundcontrol-x11:"):
        assert svc in compose_yaml, f"missing service {svc} in ardupilot-condo"
    assert "{{" not in compose_yaml and "{%" not in compose_yaml


SELF_TESTS["ardupilot-condo"] = _self_test_ardupilot_condo
```

- [ ] **Step 3: Regenerate, golden check, self-test**

```bash
python3 tools/generate_scenario.py --scenario ardupilot-condo
diff <(tail -n +6 compose/ardupilot-condo/docker-compose.yml) /tmp/ardupilot-condo-before.yml
python3 tools/generate_scenario.py --self-test
python3 tools/generate_scenario.py --check && echo "no drift"
```

Expected: empty diff; 4 self-tests OK; no drift.

- [ ] **Step 4: Commit**

```bash
git add tools/generate_scenario.py compose/ardupilot-condo/
git commit -m "feat(ardupilot-condo): generate docker-compose.yml from jinja template"
```

---

### Task 5: Generic regen block in launch.sh

**Files:**
- Modify: `launch.sh:234-242` (the ardupilot-xfs-only regen block)

- [ ] **Step 1: Replace the scenario-specific block**

Current block:

```bash
# Regenerate scenario files from Jinja templates if needed.
# Idempotent: --check exits 0 when outputs match templates and .env, so the
# generator only writes when something drifted (e.g., NUM_DRONES changed).
if [ "$SCENARIO" = "ardupilot-xfs" ] && [ -f "$SCRIPT_DIR/tools/generate_scenario.py" ]; then
  if ! python3 "$SCRIPT_DIR/tools/generate_scenario.py" --check >/dev/null 2>&1; then
    echo "Regenerating ardupilot-xfs scenario files (drift detected)..."
    python3 "$SCRIPT_DIR/tools/generate_scenario.py"
  fi
fi
```

New block:

```bash
# Regenerate scenario files from Jinja templates if needed.
# Generic: any scenario with a compose/<scenario>/templates/ dir is
# generator-managed. Idempotent: --check exits 0 when outputs match
# templates and .env, so the generator only writes on drift (e.g.,
# NUM_DRONES changed).
if [ -d "compose/${SCENARIO}/templates" ] && [ -f "$SCRIPT_DIR/tools/generate_scenario.py" ]; then
  if ! python3 "$SCRIPT_DIR/tools/generate_scenario.py" --scenario "$SCENARIO" --check >/dev/null 2>&1; then
    echo "Regenerating ${SCENARIO} scenario files (drift detected)..."
    python3 "$SCRIPT_DIR/tools/generate_scenario.py" --scenario "$SCENARIO"
  fi
fi
```

Also update the usage() text line `For ardupilot-xfs, drone count is set via NUM_DRONES in .env (default 4); the launcher regenerates compose + settings.json from compose/ardupilot-xfs/templates/ via tools/generate_scenario.py.` to:

```
Scenarios with a compose/<scenario>/templates/ dir are generated from Jinja
templates via tools/generate_scenario.py (drone count via NUM_DRONES in .env,
default 4); the launcher regenerates them automatically on drift.
```

- [ ] **Step 2: Verify**

```bash
bash -n launch.sh
./launch.sh --help | head -40
# Drift round-trip: touch a generated file, launch should NOT be needed to fix it —
# the generator detects and repairs on next launch. Simulate without docker:
echo "# scratch" >> compose/px4-condo/docker-compose.yml
python3 tools/generate_scenario.py --scenario px4-condo --check; echo "exit=$? (expect 1)"
python3 tools/generate_scenario.py --scenario px4-condo
python3 tools/generate_scenario.py --scenario px4-condo --check && echo "repaired"
```

Expected: syntax OK, help shows new text, `exit=1`, then `repaired`.

- [ ] **Step 3: Commit**

```bash
git add launch.sh
git commit -m "feat(launch): generic template-regen for any scenario with templates/ dir"
```

---

### Task 6: Root Makefile

**Files:**
- Create: `Makefile`

- [ ] **Step 1: Write the Makefile**

```make
# M&S Simulation Runtime Stack — dev convenience wrapper around ./launch.sh.
#
# Scenario targets (each wraps `./launch.sh <scenario> <flags>`):
#   make ardupilot-xfs | px4-xfs | px4-condo | ardupilot-condo
#
# Flag vars -> launch.sh flags (set to `true` to enable):
#   HEADLESS=true          -> --headless              (AirSim -RenderOffScreen)
#   AGENT_EXTERNAL=true    -> --with-agent-external   (per-drone zenoh bridges)
#   PIXEL_STREAMING=true   -> --with-pixel-streaming  (UE5 signalling sidecar)
#   MONITORING=true        -> --with-monitoring       (grafana/prometheus)
#   METRICS=true           -> --with-metrics          (metrics stack)
#   ALL=true               -> --all                   (monitoring + metrics)
# e.g.  make ardupilot-xfs HEADLESS=true AGENT_EXTERNAL=true
#
# Scenario shape (NUM_DRONES etc.) lives in .env — single source of truth.
# Generated compose files are regenerated automatically on drift by launch.sh;
# `make generate` / `make check` / `make self-test` drive the generator directly.

HEADLESS        ?= false
AGENT_EXTERNAL  ?= false
PIXEL_STREAMING ?= false
MONITORING      ?= false
METRICS         ?= false
ALL             ?= false
# `make generate SCENARIO=px4-xfs` limits the generator; empty = all scenarios.
# Also forwarded to `make stop` (stop.sh auto-detects when empty).
SCENARIO        ?=

LAUNCH_FLAGS :=
ifeq ($(HEADLESS),true)
LAUNCH_FLAGS += --headless
endif
ifeq ($(AGENT_EXTERNAL),true)
LAUNCH_FLAGS += --with-agent-external
endif
ifeq ($(PIXEL_STREAMING),true)
LAUNCH_FLAGS += --with-pixel-streaming
endif
ifeq ($(MONITORING),true)
LAUNCH_FLAGS += --with-monitoring
endif
ifeq ($(METRICS),true)
LAUNCH_FLAGS += --with-metrics
endif
ifeq ($(ALL),true)
LAUNCH_FLAGS += --all
endif

SCENARIOS := ardupilot-xfs px4-xfs px4-condo ardupilot-condo

.PHONY: help $(SCENARIOS) stop logs ps generate check self-test

help:
	@echo "Scenario targets (wrap ./launch.sh):"
	@echo "  make ardupilot-xfs | px4-xfs | px4-condo | ardupilot-condo"
	@echo "Flag vars (=true): HEADLESS AGENT_EXTERNAL PIXEL_STREAMING MONITORING METRICS ALL"
	@echo "Utility targets:"
	@echo "  stop       ./stop.sh [SCENARIO=name]"
	@echo "  logs       ./logs.sh"
	@echo "  ps         running containers (name/status/image)"
	@echo "  generate   render compose files from templates [SCENARIO=name]"
	@echo "  check      exit nonzero when rendered files drift from .env+templates"
	@echo "  self-test  generator invariant checks"
	@echo "Current flags: $(if $(LAUNCH_FLAGS),$(LAUNCH_FLAGS),(none))"

$(SCENARIOS):
	./launch.sh $@ $(LAUNCH_FLAGS)

stop:
	./stop.sh $(SCENARIO)

logs:
	./logs.sh

ps:
	@docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'

generate:
	python3 tools/generate_scenario.py $(if $(SCENARIO),--scenario $(SCENARIO))

check:
	python3 tools/generate_scenario.py --check $(if $(SCENARIO),--scenario $(SCENARIO))

self-test:
	python3 tools/generate_scenario.py --self-test
```

- [ ] **Step 2: Verify (no containers started)**

```bash
make help
make -n px4-condo
make -n ardupilot-xfs HEADLESS=true AGENT_EXTERNAL=true ALL=true
make check
make self-test
make ps
```

Expected: `make -n px4-condo` prints `./launch.sh px4-condo`; the flagged variant prints `./launch.sh ardupilot-xfs --headless --with-agent-external --all`; check/self-test pass.

- [ ] **Step 3: Commit**

```bash
git add Makefile
git commit -m "feat: root Makefile wrapping launch.sh (scenario targets + generator utils)"
```

---

### Task 7: px4-condo Phase 2 — image vars + monolith → bridge/mavros

**Files:**
- Modify: `compose/px4-condo/templates/docker-compose.yml.j2`
- Regenerate: `compose/px4-condo/docker-compose.yml`
- Modify: `tools/generate_scenario.py` (`_self_test_px4_condo` asserts)

- [ ] **Step 1: Unify image vars in the template**

In `compose/px4-condo/templates/docker-compose.yml.j2`:
- `image: ${AIRSIM_IMAGE:?set AIRSIM_IMAGE}` → `image: ${AIRSIM_CONDO_IMAGE:-dhdevspace/auto_mns:tevv-airsim-condo-latest-ceilingless}` (NOT `AIRSIM_IMAGE` — root .env pins that to the XFS image).
- `image: ${PX4_IMAGE:?set PX4_IMAGE}` → `image: ${PX4_IMAGE:-dhdevspace/auto_mns:px4-airsim-px4}`.
- `image: ${QGC_IMAGE:-dhdevspace/auto_mns:airsim-qgc-x11-latest}` — already correct, leave.
- `image: ${PIXEL_STREAMING_SIGNALLING_IMAGE:-…}` — already correct, leave.

- [ ] **Step 2: Replace `ros2-x11-node` with `airsim_bridge_d1` + `mavros_d1`**

Delete the whole `ros2-x11-node` service block (template lines matching the committed file's lines 128–203). Insert in its place:

```yaml
  # ============================================================================
  # AirSim ROS2 bridge (standalone image — replaces the legacy ros2-x11 monolith;
  # same recipe as ardupilot-xfs, adapted to host networking).
  # ============================================================================
  airsim_bridge_d1:
    image: ${AIRSIM_BRIDGE_IMAGE:-dhdevspace/auto_mns:airsim-ros2-bridge}
    pull_policy: if_not_present
    container_name: airsim_bridge_d1
    hostname: airsim_bridge_d1
    init: true
    restart: unless-stopped

    network_mode: host
    networks: !reset []
    ports: !reset []
    ipc: host

    user: "${UID:?set UID}:${GID:?set GID}"

    depends_on:
      # service_healthy (NOT service_started): single_vehicle.launch.py's
      # vehicle node queries AirSim's RPC port on launch with no retry; a
      # call landing mid engine-init can crash the sim. Gate on the
      # airsim-condo healthcheck (RPC port open).
      airsim-condo:
        condition: service_healthy

    environment:
      # HOME must point at a writable dir: the container runs as uid 1000,
      # which is absent from the image's /etc/passwd, so HOME would default
      # to / and `ros2 launch` would fail to create its ~/.ros/log dir.
      - HOME=/tmp
      - DISPLAY=${DISPLAY:-:0}
      - XAUTHORITY=/tmp/.Xauthority
      - ROS_DOMAIN_ID=${ROS_DOMAIN_ID:-0}
      - ROS_LOCALHOST_ONLY=0
      - AIRSIM_HOST_IP=${AIRSIM_HOST_IP:-127.0.0.1}
      - AIRSIM_HOST_PORT=${AIRSIM_HOST_PORT:-41451}
      - VEHICLE_NAME=${VEHICLE_1_NAME:-Copter1}

    volumes:
      - /dev/shm:/dev/shm:rw
      - /tmp/iceoryx2:/tmp/iceoryx2:rw
      - /tmp/.X11-unix:/tmp/.X11-unix:rw
      - ${XAUTHORITY:-$HOME/.Xauthority}:/tmp/.Xauthority:ro

    # The image ENTRYPOINT sources ROS + the bridge workspace, then exec's
    # this argv directly — no `bash -lc "source ... && ..."` wrapper needed.
    command:
      - "ros2"
      - "launch"
      - "airsim_ros2_bridge"
      - "single_vehicle.launch.py"
      - "vehicle_name:=${VEHICLE_1_NAME:-Copter1}"
      - "host_ip:=${AIRSIM_HOST_IP:-127.0.0.1}"
      - "host_port:=${AIRSIM_HOST_PORT:-41451}"
      - "enable_localization:=true"
      - "enable_coordination:=false"
      - "enable_local_obs:=${ENABLE_LOCAL_OBS:-true}"
      - "local_obs_target_frame:=${LOCAL_OBS_TARGET_FRAME:-map}"
      - "local_obs_buffer_sec:=${LOCAL_OBS_BUFFER_SEC:-5.0}"
      - "local_obs_voxel_size:=${LOCAL_OBS_VOXEL_SIZE:-0.15}"

  # ============================================================================
  # MAVROS toward the PX4 SITL (same image family — ships airsim_mavros_bringup
  # + mavros + mavros_msgs). The PX4 image's mavlink-router exposes the FCU on
  # tcp 5760 (this matches the legacy monolith's MAVROS_FCU_URL).
  # ============================================================================
  mavros_d1:
    image: ${AIRSIM_BRIDGE_IMAGE:-dhdevspace/auto_mns:airsim-ros2-bridge}
    pull_policy: if_not_present
    container_name: mavros_d1
    hostname: mavros_d1
    # init: false (intentional). mavros_bringup.launch.py's DDS cleanup step
    # forks children; with tini as PID 1 they get re-parented and never
    # finish, hanging bring-up. The bash -lc wrapper keeps bash as PID 1.
    init: false
    restart: unless-stopped

    network_mode: host
    networks: !reset []
    ports: !reset []
    ipc: host

    user: "${UID:?set UID}:${GID:?set GID}"

    depends_on:
      airsim_bridge_d1:
        condition: service_started
      px4-drone-1:
        condition: service_started

    environment:
      # Same writable-HOME fix as airsim_bridge_d1.
      - HOME=/tmp
      - ROS_DOMAIN_ID=${ROS_DOMAIN_ID:-0}
      - ROS_LOCALHOST_ONLY=0

    volumes:
      - /dev/shm:/dev/shm:rw

    command:
      - "bash"
      - "-lc"
      - >
        ros2 launch airsim_mavros_bringup mavros_bringup.launch.py
        vehicle:=${VEHICLE_1_NAME:-Copter1}
        fcu_url:=${MAVROS_FCU_URL:-tcp://127.0.0.1:5760}
        target_system_id:=1
        mavros_config:=mavros_px4.yaml
        enable_dds_cleanup:=true
```

Also update `qgroundcontrol-x11`'s `depends_on` — it currently depends only on `px4-drone-1`, leave unchanged. Delete nothing else.

- [ ] **Step 3: Update the px4-condo self-test asserts**

In `_self_test_px4_condo`, extend the service list and add monolith/bridge invariants:

```python
    for svc in ("airsim-condo:", "px4-drone-1:", "airsim_bridge_d1:",
                "mavros_d1:", "qgroundcontrol-x11:", "pixel-streaming-signalling:"):
        assert svc in compose_yaml, f"missing service {svc} in px4-condo"
    assert "ros2-x11-node:" not in compose_yaml, "legacy monolith still present in px4-condo"
    assert "enable_coordination:=false" in compose_yaml
    assert "enable_coordination:=true" not in compose_yaml
    assert "tevv-airstack-ros2-x11-node" not in compose_yaml, "monolith image still referenced"
```

- [ ] **Step 4: Regenerate + validate**

```bash
python3 tools/generate_scenario.py --scenario px4-condo
python3 tools/generate_scenario.py --self-test
docker compose --project-directory . -f compose/px4-condo/docker-compose.yml config -q && echo "config ok"
docker compose --project-directory . -f compose/px4-condo/docker-compose.yml --profile pixel-streaming config -q && echo "profile ok"
grep -c "tevv-airstack-ros2-x11-node" compose/px4-condo/docker-compose.yml || echo "monolith gone"
```

Expected: self-tests OK, `config ok`, `profile ok`, `monolith gone`.

- [ ] **Step 5: Commit**

```bash
git add tools/generate_scenario.py compose/px4-condo/
git commit -m "feat(px4-condo): migrate to standalone airsim-ros2-bridge image + unified image vars

Replaces the legacy tevv-airstack-ros2-x11-node monolith with
airsim_bridge_d1 (single_vehicle.launch.py) + mavros_d1
(airsim_mavros_bringup), following the ardupilot-xfs recipe (c71c3d0).
Drops supervisor/auto-launch/TEST_LOCAL_PLANNER behavior by design;
local-planner startup remains via launch.sh LOCAL_PLANNER_MODE."
```

---

### Task 8: ardupilot-condo Phase 2 — image vars + monolith → bridge/mavros

**Files:**
- Modify: `compose/ardupilot-condo/templates/docker-compose.yml.j2`
- Regenerate: `compose/ardupilot-condo/docker-compose.yml`
- Modify: `tools/generate_scenario.py` (`_self_test_ardupilot_condo` asserts)

- [ ] **Step 1: Unify image vars in the template**

- `image: ${AIRSIM_IMAGE:-dhdevspace/auto_mns:tevv-airsim-condo-latest-ceilingless}` → `image: ${AIRSIM_CONDO_IMAGE:-dhdevspace/auto_mns:tevv-airsim-condo-latest-ceilingless}` (root .env's global `AIRSIM_IMAGE=xfs-latest` would otherwise hijack it).
- `image: ${ARDUPILOT_IMAGE:-dhdevspace/auto_mns:ardupilot-slim}` — already correct, leave.
- `image: dhdevspace/auto_mns:airsim-qgc-x11-latest` (hardcoded) → `image: ${QGC_IMAGE:-dhdevspace/auto_mns:airsim-qgc-x11-latest}`.
- Header comment's "Optional image overrides" list: update `AIRSIM_IMAGE`→`AIRSIM_CONDO_IMAGE`, `ROS2_IMAGE` line → `AIRSIM_BRIDGE_IMAGE (default: dhdevspace/auto_mns:airsim-ros2-bridge)`, add `QGC_IMAGE`.
- Fix the QGC service's stale names while here: `container_name: ardupilot-xfs-qgc` → `container_name: ardupilot-condo-qgc`, `hostname: ardupilot-xfs-qgc` → `hostname: ardupilot-condo-qgc` (copy-paste leftover from the xfs scenario; this is a deliberate Phase 2 rename).

- [ ] **Step 2: Replace `ros2-x11-node` with `airsim_bridge_d1` + `mavros_d1`**

Delete the `ros2-x11-node` service block (committed file lines 138–224) and the `ros2_x11_build/install/logs` named volumes (lines 301–306, keep `ardupilot_instance_0`/`ardupilot_logs_0`). Insert the same two services as Task 7 Step 2 with these differences (everything else identical — repeat the full blocks from Task 7 with these substitutions applied):

- `airsim_bridge_d1` `depends_on` stays `airsim-condo: condition: service_healthy` (the service exists in this file with an nc-41451 healthcheck).
- `airsim_bridge_d1` has NO `networks: !reset []` / `ports: !reset []` lines (this file doesn't use them; plain `network_mode: host`).
- `mavros_d1` `depends_on`: `airsim_bridge_d1: service_started` and `ardupilot-drone-0: condition: service_started` (instead of `px4-drone-1`).
- `mavros_d1` command block — ArduPilot flavour:

```yaml
    command:
      - "bash"
      - "-lc"
      - >
        ros2 launch airsim_mavros_bringup mavros_bringup.launch.py
        vehicle:=${VEHICLE_1_NAME:-Copter1}
        fcu_url:=${MAVROS_FCU_URL:-udp://:14550@127.0.0.1:14550}
        target_system_id:=1
        mavros_config:=mavros_ardupilot.yaml
        enable_dds_cleanup:=true
```

- [ ] **Step 3: Update the ardupilot-condo self-test asserts**

```python
    for svc in ("ardupilot-drone-0:", "airsim-condo:", "airsim_bridge_d1:",
                "mavros_d1:", "qgroundcontrol-x11:"):
        assert svc in compose_yaml, f"missing service {svc} in ardupilot-condo"
    assert "ros2-x11-node:" not in compose_yaml, "legacy monolith still present in ardupilot-condo"
    assert "tevv-airstack-ros2-x11-node" not in compose_yaml, "monolith image still referenced"
    assert "mavros_config:=mavros_ardupilot.yaml" in compose_yaml
    assert "enable_coordination:=false" in compose_yaml
```

- [ ] **Step 4: Regenerate + validate**

```bash
python3 tools/generate_scenario.py --scenario ardupilot-condo
python3 tools/generate_scenario.py --self-test
docker compose --project-directory . -f compose/ardupilot-condo/docker-compose.yml config -q && echo "config ok"
```

Expected: self-tests OK, `config ok`.

- [ ] **Step 5: Commit**

```bash
git add tools/generate_scenario.py compose/ardupilot-condo/
git commit -m "feat(ardupilot-condo): migrate to standalone airsim-ros2-bridge image + unified image vars

Same monolith->bridge/mavros migration as px4-condo (c71c3d0 recipe),
ArduPilot flavour: fcu_url udp://:14550, mavros_ardupilot.yaml. Also
parameterises QGC image and renames the stale ardupilot-xfs-qgc
container name to ardupilot-condo-qgc."
```

---

### Task 9: px4-xfs Phase 2 — per-drone bridge/mavros, image vars

**Files:**
- Modify: `compose/px4-xfs/templates/docker-compose.yml.j2`
- Regenerate: `compose/px4-xfs/docker-compose.yml`
- Modify: `tools/generate_scenario.py` (`_self_test_px4_xfs` asserts)

**Design note (host networking + N bridges):** all px4-xfs services share the host network. N `single_vehicle.launch.py` bridges each publish `/clock`; on one DDS domain they'd race each other and reset tf2 buffers (the exact failure the ardupilot-xfs per-drone networks avoid). Isolation here comes from ROS_DOMAIN_ID instead: bridge+mavros pair N runs on domain `{{ d.domain_id }}` (default N, override `DRONE_N_DOMAIN_ID`). Consumers (foxglove, ros2 cli) must set the matching domain.

- [ ] **Step 1: Image vars in the template**

- `image: dhdevspace/auto_mns:tevv-airsim-xfs-latest` (airsim-xfs service) → `image: ${AIRSIM_IMAGE:-dhdevspace/auto_mns:tevv-airsim-xfs-latest}` (root .env's AIRSIM_IMAGE=xfs-latest is the intended XFS override).
- In the drone loop: `image: dhdevspace/auto_mns:px4-airsim-px4` → `image: ${PX4_IMAGE:-dhdevspace/auto_mns:px4-airsim-px4}`.
- `image: dhdevspace/auto_mns:airsim-qgc-x11-latest` → `image: ${QGC_IMAGE:-dhdevspace/auto_mns:airsim-qgc-x11-latest}`.
- Header comment: replace the `ROS2_IMAGE=dhdevspace/auto_mns:ros2-x11-latest` prerequisite line with `AIRSIM_BRIDGE_IMAGE=dhdevspace/auto_mns:airsim-ros2-bridge   # optional override`.

- [ ] **Step 2: Replace `ros2-x11-node` with per-drone bridge + mavros loops**

Delete the `ros2-x11-node` service block AND the `volumes:` section at the end of the file (`ros2_x11_build/install/logs` — nothing else uses named volumes here, so the whole `volumes:` top-level key goes). Update the header comment service list: replace the `#   - ros2-x11-node …` line with:

```
{% for d in px4_drones %}
#   - airsim_bridge_d{{ d.n }} + mavros_d{{ d.n }}   (ROS_DOMAIN_ID {{ d.domain_id }})
{% endfor %}
```

Insert where ros2-x11-node was:

```yaml
  # ===========================================================================
  # PER-DRONE AIRSIM BRIDGES + MAVROS (standalone airsim-ros2-bridge image —
  # replaces the legacy ros2-x11 monolith; ardupilot-xfs recipe adapted to
  # host networking).
  #
  # All services share the host network, so per-drone isolation comes from
  # ROS_DOMAIN_ID (default: drone number; override DRONE_N_DOMAIN_ID). Each
  # bridge is its own /clock publisher inside its own domain — N bridges on
  # one domain would race /clock and reset tf2 buffers.
  # ===========================================================================
{% for d in px4_drones %}
  airsim_bridge_d{{ d.n }}:
    image: ${AIRSIM_BRIDGE_IMAGE:-dhdevspace/auto_mns:airsim-ros2-bridge}
    pull_policy: if_not_present
    container_name: airsim_bridge_d{{ d.n }}
    hostname: airsim_bridge_d{{ d.n }}
    init: true
    restart: unless-stopped

    network_mode: "host"
    ipc: host

    user: "${UID:?set UID}:${GID:?set GID}"

    depends_on:
      # service_healthy: the bridge's first RPC mid engine-init can crash
      # the sim, and a closed RPC port leaves sensor topics with 0
      # publishers and no retry.
      airsim-xfs:
        condition: service_healthy

    environment:
      # Writable HOME: uid 1000 is absent from the image's /etc/passwd, so
      # HOME would default to / and `ros2 launch` couldn't create ~/.ros/log.
      - HOME=/tmp
      - DISPLAY=${DISPLAY:-:0}
      - XAUTHORITY=/tmp/.Xauthority
      - ROS_DOMAIN_ID=${DRONE_{{ d.n }}_DOMAIN_ID:-{{ d.domain_id }}}
      - ROS_LOCALHOST_ONLY=0
      - AIRSIM_HOST_IP=${AIRSIM_HOST_IP:-127.0.0.1}
      - AIRSIM_HOST_PORT=${AIRSIM_HOST_PORT:-41451}
      - VEHICLE_NAME=${VEHICLE_{{ d.n }}_NAME:-{{ vehicle_prefix }}{{ d.n }}}

    volumes:
      - /dev/shm:/dev/shm:rw
      - /tmp/iceoryx2:/tmp/iceoryx2:rw
      - /tmp/.X11-unix:/tmp/.X11-unix:rw
      - ${XAUTHORITY:-$HOME/.Xauthority}:/tmp/.Xauthority:ro

    command:
      - "ros2"
      - "launch"
      - "airsim_ros2_bridge"
      - "single_vehicle.launch.py"
      - "vehicle_name:=${VEHICLE_{{ d.n }}_NAME:-{{ vehicle_prefix }}{{ d.n }}}"
      - "host_ip:=${AIRSIM_HOST_IP:-127.0.0.1}"
      - "host_port:=${AIRSIM_HOST_PORT:-41451}"
      - "enable_localization:=true"
      - "enable_coordination:=false"
      - "enable_local_obs:=${ENABLE_LOCAL_OBS:-true}"
      - "local_obs_target_frame:=${LOCAL_OBS_TARGET_FRAME:-map}"
      - "local_obs_buffer_sec:=${LOCAL_OBS_BUFFER_SEC:-5.0}"
      - "local_obs_voxel_size:=${LOCAL_OBS_VOXEL_SIZE:-0.15}"

  mavros_d{{ d.n }}:
    image: ${AIRSIM_BRIDGE_IMAGE:-dhdevspace/auto_mns:airsim-ros2-bridge}
    pull_policy: if_not_present
    container_name: mavros_d{{ d.n }}
    hostname: mavros_d{{ d.n }}
    # init: false: mavros_bringup's DDS-cleanup forks children that tini
    # would orphan; the bash -lc wrapper keeps bash as PID 1 to reap them.
    init: false
    restart: unless-stopped

    network_mode: "host"
    ipc: host

    user: "${UID:?set UID}:${GID:?set GID}"

    depends_on:
      airsim_bridge_d{{ d.n }}:
        condition: service_started
      px4-bridge-drone-{{ d.n }}:
        condition: service_started

    environment:
      - HOME=/tmp
      - ROS_DOMAIN_ID=${DRONE_{{ d.n }}_DOMAIN_ID:-{{ d.domain_id }}}
      - ROS_LOCALHOST_ONLY=0

    volumes:
      - /dev/shm:/dev/shm:rw

    # PX4 offboard MAVLink pair: local 14540+i / remote 14580+i per SITL
    # instance i. Override per drone with DRONE_N_FCU_URL if the
    # mavlink-router profile maps differently.
    command:
      - "bash"
      - "-lc"
      - >
        ros2 launch airsim_mavros_bringup mavros_bringup.launch.py
        vehicle:=${VEHICLE_{{ d.n }}_NAME:-{{ vehicle_prefix }}{{ d.n }}}
        fcu_url:=${DRONE_{{ d.n }}_FCU_URL:-udp://:{{ d.mavros_local }}@127.0.0.1:{{ d.mavros_remote }}}
        target_system_id:={{ d.n }}
        mavros_config:=mavros_px4.yaml
        enable_dds_cleanup:={{ "true" if d.n == 1 else "false" }}

{% endfor %}
```

- [ ] **Step 3: Update `_self_test_px4_xfs` asserts**

Add inside the per-N loop (after the existing drone asserts):

```python
        for d in ctx["px4_drones"]:
            assert f"airsim_bridge_d{d.n}:" in compose_yaml, \
                f"missing bridge for drone {d.n}, N={n}"
            assert f"mavros_d{d.n}:" in compose_yaml, \
                f"missing mavros for drone {d.n}, N={n}"
            assert f"udp://:{d.mavros_local}@127.0.0.1:{d.mavros_remote}" in compose_yaml, \
                f"bad mavros port pair for drone {d.n}, N={n}"
        assert "ros2-x11-node:" not in compose_yaml, "legacy monolith still present in px4-xfs"
        assert "tevv-airstack-ros2-x11-node" not in compose_yaml
        assert compose_yaml.count("enable_coordination:=false") == n
        assert "enable_coordination:=true" not in compose_yaml
        assert compose_yaml.count("enable_dds_cleanup:=true") == 1, \
            f"dds cleanup must run on d1 only, N={n}"
```

- [ ] **Step 4: Regenerate + validate**

```bash
python3 tools/generate_scenario.py --scenario px4-xfs
python3 tools/generate_scenario.py --self-test
docker compose --project-directory . -f compose/px4-xfs/docker-compose.yml config -q && echo "config ok"
grep -c "airsim_bridge_d" compose/px4-xfs/docker-compose.yml   # expect >= 4 with NUM_DRONES=4
```

Expected: self-tests OK, `config ok`.

- [ ] **Step 5: Commit**

```bash
git add tools/generate_scenario.py compose/px4-xfs/
git commit -m "feat(px4-xfs): per-drone airsim-ros2-bridge + mavros, unified image vars

Replaces the single ros2-x11 monolith with NUM_DRONES bridge+mavros
pairs on the standalone image. Host networking means per-drone isolation
comes from ROS_DOMAIN_ID (default: drone number) instead of per-drone
docker networks; each bridge owns /clock inside its own domain."
```

---

### Task 10: Final verification sweep

**Files:** none (verification only)

- [ ] **Step 1: Generator suite**

```bash
python3 tools/generate_scenario.py --self-test
python3 tools/generate_scenario.py --check && echo "no drift"
```

Expected: `self_test[ardupilot-condo]: OK`, `self_test[ardupilot-xfs]: OK`, `self_test[px4-condo]: OK`, `self_test[px4-xfs]: OK`, `self_test: OK`, `no drift`.

- [ ] **Step 2: Compose validation, all scenarios incl. profiles**

```bash
for s in ardupilot-xfs px4-xfs px4-condo ardupilot-condo; do
  docker compose --project-directory . -f compose/$s/docker-compose.yml config -q && echo "$s ok"
done
docker compose --project-directory . -f compose/ardupilot-xfs/docker-compose.yml \
  --profile per-drone-bridge --profile agent-external --profile pixel-streaming config -q && echo "xfs profiles ok"
docker compose --project-directory . -f compose/px4-condo/docker-compose.yml \
  --profile pixel-streaming config -q && echo "condo profile ok"
```

Expected: all `ok`.

- [ ] **Step 3: Makefile + launch.sh dry runs**

```bash
make help
make -n px4-xfs PIXEL_STREAMING=true
make check
bash -n launch.sh && ./launch.sh --help >/dev/null && echo "launch ok"
```

- [ ] **Step 4: No stray monolith references in scenario compose files**

```bash
grep -rn "tevv-airstack-ros2-x11-node" compose/ && echo "FAIL: monolith ref remains" || echo "clean"
```

Expected: `clean`.

- [ ] **Step 5: Smoke test (requires GPU host + AirSim images; run per scenario when convenient)**

```bash
./launch.sh px4-condo
docker ps --format '{{.Names}}\t{{.Status}}'        # airsim-condo healthy, bridge+mavros up
docker exec airsim_bridge_d1 bash -lc 'ros2 topic list | head'   # /Copter1/* topics
docker exec mavros_d1 bash -lc 'ros2 topic echo /Copter1/mavros/state --once'  # connected: true
./stop.sh px4-condo
```

For px4-xfs remember per-drone domains: `docker exec airsim_bridge_d2 bash -lc 'ROS_DOMAIN_ID=2 ros2 topic list'`.

- [ ] **Step 6: Update SIMREADME/README launch docs if they mention ros2-x11-node for these scenarios**

```bash
grep -rn "ros2-x11-node" README.md SIMREADME.md docs/ 2>/dev/null
```

Fix any hits that describe the migrated scenarios (state the bridge+mavros replacement and the Makefile entry points). Commit:

```bash
git add README.md SIMREADME.md docs/
git commit -m "docs: update launch docs for templated scenarios + Makefile"
```

---

## Self-Review Notes

- Spec coverage: generator scenario-awareness (T1), 3 new templates (T2–T4), generic launch.sh (T5), Makefile (T6), image vocabulary + monolith replacement (T7–T9), verification (T10). Spec's `AIRSIM_IMAGE` per-scenario default amended to `AIRSIM_CONDO_IMAGE` for condo scenarios (collision with root .env documented in header).
- Type consistency: `Px4Drone` fields (`n/instance/cpuset/stagger_s/domain_id/mavros_local/mavros_remote`) match every template/self-test reference; `build_context_condo` shared by T3/T4 registrations; `SELF_TESTS`/`CONTEXT_BUILDERS` keys match `SCENARIOS` keys exactly.
- Known risk, called out where it bites: px4-xfs mavros FCU port pair (14540/14580 per instance) follows PX4 offboard convention but the image's mavlink-router profile may map differently — `DRONE_N_FCU_URL` override exists; T10 Step 5 smoke verifies `connected: true`.
