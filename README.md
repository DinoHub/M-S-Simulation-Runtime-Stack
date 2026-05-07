# M-S-Simulation-Runtime-Stack

This repository provides the runtime environment used by the autonomy team.

It launches the following components:

- Simulation runtime
- Optional local planner
- Metrics collection / supervisor
- Monitoring stack

This repo **does not contain the planner implementation**.

---

## Quick Start

```bash
cp .env.example .env
./setup.sh
./launch.sh                                 # sim only, default scenario (px4-condo)
./launch.sh ardupilot-condo                 # sim only, alternative scenario
./launch.sh ardupilot-xfs --with-monitoring # sim + monitoring stack
./launch.sh px4-xfs --all                   # sim + monitoring + metrics
```

By default `./launch.sh` starts only the simulation. Add cross-cutting stacks with:

| Flag | Effect |
|------|--------|
| `--with-monitoring` | Also start `docker-compose-monitoring.yml` (Prometheus, Grafana, Elasticsearch, exporters, foxglove-bridge, Lichtblick) |
| `--with-metrics` | Also start `docker-compose-metrics.yml` (metrics-collector, mission-supervisor) |
| `--all` | Shortcut for both |

You can also persist these in `.env` via `START_MONITORING=true` / `START_METRICS=true`.

Stop everything (always tears down monitoring + metrics + scenario):

```bash
./stop.sh
./stop.sh ardupilot-condo
```

View logs:

```bash
./logs.sh
```

---

## Scenarios

The runtime ships with four simulation scenarios under `compose/<scenario>/docker-compose.yml`. Pick one with the `SCENARIO` env var or by passing it as the first arg to `launch.sh` / `stop.sh`.

| Scenario | Autopilot | Drones | Scene | Notes |
|----------|-----------|--------|-------|-------|
| `px4-condo` (default) | PX4 SITL | 1 | AirSim Condo | Includes pixel-streaming-signalling for browser viewing |
| `px4-xfs` | PX4 SITL | 4 | AirSim XFS | Multi-drone swarm, MAVLink router, host networking |
| `ardupilot-condo` | ArduPilot SITL | 1 | AirSim Condo | MAVROS over UDP `:14550` |
| `ardupilot-xfs` | ArduPilot SITL | 4 | AirSim XFS | Multi-drone swarm, AirSim opt-in via `--profile containerized-airsim` |

Cross-cutting stacks (run alongside any scenario):

- `docker-compose-monitoring.yml` — Prometheus, Grafana, Elasticsearch, exporters, foxglove-bridge, Lichtblick
- `docker-compose-metrics.yml` — metrics-collector + mission-supervisor

---

## Before You Start

After copying `.env.example` to `.env`, update the fields relevant to your setup.

### Minimum fields to check

```env
SCENARIO=px4-condo
CONFIG_ROOT=./config
LOCAL_PLANNER_MODE=disabled
```

### Image tags to check

This stack uses prebuilt Docker images. Each scenario's `compose/<scenario>/docker-compose.yml` ships a sensible default image for every service — you only need to override an image if your team has pinned a different version.

The relevant env-var overrides:

```env
ARDUPILOT_IMAGE=dhdevspace/auto_mns:ardupilot-slim
AIRSIM_IMAGE=dhdevspace/auto_mns:tevv-airsim-condo-latest-ceilingless
ROS2_IMAGE=...                # see scenario notes below — leave unset to use scenario default
PX4_IMAGE=dhdevspace/auto_mns:px4-airsim-px4
```

**Important: `ROS2_IMAGE` is global, but each scenario's compose default is different.**

| Scenario | Compose default `ROS2_IMAGE` |
|----------|-------------------------------|r
| `px4-condo` | (no default — `${ROS2_IMAGE:?set ROS2_IMAGE}`, you must set it) |
| `px4-xfs` | `tevv-airstack-ros2-x11-node-development` |
| `ardupilot-condo` | `tevv-airstack-ros2-x11-node-development` |
| `ardupilot-xfs` | `tevv-airstack-ros2-multi-vehicle-gt` (multi-vehicle GT-registration fix baked in) |

Setting `ROS2_IMAGE=…` in `.env` overrides **all** scenarios. Recommended: leave it unset and let each scenario use its own default. If you need `px4-condo`, set it just for that run via `ROS2_IMAGE=… ./launch.sh px4-condo`.

### Running `ardupilot-xfs` (multi-vehicle ArduPilot)

Multi-vehicle ArduPilot needs two specific images that differ from the older defaults — please confirm both before launching:

```env
# Required for multi-drone SERIAL port offsetting (5760 + 10*N).
# The older :ardupilot-latest tag is missing the run_ardupilot_airsim.sh
# offset script; all four drones will fight over port 5762 and three exit.
ARDUPILOT_IMAGE=dhdevspace/auto_mns:ardupilot-slim

# Required for multi-vehicle TF point-cloud registration.
# The older :tevv-airstack-ros2-fix-mavros-sysid tag has
# use_ground_truth_registration=False, which silently drops vehicles 2..N
# because their map->odom dynamic TFs miss canTransform's 20ms window.
# The :multi-vehicle-gt tag flips this to True (GT mode bypasses TF).
ROS2_IMAGE=dhdevspace/auto_mns:tevv-airstack-ros2-multi-vehicle-gt
```

Quick sanity check for a colleague picking this up fresh:

```bash
./launch.sh ardupilot-xfs
docker ps --filter name=ardupilot-xfs --format 'table {{.Names}}\t{{.Status}}'
docker ps --filter name=airsim_bridge_d --format 'table {{.Names}}\t{{.Status}}'
# Expect (per-drone default flow):
#   ardupilot-xfs-{drone-{0..3}, airsim, qgc, pixel-streaming-signalling,
#                  zenoh-bridge-{1..4}}
#   airsim_bridge_d{1..4}
# If you only see drone-2, ARDUPILOT_IMAGE is wrong (slim missing).
# If bridges crash-loop on X11, check XAUTHORITY is set and `xhost +local:docker`.
# Pass --legacy-bridge for the old ros2-x11-node + sim-router topology.
```

`ardupilot-xfs` defaults to the **per-drone bridge architecture**: four `airsim_bridge_dN` containers (one per Copter) plus four `zenoh-bridge-N` (one per `agent_internal-N` onto `agent_external`). Pass `--legacy-bridge` to fall back to the older single `ros2-x11-node` + `sim-router` stack. See [`compose/ardupilot-xfs/README.md`](./compose/ardupilot-xfs/README.md) for the workflow, validation tool, and the `DRONE_N_DOMAIN_ID` knobs for autonomy-team domain alignment.

### If the planner is started separately

Use this during active planner development when you already run the planner from its own repo:

```env
LOCAL_PLANNER_MODE=external
```

### If this runtime stack should start the planner

Update these fields:

```env
LOCAL_PLANNER_MODE=managed-script
LOCAL_PLANNER_DIR=/path/to/planner-repo
LOCAL_PLANNER_START_CMD="./run.sh"
LOCAL_PLANNER_STOP_CMD="make stop"
```

### If the planner has its own Docker Compose stack

Update these fields:

```env
LOCAL_PLANNER_MODE=managed-compose
LOCAL_PLANNER_DIR=/path/to/planner-repo
LOCAL_PLANNER_COMPOSE_FILE=docker-compose.yml
LOCAL_PLANNER_PROFILE=
```

For configuration file details, see [CONFIG_README.md](./CONFIG_README.md).

---

## Typical Workflow

Autonomy engineers usually work in one of two ways.

### Option 1 — Planner started separately (recommended during development)

Run your planner from its own repository.

Then set in `.env`:

```env
LOCAL_PLANNER_MODE=external
```

Start the runtime stack:

```bash
./launch.sh
```

### Option 2 — Planner started by this runtime stack

If your planner repo has a startup script, this repo can launch it automatically.

Set in `.env`:

```env
LOCAL_PLANNER_MODE=managed-script
LOCAL_PLANNER_DIR=/path/to/planner-repo
LOCAL_PLANNER_START_CMD="./run.sh"
LOCAL_PLANNER_STOP_CMD="make stop"
```

Then run:

```bash
./launch.sh
```

---

## Local Planner Modes

Planner behavior is controlled using `LOCAL_PLANNER_MODE` in `.env`.

| Mode | Description | What to update in `.env` |
|------|-------------|--------------------------|
| `disabled` | No planner will be started | `LOCAL_PLANNER_MODE=disabled` |
| `external` | Planner is assumed to already be running | `LOCAL_PLANNER_MODE=external` |
| `managed-script` | Planner started using a script in its repository | `LOCAL_PLANNER_MODE`, `LOCAL_PLANNER_DIR`, `LOCAL_PLANNER_START_CMD`, `LOCAL_PLANNER_STOP_CMD` |
| `managed-compose` | Planner started using its own Docker Compose stack | `LOCAL_PLANNER_MODE`, `LOCAL_PLANNER_DIR`, `LOCAL_PLANNER_COMPOSE_FILE`, optionally `LOCAL_PLANNER_PROFILE` |

---

## Example `.env`

Most engineers only need to modify these fields.

```env
ARDUPILOT_IMAGE=dhdevspace/auto_mns:ardupilot-latest
AIRSIM_IMAGE=dhdevspace/auto_mns:tevv-airsim-condo-latest-ceilingless
ROS2_IMAGE=dhdevspace/auto_mns:tevv-airstack-ros2-x11-node-release
PX4_IMAGE=dhdevspace/auto_mns:px4-airsim-px4

CONFIG_ROOT=./config

LOCAL_PLANNER_MODE=disabled
```

### Example `.env` for managed planner

```env
ARDUPILOT_IMAGE=dhdevspace/auto_mns:ardupilot-latest
AIRSIM_IMAGE=dhdevspace/auto_mns:tevv-airsim-condo-latest-ceilingless
ROS2_IMAGE=dhdevspace/auto_mns:tevv-airstack-ros2-x11-node-release
PX4_IMAGE=dhdevspace/auto_mns:px4-airsim-px4

CONFIG_ROOT=./config

LOCAL_PLANNER_MODE=managed-script
LOCAL_PLANNER_DIR=/home/mnsdemo01/Downloads/super_planner/docker/TEVV_docker
LOCAL_PLANNER_START_CMD="./run.sh"
LOCAL_PLANNER_STOP_CMD="make stop"
```

---

## PX4 Workflow Example

This section shows a common manual PX4 + MAVROS workflow for testing after the stack is up.

### 1. Enter the ROS2 container

```bash
docker exec -it ros2-x11-node bash
```

### 2. Confirm MAVROS is connected

```bash
ros2 topic echo --once /Copter1/mavros/state
```

You should see:

- `connected: true`

If `connected: false`, OFFBOARD control will not work.

### 3. Start streaming position setpoints

PX4 requires a continuous stream of offboard setpoints before switching to `OFFBOARD`.

```bash
ros2 topic pub -r 10 /Copter1/mavros/setpoint_position/local geometry_msgs/msg/PoseStamped "
header:
  frame_id: 'map'
pose:
  position:
    x: 0.0
    y: 0.0
    z: 3.0
  orientation:
    w: 1.0
"
```

### 4. Switch to OFFBOARD mode

In a second terminal inside the ROS2 container:

```bash
ros2 service call /Copter1/mavros/set_mode mavros_msgs/srv/SetMode "{base_mode: 0, custom_mode: 'OFFBOARD'}"
```

### 5. Arm the vehicle

```bash
ros2 service call /Copter1/mavros/cmd/arming mavros_msgs/srv/CommandBool "{value: true}"
```

### 6. Verify state

```bash
ros2 topic echo --once /Copter1/mavros/state
```

You want to see:

- `connected: true`
- `mode: OFFBOARD`

### 7. Useful debug commands

Check status text from PX4:

```bash
ros2 topic echo /Copter1/mavros/statustext/recv
```

Check whether setpoints are actually being published:

```bash
ros2 topic hz /Copter1/mavros/setpoint_position/local
```

### Raw velocity setpoint example

If you want to use raw local velocity setpoints instead of position setpoints:

```bash
ros2 topic pub -r 10 /Copter1/mavros/setpoint_raw/local mavros_msgs/msg/PositionTarget "
coordinate_frame: 1
type_mask: 1479
position:
  x: 0.0
  y: 0.0
  z: 0.0
velocity:
  x: 1.0
  y: 0.0
  z: 0.0
acceleration_or_force:
  x: 0.0
  y: 0.0
  z: 0.0
yaw: 0.0
yaw_rate: 0.0
"
```

---

## Commands

Start runtime stack:

```bash
./launch.sh
```

Stop runtime stack:

```bash
./stop.sh
```

View logs from all services:

```bash
./logs.sh
```

View logs from a specific stack:

```bash
./logs.sh sim
./logs.sh monitoring
./logs.sh metrics
```

View logs from a specific service:

```bash
./logs.sh sim ros2-x11-node
./logs.sh metrics metrics-collector
```

Filter logs by text:

```bash
./logs.sh metrics metrics-collector ERROR
```

---

## Repository Structure

```text
sim-runtime-stack/
├── compose/
│   ├── px4-condo/docker-compose.yml         # default — PX4 + AirSim Condo
│   ├── px4-xfs/docker-compose.yml           # PX4 x4 + AirSim XFS swarm
│   ├── ardupilot-condo/docker-compose.yml   # ArduPilot + AirSim Condo
│   └── ardupilot-xfs/                       # ArduPilot x4 + AirSim XFS swarm
│       ├── docker-compose.yml
│       ├── patches/                          # mounted into ros2-x11-node
│       └── scripts/                          # MAVROS demo scripts
├── config/
│   ├── ardupilot/config/
│   ├── experiments/
│   ├── metrics-collector/
│   ├── qgroundcontrol/{qgc_config,user_config}/
│   └── unreal-airsim/{condo,xfs}/
├── docker-compose-monitoring.yml
├── docker-compose-metrics.yml
├── launch.sh                                # ./launch.sh [scenario]
├── stop.sh                                  # ./stop.sh   [scenario]
├── logs.sh
├── setup.sh
├── .env.example
├── README.md
└── config/CONFIG_README.md
```

---

## Notes

- This repository only orchestrates the runtime environment.
- Planner code should live in its own repository.
- Planner startup behavior is controlled through `.env`.
- Engineers typically use `LOCAL_PLANNER_MODE=external` during active development.
- PX4 OFFBOARD requires MAVROS to be connected and receiving continuous setpoints before mode switching.
- If the stack fails to start, first verify the Docker image tags in `.env`.