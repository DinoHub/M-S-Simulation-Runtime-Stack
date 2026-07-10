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

A root `Makefile` wraps `./launch.sh` for convenience:

```bash
make ardupilot-xfs                    # same as ./launch.sh ardupilot-xfs
make px4-xfs HEADLESS=true ALL=true   # flags as Make vars
make stop                             # ./stop.sh
make help                             # full flag/target reference
```

The first launch pulls Docker images, which can take several minutes.
Subsequent launches are warm.

---

## How a launch works

Three pillars:

1. **`.env` is the single source of truth.** It holds scenario shape
   (`NUM_DRONES`, `VEHICLE_PREFIX`, port bases), scenario selection
   (`SCENARIO=...`), planner mode, image tags, and feature flags.
2. **`./launch.sh` regenerates + starts.** Before every launch it runs
   `tools/generate_scenario.py --check` on the target scenario; if `.env`
   (or the templates) drifted from the on-disk compose/settings, it
   regenerates them. Then `docker compose up -d` brings the stack up.
   All 4 scenarios are Jinja-template-generated — do not hand-edit the
   rendered compose files directly.
3. **`./stop.sh` tears down.** Stops the scenario stack (with all
   profiles activated so any flag combination cleans up), the
   monitoring stack, and the metrics stack — idempotent.

---

## Template-generated scenarios

All 4 scenarios (`ardupilot-xfs`, `px4-xfs`, `px4-condo`,
`ardupilot-condo`) are rendered from Jinja2 templates by
`tools/generate_scenario.py`. The root `.env` is the single source of
truth; `launch.sh` detects drift and regenerates automatically before
every launch.

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

Scenario shape (drone count, vehicle prefix, port bases, subnet base)
lives in `.env`. The compose files and AirSim `settings.json` variants
are **generated** from Jinja2 templates by `tools/generate_scenario.py`.
Every scenario generates both its compose file and its own `settings.json`
— px4 and ardupilot variants are kept separate so the two condo scenarios
don't fight over a single file.

| Source of truth (`.env`) | Default | Purpose |
|---|---|---|
| `NUM_DRONES` | `4` | drones in the fleet (1..16) |
| `VEHICLE_PREFIX` | `Copter` | default vehicle name prefix |
| `DRONE_X_SPACING_M` | `8` | per-drone X offset (settings.json) |
| `MAVLINK_PORT_BASE` / `_STRIDE` | `5760` / `10` | SITL N → MAVLink TCP base + N×stride |
| `FDM_TCP_PORT_BASE` / `FDM_UDP_PORT_BASE` / `FDM_PORT_STRIDE` | `9002` / `9003` / `10` | ArduPilot ↔ AirSim FDM |
| `AGENT_INTERNAL_SUBNET_BASE` | `172.28` | `/24` prefix for `agent_internal-{1..N}` |

Templates and outputs:

| Template | Generated output | Mounted by |
|---|---|---|
| `compose/ardupilot-xfs/templates/docker-compose.yml.j2` | `compose/ardupilot-xfs/docker-compose.yml` | — |
| `compose/ardupilot-xfs/templates/docker-compose.mavros-test.yml.j2` | `compose/ardupilot-xfs/docker-compose.mavros-test.yml` | — |
| `config/unreal-airsim/xfs/templates/settings-ardupilot.json.j2` | `config/unreal-airsim/xfs/settings-ardupilot.json` | `airsim-xfs` (ardupilot-xfs) |
| `compose/px4-xfs/templates/docker-compose.yml.j2` | `compose/px4-xfs/docker-compose.yml` | — |
| `config/unreal-airsim/xfs/templates/settings-px4.json.j2` | `config/unreal-airsim/xfs/settings-px4.json` | `airsim-xfs` (px4-xfs) |
| `compose/px4-condo/templates/docker-compose.yml.j2` | `compose/px4-condo/docker-compose.yml` | — |
| `config/unreal-airsim/condo/templates/settings-px4.json.j2` | `config/unreal-airsim/condo/settings-px4.json` | `airsim-condo` (px4-condo) |
| `compose/ardupilot-condo/templates/docker-compose.yml.j2` | `compose/ardupilot-condo/docker-compose.yml` | — |
| `config/unreal-airsim/condo/templates/settings-ardupilot.json.j2` | `config/unreal-airsim/condo/settings-ardupilot.json` | `airsim-condo` (ardupilot-condo) |

A shared Jinja partial (`config/unreal-airsim/_partials/cameras.json.j2`)
is included by all four settings templates — camera knobs apply uniformly
across every scenario.

> **Do not hand-edit the generated files.** Every `./launch.sh <scenario>`
> invocation will overwrite them via the generator's drift check. Edit
> the **template** (the `.j2` file), then run
> `python3 tools/generate_scenario.py` (or just relaunch).

### Cameras

The bridge auto-discovers cameras from `settings.json`
(`auto_discover_cameras` defaults true), so enabling cameras is a
two-step process: flip the knob in `.env`, regenerate, restart the sim.
Topics appear as `/<vehicle>/<camera>_Scene/image`.

| `.env` key | Default | Purpose |
|---|---|---|
| `CAMERA_ENABLE` | `false` | set `true` to add a camera to every vehicle |
| `CAMERA_NAME` | `Camera1` | AirSim camera key (also the ROS topic prefix) |
| `CAMERA_WIDTH` | `1280` | capture width in pixels |
| `CAMERA_HEIGHT` | `720` | capture height in pixels |
| `CAMERA_FOV` | `81` | horizontal field of view in degrees |

> **Note:** every enabled camera is rendered for **every** drone. Large
> resolutions or high drone counts load both the GPU and AirSim's
> single-threaded RPC server. A sim restart is required after regenerating
> with changed camera settings.

### Topic naming — prefix, flat mode, renames

Applies to **all four scenarios** (every `airsim_bridge_d*` runs
`single_vehicle.launch.py`). By default every bridge topic is namespaced per
vehicle: `/Copter1/odom`, `/Copter1/Imu`, `/Copter1/LidarSensor1/points`, …
The prefix is controlled by the `topic_prefix` launch arg, exposed via the
`TOPIC_PREFIX` env var:

| `TOPIC_PREFIX` | Result | Example |
|---|---|---|
| _unset_ (default) | per-vehicle prefix | `/Copter1/odom` |
| `/` | **flat** — all topics at root | `/odom`, `/Imu` |

```bash
TOPIC_PREFIX=/ make ardupilot-xfs      # or px4-xfs / px4-condo / ardupilot-condo
```

For the multi-drone scenarios (`ardupilot-xfs`, `px4-xfs`) flat names don't
collide across the fleet because each drone runs on its own `ROS_DOMAIN_ID`
(distinct by default) — so `/odom` on drone 1 and drone 2 live on separate DDS
domains. Flat would only collide if you force every drone onto one domain.

> **Use `/`, not empty.** Compose passes the arg in exec-form (no shell), so an
> empty value sends the literal token `topic_prefix:=` and `ros2 launch` rejects
> it (`malformed launch argument`). `/` resolves to the same flat root and is a
> valid non-empty value. An accidental `TOPIC_PREFIX=` coerces back to the
> prefixed default rather than crash-looping the bridge.

**Sensor/camera topics need `config/airsim-bridge/topic_names.yaml`** (one
shared file, mounted by every scenario's bridge). The launch layer normally
enumerates sensor/camera topics by reading `settings.json` over RPC, but that
read fails in this bridge image — so `TOPIC_PREFIX=/` alone would flatten only
the fixed core topics (`odom`, `pose`, `global_gps`, …) and leave `/Copter1/Imu`,
`/Copter1/LidarSensor1/points`, cameras, etc. prefixed. That yaml lists each such
topic as an **identity rename**, which forces it into the remap set so
`topic_prefix` relocates it too. It is a **superset** across scenarios (entries
for a sensor a scenario doesn't run are harmless no-ops) — e.g. `Imu`/
`Magnetometer` (ardupilot only), `LidarSensor1/points`+`labels` (CPU LiDAR) vs
`gpulidar/pointsLidarSensor1` (GPU LiDAR). Keys must match the sensor/camera
names in that scenario's `config/unreal-airsim/*/settings-*.json`; add a key if
you introduce a new sensor/camera.

The same file also **renames** topics independently of the prefix: change a
value (e.g. `odom: odometry`) to expose `/…/odometry`. TF frame_ids are **never**
renamed — frames stay `Copter1/base_link`, `Copter1/odom` regardless of topic
prefix or renames.

### Fisheye cameras

A calibrated fisheye rig, independent of the pinhole `CAMERA_*` block
above — both can render at once. Enabling adds polynomial-distortion
fisheyes (`FisheyeModel 3`, with `Fx/Fy/Cx/Cy`, `K1..K4`, lens mask, and
per-camera chromatic aberration) to **every** vehicle. Same two-step
flow: flip the knob in `.env`, regenerate, restart the sim. Topics appear
as `/<vehicle>/Camera_N_Scene/image`.

| `.env` key | Default | Purpose |
|---|---|---|
| `FISHEYE_ENABLE` | `false` | set `true` to add the fisheye rig to every vehicle |
| `FISHEYE_COUNT` | `4` | `4` → surround ring (`Camera_0..3`, ±0.30 m offsets, ±45/±135° yaw); `1` → single forward `Camera_0` |
| `FISHEYE_WIDTH` | `618` | capture width in pixels |
| `FISHEYE_HEIGHT` | `516` | capture height in pixels |
| `FISHEYE_FOV` | `220` | field of view in degrees |

```env
# Single forward fisheye
FISHEYE_ENABLE=true
FISHEYE_COUNT=1
```

> **Note:** the rich per-camera schema (intrinsics, lens mask, seam
> blending, tonemap) is fixed in the shared cameras partial; only mount
> pose and chromatic aberration vary per ring position. A 4-fisheye ring ×
> N drones is heavy on GPU + AirSim's single-threaded RPC — drop
> resolution or use `FISHEYE_COUNT=1` if it bogs. Sim restart required
> after regenerating.

### Terrain origin / home position

Two **independent** knobs set "where on Earth the sim is," and they must
agree or the autopilot's EKF origin jumps when AirSim GPS arrives:

1. **AirSim `OriginGeopoint`** — maps Unreal world origin `(0,0,0)` to a real
   GPS coordinate. This is the authoritative GPS source at runtime. It is
   **hardcoded in the settings `*.json.j2` template** (e.g.
   `config/unreal-airsim/condo/templates/settings-px4.json.j2`), so edit
   the template and regenerate (`make generate`), **not** the rendered file.
   In `--editor` / `EDITOR=true` mode the containerized sim is skipped and
   AirSim reads the **host** `~/Documents/AirSim/settings.json` instead — set
   `OriginGeopoint` there for the editor level.

2. **Autopilot home (`.env`)** — PX4 / ArduPilot SITL carry their own home /
   EKF origin, separate from `OriginGeopoint`. These are **not** generator
   inputs; they are `${VAR:-default}` interpolated by compose at launch, so a
   change needs only a stack restart (no regen). **You must set them to match
   the `OriginGeopoint` of the terrain you fly** — otherwise home is wrong
   before GPS lock and the EKF origin jumps once AirSim's GPS streams in.

| `.env` key | Applies to | Purpose |
|---|---|---|
| `PX4_HOME_LAT` / `PX4_HOME_LON` / `PX4_HOME_ALT` | px4-* scenarios | PX4 SITL home / EKF origin |
| `ARDUPILOT_HOME_LAT` / `ARDUPILOT_HOME_LON` / `ARDUPILOT_HOME_ALT` | ardupilot-* scenarios | ArduPilot SITL home |

```env
# Match the terrain's OriginGeopoint (e.g. a Singapore terrain)
PX4_HOME_LAT=1.3738003
PX4_HOME_LON=103.677839
PX4_HOME_ALT=16.0
```

> **Note:** defaults ship as an Idaho test coordinate (`42.76 / -115.57`),
> **not** the Singapore terrains — always set the home trio when you change
> map or run `--editor` against a relocated level. If `setup-env.sh`
> generates a `compose/<scenario>/.env`, it shadows the root `.env`; set the
> home trio there too.

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
python3 tools/generate_scenario.py                          # render all 4 scenarios
python3 tools/generate_scenario.py --scenario px4-xfs       # render one scenario only
python3 tools/generate_scenario.py --check                  # exit 1 if drift; no write
python3 tools/generate_scenario.py --self-test              # invariants for N in {1,2,4,8,16}
```

Or via the Makefile:

```bash
make generate                    # all scenarios
make generate SCENARIO=px4-xfs   # one scenario
make check                       # drift check
make self-test                   # invariant suite
```

Dependencies (Python 3, `jinja2`, `python-dotenv`):

```bash
pip install -r tools/requirements.txt
```

For the per-scenario verification runbook (smoke-test sensors,
flight test, teardown), see
[`compose/ardupilot-xfs/README.md`](./compose/ardupilot-xfs/README.md).

---

## Running (`./launch.sh` / `make`)

```bash
./launch.sh                                  # default scenario from .env
./launch.sh ardupilot-xfs                    # explicit scenario
./launch.sh ardupilot-xfs --with-agent-external
./launch.sh ardupilot-xfs --headless --with-monitoring
./launch.sh px4-xfs --all
```

The root `Makefile` is a thin wrapper — flag vars translate to launch.sh flags:

```bash
make ardupilot-xfs                                      # basic launch
make ardupilot-xfs HEADLESS=true AGENT_EXTERNAL=true    # with flags
make px4-xfs PIXEL_STREAMING=true                       # UE5 signalling sidecar
make px4-condo ALL=true                                 # monitoring + metrics
make dev SCENARIO=px4-xfs                               # launch + tmux dashboard
make dev EDITOR=true                                    # run AirSim from the Unreal editor on host
make stop                                               # ./stop.sh
make logs                                               # ./logs.sh
make ps                                                 # docker ps formatted
```

| Flag / Make var | Effect | `.env` equivalent |
|---|---|---|
| `--with-monitoring` / `MONITORING=true` | Start `docker-compose-monitoring.yml` (Prometheus, Grafana, exporters, foxglove-bridge, Lichtblick) | `START_MONITORING=true` |
| `--with-metrics` / `METRICS=true` | Start `docker-compose-metrics.yml` (metrics-collector, mission-supervisor) | `START_METRICS=true` |
| `--all` / `ALL=true` | `--with-monitoring` + `--with-metrics` | both |
| `--headless` / `HEADLESS=true` | UE5 runs with `-RenderOffScreen` (cameras + PixelStreaming still work) | `AIRSIM_HEADLESS=true` |
| `--with-pixel-streaming` / `PIXEL_STREAMING=true` | Start UE5 signalling sidecar for browser streaming | `WITH_PIXEL_STREAMING=true` |
| `--with-agent-external` / `AGENT_EXTERNAL=true` | Also start per-drone `zenoh-bridge-{1..N}` on `agent_external` for `/shared/*` mesh | `WITH_AGENT_EXTERNAL=true` |
| `--editor` / `EDITOR=true` | Skip the containerized AirSim (`sim` profile off); run AirSim from the Unreal editor on the host instead. Launcher **waits for the editor RPC** (`localhost:41451`, i.e. Play the level) before starting bridges — they query RPC once with no retry (window: `EDITOR_RPC_TIMEOUT`, default 180s, `0` = forever). Set the editor's host `~/Documents/AirSim/settings.json` `OriginGeopoint` + matching `PX4_HOME_*`/`ARDUPILOT_HOME_*` (see Terrain origin) | `EDITOR_MODE=true` |

Running benchmark metrics (goal / exploration / teleop trigger modes, the
`/run_state` flow, and the `run_state_bridge`) → **`config/metrics-collector/README.md`**.
The monitoring & observability plane (Grafana/Prometheus/ES/Loki, endpoints, data flow,
ES disk gotcha) → **`config/grafana/README.md`**.

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
AIRSIM_BRIDGE_IMAGE=dhdevspace/auto_mns:airsim-ros2-bridge
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

## MAVLink ports & endpoints

The PX4 image's **mavlink-router** is the single hub between PX4 SITL, AirSim,
QGC, MAVROS, and any C2 client. Its config is generated **inside the image** at
container start from a baked template (`mavlink-router.conf.template`); the host
stack only has to dial the matching ports.

Per PX4 instance `i` (instance 0 = drone 1), host networking:

| Endpoint | Router mode | Port | Peer & direction | Configured where |
|---|---|---|---|---|
| `PX4_Local` | Server | `5760+i` | PX4 SITL → router | in-image |
| `AirSim` | Normal | `14540+i` | router → AirSim `ControlPortLocal` | AirSim `settings.json` |
| `AirSim_Inbound` | Server | `14580+i` | AirSim `ControlPortRemote` → router | AirSim `settings.json` |
| `QGC_UDP` | Normal | `14550` | router → QGC (UDP listen) | QGC `QGroundControl.ini` |
| `MAVROS_UDP` | **Server** | `14555+i` | **MAVROS dials in** | compose `fcu_url` / `*_FCU_URL` |
| `C2` | Normal | `14552` | router → C2 client | external C2 / extra QGC link |

`Normal` endpoints **send to** `MAVLINK_TARGET` (default `127.0.0.1`).
`Server` endpoints **bind and wait** — the peer must initiate.

### MAVROS — must dial the router

`MAVROS_UDP` is a **Server**, so the sidecar connects *out* to it:

```text
fcu_url:=udp://:0@127.0.0.1:14555      # instance 0; port is 14555+i per drone
```

This is the compose default. To override:

- single-drone (`px4-condo`): set `MAVROS_FCU_URL` in `.env`
- multi-drone (`px4-xfs`): set `DRONE_<N>_FCU_URL` in `.env`

Defaults are produced by `tools/generate_scenario.py` (`mavros_local = 14555+i`)
into each `compose/<scenario>/docker-compose.yml`. **Edit the `.j2` template
then `make generate`** — never the rendered file (`make check` fails on drift).

> ⚠️ Old images used MAVROS `Normal :14560` (router pushed, MAVROS bound
> passively). Current images use `Server :14555` (MAVROS dials). A stale
> `MAVROS_FCU_URL=udp://:14560@` silently fails to connect.

### C2 — command & control

`C2` is a dedicated GCS-style endpoint, separate from QGC, so a C2 client can
attach without sharing QGC's sink or forcing QGC closed during uploads. The
router **emits** to `${MAVLINK_TARGET}:14552`; **nothing in the default stack
consumes it.** To use it, point a C2 client — or an extra QGC UDP link — at
`127.0.0.1:14552`.

### Editor mode (`EDITOR=true`) caveat

AirSim then runs from the Unreal editor on the host, so it reads the **host**
`~/Documents/AirSim/settings.json` — *not* the container-mounted
`config/unreal-airsim/<scene>/settings*.json`. The host file's
`ControlPortLocal`/`ControlPortRemote` (14540/14580) must match the router for
AirSim↔PX4 HIL. MAVROS and C2 are router↔client and are unaffected by where
AirSim runs.

---

## Manual MAVROS smoke test (PX4 OFFBOARD)

Reference flow for driving a single PX4 vehicle from MAVROS CLI. The
target container depends on which scenario is up:

| Scenario | Where to run MAVROS commands |
|---|---|
| `ardupilot-xfs` | inside `mavros_d1` (started by `./compose/ardupilot-xfs/test-per-drone-mavros.sh`) |
| `px4-xfs` | inside `mavros_d1`..`mavros_d4` — one per drone, started by the scenario itself |
| `px4-condo` / `ardupilot-condo` | inside `mavros_d1` — started by the scenario itself |

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
│   ├── px4-condo/
│   │   ├── docker-compose.yml                  ← generated
│   │   └── templates/                          ← Jinja sources
│   ├── px4-xfs/
│   │   ├── docker-compose.yml                  ← generated
│   │   └── templates/                          ← Jinja sources
│   ├── ardupilot-condo/
│   │   ├── docker-compose.yml                  ← generated
│   │   └── templates/                          ← Jinja sources
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
│   ├── unreal-airsim/
│   │   ├── _partials/cameras.json.j2           ← shared camera Jinja partial
│   │   ├── condo/templates/                    ← Jinja for settings-px4/ardupilot.json
│   │   └── xfs/templates/                      ← Jinja for settings-ardupilot/px4.json
│   ├── zenoh/                                  ← per-drone bridge configs
│   └── CONFIG_README.md
├── tools/
│   ├── generate_scenario.py                    ← Jinja generator
│   ├── requirements.txt
│   └── README.md
├── docker-compose-monitoring.yml
├── docker-compose-metrics.yml
├── Makefile                                    ← convenience wrapper for launch.sh
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
- **All 4 scenario shapes live in `.env`.** Any changes to on-disk
  generated files (`docker-compose.yml`, `docker-compose.mavros-test.yml`,
  `settings-ardupilot.json`, `settings-px4.json`) will be overwritten by
  the generator on the next launch. Edit the template (`.j2` file) instead,
  then run `make generate` or just relaunch.
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
