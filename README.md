# M-S-Simulation-Runtime-Stack

Runtime environment used by the autonomy team. Launches the simulation(SITL + AirSim + ROS 2 bridges + ground control), and optionally a local planner, monitoring stack, and metrics collection.

This repo **does not contain the planner implementation** — the planner lives in its own repository and is integrated via `LOCAL_PLANNER_MODE` in `.env`.

---

## Quick Start

```bash
cp .env.example .env
./setup.sh                            # chmod +x for the launcher scripts
./launch.sh                           # default scenario from .env (SCENARIO=...)
./launch.sh ardupilot-xfs             # named scenario
./stop.sh                             # tear down
```

The first launch pulls Docker images, which can take several minutes.
Subsequent launches are warm.

---

## How a launch works

Three pillars:

1. **`.env` is the single source of truth.** It holds scenario shape
   (`NUM_DRONES`, `VEHICLE_PREFIX`, port bases), scenario selection
   (`SCENARIO=...`), planner mode, image tags, and feature flags.
2. **`./launch.sh` regenerates + starts.** For `ardupilot-xfs` it runs
   `tools/generate_scenario.py --check` first; if `.env` (or the
   templates) drifted from the on-disk compose/settings, it regenerates
   them. Then `docker compose up -d` brings the stack up.
3. **`./stop.sh` tears down.** Stops the scenario stack (with all
   profiles activated so any flag combination cleans up), the
   monitoring stack, and the metrics stack — idempotent.

---

## Generating the `ardupilot-xfs` scenario

### TL;DR — change drone count (or any other shape knob)

```bash
# Edit .env
sed -i 's/^NUM_DRONES=.*/NUM_DRONES=8/' .env

# Bring up — launch.sh detects drift and regenerates automatically
./launch.sh ardupilot-xfs
```

That's it for the common case. Read on for the full set of knobs,
a worked example, and how to invoke the generator directly.

### What's source, what's generated

`ardupilot-xfs` is parameterized: drone count, vehicle prefix, port
bases, and subnet base all live in `.env`. The compose files and
AirSim `settings-ardupilot.json` are **generated** from Jinja2
templates by `tools/generate_scenario.py`.

| Source of truth (`.env`) | Default | Purpose |
|---|---|---|
| `NUM_DRONES` | `4` | drones in the fleet (1..16) |
| `VEHICLE_PREFIX` | `Copter` | default vehicle name prefix |
| `DRONE_X_SPACING_M` | `8` | per-drone X offset (settings.json) |
| `MAVLINK_PORT_BASE` / `_STRIDE` | `5760` / `10` | SITL N → MAVLink TCP base + N×stride |
| `FDM_TCP_PORT_BASE` / `FDM_UDP_PORT_BASE` / `FDM_PORT_STRIDE` | `9002` / `9003` / `10` | ArduPilot ↔ AirSim FDM |
| `AGENT_INTERNAL_SUBNET_BASE` | `172.28` | `/24` prefix for `agent_internal-{1..N}` |

Templates and outputs:

| Template | Generated output |
|---|---|
| `compose/ardupilot-xfs/templates/docker-compose.yml.j2` | `compose/ardupilot-xfs/docker-compose.yml` |
| `compose/ardupilot-xfs/templates/docker-compose.mavros-test.yml.j2` | `compose/ardupilot-xfs/docker-compose.mavros-test.yml` |
| `config/unreal-airsim/xfs/templates/settings-ardupilot.json.j2` | `config/unreal-airsim/xfs/settings-ardupilot.json` |

> **Do not hand-edit the generated files.** Every `./launch.sh
> ardupilot-xfs` invocation will overwrite them via the generator's
> drift check. Edit the **template** (the `.j2` file), then run
> `python3 tools/generate_scenario.py` (or just relaunch).

### Worked example: 8-drone fleet, custom prefix, flat ROS_DOMAIN_ID

Edit `.env`:

```env
NUM_DRONES=8
VEHICLE_PREFIX=Spirit       # vehicles will be Spirit1..Spirit8
DRONE_X_SPACING_M=10        # 10m apart instead of default 8m

# Per-drone overrides (optional). Set the same domain on all 8 to
# share a single ROS_DOMAIN_ID with the autonomy team:
DRONE_1_DOMAIN_ID=20
DRONE_2_DOMAIN_ID=20
DRONE_3_DOMAIN_ID=20
DRONE_4_DOMAIN_ID=20
DRONE_5_DOMAIN_ID=20
DRONE_6_DOMAIN_ID=20
DRONE_7_DOMAIN_ID=20
DRONE_8_DOMAIN_ID=20
```

Then:

```bash
./launch.sh ardupilot-xfs
```

The generator picks up `NUM_DRONES=8`, renders 8 SITL containers,
8 bridges, and 8 vehicles in `settings-ardupilot.json` named
`Spirit1..Spirit8`. The launcher creates 8 `agent_internal-{1..8}`
networks. Topics flow on `/Spirit1/*`..`/Spirit8/*`, all on
`ROS_DOMAIN_ID=20`.

### Generator usage (direct invocation)

```bash
python3 tools/generate_scenario.py             # write all 3 outputs
python3 tools/generate_scenario.py --check     # exit 1 if drift; no write
python3 tools/generate_scenario.py --self-test # invariants for N in {1,2,4,8,16}
```

Dependencies (Python 3, `jinja2`, `python-dotenv`):

```bash
pip install -r tools/requirements.txt
```

For the per-scenario verification runbook (smoke-test sensors,
flight test, teardown), see
[`compose/ardupilot-xfs/README.md`](./compose/ardupilot-xfs/README.md).

---

## Running (`./launch.sh`)

```bash
./launch.sh                                  # default scenario from .env
./launch.sh ardupilot-xfs                    # explicit scenario
./launch.sh ardupilot-xfs --with-agent-external
./launch.sh ardupilot-xfs --headless --with-monitoring
./launch.sh px4-xfs --all
```

| Flag | Effect | `.env` equivalent |
|---|---|---|
| `--with-monitoring` | Start `docker-compose-monitoring.yml` (Prometheus, Grafana, exporters, foxglove-bridge, Lichtblick) | `START_MONITORING=true` |
| `--with-metrics` | Start `docker-compose-metrics.yml` (metrics-collector, mission-supervisor) | `START_METRICS=true` |
| `--all` | `--with-monitoring` + `--with-metrics` | both |
| `--headless` | UE5 runs with `-RenderOffScreen` (cameras + PixelStreaming still work) — `ardupilot-xfs` only | `AIRSIM_HEADLESS=true` |
| `--with-agent-external` | Also start per-drone `zenoh-bridge-{1..N}` on `agent_external` for `/shared/*` mesh — `ardupilot-xfs` only | `WITH_AGENT_EXTERNAL=true` |

Logs:

```bash
./logs.sh                                    # tail everything
./logs.sh sim                                # just the scenario stack
./logs.sh sim airsim_bridge_d1               # one service
./logs.sh metrics metrics-collector ERROR    # filter by text
```

---

## Tearing down (`./stop.sh`)

```bash
./stop.sh                                    # default scenario
./stop.sh ardupilot-xfs                      # explicit scenario
```

Behavior:

- Stops the local planner (if `LOCAL_PLANNER_MODE` is `managed-script`
  or `managed-compose`).
- Tears down the scenario stack with **all profiles activated**
  (`per-drone-bridge`, `agent-external`) so every container started
  by any flag combination is cleaned up.
- Tears down the monitoring + metrics stacks, regardless of whether
  they were started.

The MAVROS integration test fixture is a separate compose project; tear
it down explicitly if you ran it:

```bash
./compose/ardupilot-xfs/test-per-drone-mavros.sh --teardown
```

---

## Scenarios

Pick one with the `SCENARIO` env var or by passing it as the first arg
to `launch.sh` / `stop.sh`.

| Scenario | Autopilot | Drones | Scene | Notes |
|---|---|---|---|---|
| `px4-condo` | PX4 SITL | 1 | AirSim Condo | Browser viewer via pixel-streaming-signalling |
| `px4-xfs` | PX4 SITL | 4 | AirSim XFS | Multi-drone swarm |
| `ardupilot-condo` | ArduPilot SITL | 1 | AirSim Condo | MAVROS over UDP `:14550` |
| `ardupilot-xfs` | ArduPilot SITL | **N** | AirSim XFS | Per-drone bridges; **N from `NUM_DRONES`**. See [`compose/ardupilot-xfs/README.md`](./compose/ardupilot-xfs/README.md). |

`launch.sh` defaults to whatever `SCENARIO` is set to in `.env` (or
`px4-condo` if unset).

### Image tags (`.env`)

Each scenario's compose ships sensible defaults; override only if your
team has pinned a different version.

```env
ARDUPILOT_IMAGE=dhdevspace/auto_mns:ardupilot-slim
AIRSIM_IMAGE=dhdevspace/auto_mns:xfs-latest
AIRSIM_BRIDGE_IMAGE=dhdevspace/auto_mns:tevv-airstack-ros2-x11-node-multi-agent-bridge
ZENOH_BRIDGE_IMAGE=eclipse/zenoh-bridge-ros2dds:1.4.0
PX4_IMAGE=dhdevspace/auto_mns:px4-airsim-px4
```

For multi-drone ArduPilot, `ARDUPILOT_IMAGE=dhdevspace/auto_mns:ardupilot-slim`
is required — older `:ardupilot-latest` lacks the per-instance
SERIAL-port offset and all SITLs fight over port 5762.

---

## Local planner integration

Planner behavior is controlled by `LOCAL_PLANNER_MODE` in `.env`.

| Mode | Description | Required `.env` keys |
|---|---|---|
| `disabled` | No planner started | `LOCAL_PLANNER_MODE=disabled` |
| `external` | Planner already running outside this stack (recommended for active development) | `LOCAL_PLANNER_MODE=external` |
| `managed-script` | This repo runs a startup script in the planner repo | `LOCAL_PLANNER_DIR`, `LOCAL_PLANNER_START_CMD`, `LOCAL_PLANNER_STOP_CMD` |
| `managed-compose` | This repo brings up the planner's own docker-compose | `LOCAL_PLANNER_DIR`, `LOCAL_PLANNER_COMPOSE_FILE`, optional `LOCAL_PLANNER_PROFILE` |

Configuration reference: [`config/CONFIG_README.md`](./config/CONFIG_README.md).

---

## Manual MAVROS smoke test (PX4 OFFBOARD)

Reference flow for driving a single PX4 vehicle from MAVROS CLI. The
target container depends on which scenario is up:

| Scenario | Where to run MAVROS commands |
|---|---|
| `ardupilot-xfs` | inside `mavros_d1` (started by `./compose/ardupilot-xfs/test-per-drone-mavros.sh`) |
| `px4-condo` / `px4-xfs` | inside the autonomy team's MAVROS container, or any host with ROS 2 + the right `ROS_DOMAIN_ID` |

```bash
# Confirm MAVROS is connected
ros2 topic echo --once /Copter1/mavros/state           # connected: true

# Stream offboard setpoints continuously
ros2 topic pub -r 10 /Copter1/mavros/setpoint_position/local geometry_msgs/msg/PoseStamped \
  "{header: {frame_id: 'map'}, pose: {position: {x: 0.0, y: 0.0, z: 3.0}, orientation: {w: 1.0}}}"

# In another shell, switch mode + arm
ros2 service call /Copter1/mavros/set_mode mavros_msgs/srv/SetMode \
  "{base_mode: 0, custom_mode: 'OFFBOARD'}"
ros2 service call /Copter1/mavros/cmd/arming mavros_msgs/srv/CommandBool "{value: true}"

# Verify
ros2 topic echo --once /Copter1/mavros/state           # connected, mode=OFFBOARD
```

PX4 OFFBOARD requires a continuous setpoint stream **before** the
mode switch. ArduPilot's equivalent is `GUIDED` mode and uses the same
service surface — see `compose/ardupilot-xfs/scripts/test_one_drone_mavros.py`
for a full arm → takeoff → setpoint → land mission.

---

## Repository structure

```text
M-S-Simulation-Runtime-Stack/
├── compose/
│   ├── px4-condo/docker-compose.yml
│   ├── px4-xfs/docker-compose.yml
│   ├── ardupilot-condo/docker-compose.yml
│   └── ardupilot-xfs/
│       ├── docker-compose.yml                  ← generated
│       ├── docker-compose.mavros-test.yml      ← generated
│       ├── templates/                          ← Jinja sources
│       ├── scripts/                            ← MAVROS mission helpers
│       ├── test-per-drone-mavros.sh            ← integration test
│       └── README.md
├── config/
│   ├── ardupilot/config/                       ← SITL defaults
│   ├── experiments/
│   ├── metrics-collector/
│   ├── qgroundcontrol/{qgc_config,user_config}/
│   ├── unreal-airsim/{condo,xfs}/
│   │   └── xfs/templates/                      ← Jinja for settings-ardupilot.json
│   ├── zenoh/                                  ← per-drone bridge configs
│   └── CONFIG_README.md
├── tools/
│   ├── generate_scenario.py                    ← Jinja generator
│   ├── requirements.txt
│   └── README.md
├── docker-compose-monitoring.yml
├── docker-compose-metrics.yml
├── launch.sh
├── stop.sh
├── logs.sh
├── setup.sh
├── .env.example
└── README.md
```

---

## Notes

- **`.env` is the single source of truth.** Don't add scenario-local
  `.env` files inside `compose/<scenario>/` — they were a footgun that
  got removed. `launch.sh` always loads the root `.env`.
- **`ardupilot-xfs` shape lives in `.env`.** Any changes to the on-disk
  `docker-compose.yml`, `docker-compose.mavros-test.yml`, or
  `settings-ardupilot.json` will be overwritten by the generator on
  next `./launch.sh ardupilot-xfs`. Edit the templates instead.
- **If `ros2 topic echo` is silent on `/CopterN/*` topics**: see the
  Troubleshooting section in [`compose/ardupilot-xfs/README.md`](./compose/ardupilot-xfs/README.md)
  — usually a QoS or boot-race issue, both with documented workarounds.
- **If the stack fails to start**: check Docker image tags first
  (especially `ARDUPILOT_IMAGE=ardupilot-slim` for multi-drone), then
  X11 (`xhost +local:docker` and `XAUTHORITY` exported).
- **`agent_internal-N` is co-owned with the autonomy stack.** Whoever
  runs first creates the docker network; the other side attaches.
  See "Network ownership contract" in
  [`compose/ardupilot-xfs/README.md`](./compose/ardupilot-xfs/README.md)
  for the rules (especially: both sides MUST declare networks with
  `external: true, name: agent_internal-N` to avoid silent
  split-brain).
