# Scenario Templating, Root Makefile, and Image Standardisation — Design

**Date:** 2026-06-12
**Branch:** feat/runtime-source-of-truth-migration
**Status:** Approved

## Goal

Every scenario launched by `./launch.sh` is generated from Jinja templates with
the root `.env` as the single source of truth, a root `Makefile` provides the
tevv-airsim-ros2-bridge-style dev UX on top of `launch.sh`, and all scenarios
use the same standardised docker images (notably the standalone
`airsim-ros2-bridge` image instead of the legacy ros2-x11 monolith).

## Current State

| Scenario | Templated | Bridge image | Notes |
|---|---|---|---|
| ardupilot-xfs | yes (`tools/generate_scenario.py`) | `dhdevspace/auto_mns:airsim-ros2-bridge` (c71c3d0) | reference implementation |
| px4-xfs | no | monolith `tevv-airstack-ros2-x11-node-development` | 4 hand-copied PX4 drone blocks (cpuset 8–11, sleep stagger 20/20/25/30) |
| px4-condo | no | monolith | single drone; strict `:?set` image vars |
| ardupilot-condo | no | monolith | single drone; QGC image hardcoded |

The monolith `ros2-x11-node` carries behavior the standalone bridge does not:
`ENABLE_SUPERVISOR`, `AUTO_LAUNCH_ROS2`, `TEST_LOCAL_PLANNER`,
`AUTO_START_ON_TAKEOFF`, coordination. Replacing it is a behavior change, not
just an image swap.

## Decisions

1. **Template scope:** all 4 scenarios get the generate_scenario.py treatment.
2. **Makefile:** root Makefile *wraps* `launch.sh` (launch.sh remains the
   engine); no per-scenario Makefiles.
3. **Standard bridge image:** `dhdevspace/auto_mns:airsim-ros2-bridge`
   everywhere, overridable via `AIRSIM_BRIDGE_IMAGE`.
4. **Monolith:** dropped clean from condo + px4-xfs. No legacy compose
   profile; git history is the escape hatch.

## Approach: Two Phases

**Phase 1 — mechanical templating (zero behavior change).** New templates must
render byte-identical to the current committed compose files (golden check).
Generator becomes scenario-aware. launch.sh regen block goes generic. Root
Makefile added.

**Phase 2 — image standardisation (deliberate behavior change).** Per-scenario
template edits replacing the monolith with the standalone bridge + mavros
pattern from c71c3d0, plus a unified image-variable vocabulary. Isolated,
bisectable diffs.

## Phase 1 Design

### Generator (`tools/generate_scenario.py`)

- Replace flat `TEMPLATE_OUTPUTS` with:
  ```python
  SCENARIOS: dict[str, list[tuple[str, str]]] = {
      "ardupilot-xfs":   [...existing three pairs...],
      "px4-xfs":         [("compose/px4-xfs/templates/docker-compose.yml.j2",
                           "compose/px4-xfs/docker-compose.yml")],
      "px4-condo":       [("compose/px4-condo/templates/docker-compose.yml.j2",
                           "compose/px4-condo/docker-compose.yml")],
      "ardupilot-condo": [("compose/ardupilot-condo/templates/docker-compose.yml.j2",
                           "compose/ardupilot-condo/docker-compose.yml")],
  }
  ```
- CLI: `--scenario NAME` (repeatable; default = all scenarios), `--check`,
  `--self-test` retained with the same semantics.
- Context: shared keys (NUM_DRONES, VEHICLE_PREFIX, port bases) plus
  per-scenario context builders:
  - **px4-xfs:** drone loop over NUM_DRONES — `PX4_INSTANCE = i` (0-indexed),
    `cpuset = PX4_CPUSET_BASE + i` (default base 8), startup stagger
    `20 + 5 * max(0, i - 1)` seconds, MAVROS udp pair `14540+i / 14550+i`.
  - **px4-condo / ardupilot-condo:** pinned `num_drones = 1`; templated for the
    image-variable vocabulary and future N>1 support, otherwise static.
- `.env` remains the single source of truth (same `load_env` precedence:
  shell overrides file).

### Templates

`compose/<scenario>/templates/docker-compose.yml.j2` for px4-xfs, px4-condo,
ardupilot-condo. Phase 1 renders byte-identical to today's committed files
(modulo the standard generated-file banner, which is added to the committed
outputs in the same commit).

### launch.sh

Replace the ardupilot-xfs-only regen block with:

```bash
if [ -d "compose/${SCENARIO}/templates" ]; then
  if ! python3 tools/generate_scenario.py --scenario "$SCENARIO" --check >/dev/null 2>&1; then
    echo "Regenerating ${SCENARIO} scenario files (drift detected)..."
    python3 tools/generate_scenario.py --scenario "$SCENARIO"
  fi
fi
```

Everything else (profiles, networks, planner, monitoring/metrics) untouched.

### Root Makefile

Wraps launch.sh; tevv-airsim-ros2-bridge conventions (`?=` vars, `help`
default, comment-documented targets):

- `make ardupilot-xfs | px4-xfs | px4-condo | ardupilot-condo` → `./launch.sh <scenario> <flags>`
- Flag vars → launch.sh flags: `HEADLESS=true` → `--headless`,
  `AGENT_EXTERNAL=true` → `--with-agent-external`, `PIXEL_STREAMING=true` →
  `--with-pixel-streaming`, `MONITORING=true` → `--with-monitoring`,
  `METRICS=true` → `--with-metrics`, `ALL=true` → `--all`.
- `make stop` → `./stop.sh`; `make logs` → `./logs.sh`; `make ps` → compose ps
  across stacks.
- `make generate [SCENARIO=name]` / `make check` / `make self-test` →
  generator entry points.
- `make help` (default) lists targets + current var values.

## Phase 2 Design

### Unified image-variable vocabulary

All image references across the 4 scenario templates use the same names with
soft `:-` defaults (no `:?set` strictness, no hardcoded refs):

| Variable | Default |
|---|---|
| `AIRSIM_IMAGE` | per scenario (xfs-latest / tevv-airsim-xfs-latest / tevv-airsim-condo-latest-ceilingless) |
| `AIRSIM_BRIDGE_IMAGE` | `dhdevspace/auto_mns:airsim-ros2-bridge` |
| `PX4_IMAGE` | `dhdevspace/auto_mns:px4-airsim-px4` |
| `ARDUPILOT_IMAGE` | `dhdevspace/auto_mns:ardupilot-slim` |
| `QGC_IMAGE` | `dhdevspace/auto_mns:airsim-qgc-x11-latest` |
| `PIXEL_STREAMING_SIGNALLING_IMAGE` | `dhdevspace/auto_mns:tevv-pixel-streaming-signalling-5.5` |
| `ZENOH_BRIDGE_IMAGE` | `eclipse/zenoh-bridge-ros2dds:1.4.0` |

### Monolith replacement (condo scenarios + px4-xfs)

`ros2-x11-node` is removed. Per drone (1 for condo, N for px4-xfs), following
the c71c3d0 recipe, adapted to host networking (these scenarios stay
`network_mode: host`):

- `airsim_bridge_dN`: `AIRSIM_BRIDGE_IMAGE`, direct argv
  `ros2 launch airsim_ros2_bridge single_vehicle.launch.py`, `HOME=/tmp`,
  `enable_localization:=true enable_coordination:=false`
  `enable_local_obs:=${ENABLE_LOCAL_OBS:-true}`,
  `depends_on: airsim-* : service_healthy` (bridge RPC races the sim
  otherwise — see feedback memory).
- `mavros_dN`: same image, `airsim_mavros_bringup/mavros_bringup.launch.py`,
  `HOME=/tmp`, `init: false`. FCU URLs: ArduPilot condo
  `udp://:14550@127.0.0.1:14550`; PX4 condo `tcp://127.0.0.1:5760` (router);
  px4-xfs `udp://localhost:14540+i@14550+i` per drone.

**Deliberately dropped with the monolith:** supervisor, auto-launch
orchestration, `TEST_LOCAL_PLANNER`, auto-start/end-on-takeoff/landing,
coordination. Local-planner startup remains available via launch.sh
`LOCAL_PLANNER_MODE`. Named volumes `ros2_x11_build/install/logs` are removed
from the templates (the standalone image is prebuilt; existing volumes are
left on disk, not auto-deleted).

## Error Handling

- Generator: unknown `--scenario` → exit with the known-scenario list.
  Missing template file → existing SystemExit path. NUM_DRONES bounds
  unchanged (1–16); px4-xfs cpuset overflow (base + N exceeding host cores)
  is a warning, not an error.
- launch.sh: regen failure aborts the launch (set -euo pipefail already does).
- Makefile: scenario targets fail with launch.sh's own error text; no
  duplicated validation.

## Testing / Verification

1. **Phase 1 golden check:** `git diff --exit-code compose/` after
   `generate_scenario.py` (all scenarios) — empty diff apart from the
   generated-file banners added in the same commit.
2. **`--self-test` extended:** per-scenario invariants for N in {1,2,4,8,16}
   where applicable — service counts, port arithmetic (5760/9002/9003 strides
   for ardupilot, 4560/14540/14580 family for px4), cpuset assignment, no
   unsubstituted Jinja, render idempotency, `enable_coordination:=false` on
   every bridge.
3. **Compose validation:** `docker compose -f <rendered> config -q` for every
   scenario (with profiles enabled) in both phases.
4. **Phase 2 smoke:** `./launch.sh <scenario>` per scenario; verify bridge
   topics appear (`ros2 topic list` in the bridge container) and MAVROS
   reaches `connected: true`.
5. **Makefile:** each target dry-checked (`make -n`), then `make px4-condo` /
   `make stop` round-trip.

## Out of Scope

- monitoring/metrics/tools compose files (not scenario-shaped).
- Changing AirSim settings.json generation for non-xfs scenarios.
- The TEVV-Airsim-ROS2-Bridge repo itself (its Makefile is the UX reference
  only).
