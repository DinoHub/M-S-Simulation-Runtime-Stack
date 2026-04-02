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
./launch.sh
```

Stop everything:

```bash
./stop.sh
```

View logs:

```bash
./logs.sh
```

---

## Before You Start

After copying `.env.example` to `.env`, update the fields relevant to your setup.

### Minimum fields to check

```env
CONFIG_ROOT=./config
LOCAL_PLANNER_MODE=disabled
```

### Image tags to check

This stack uses prebuilt Docker images. Verify these tags point to the correct versions for your environment:

```env
ARDUPILOT_IMAGE=dhdevspace/auto_mns:ardupilot-latest
AIRSIM_IMAGE=dhdevspace/auto_mns:tevv-airsim-condo-latest-ceilingless
ROS2_IMAGE=dhdevspace/auto_mns:tevv-airstack-ros2-x11-node-release
PX4_IMAGE=dhdevspace/auto_mns:px4-airsim-px4
```

Update them if your team is using a different sim image, ROS2 image, PX4 image, or pinned version.

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
├── config/
│   ├── metrics-collector/
│   │   ├── evaluation.yaml
│   │   ├── evaluation.yaml.example
│   │   ├── mission.json
│   │   └── scenario_controller.yaml
│   ├── qgroundcontrol/
│   │   ├── qgc_config/
│   │   └── user_config/
│   └── unreal-airsim/
│       └── condo/
│           ├── settings.json
│           └── settings-template.json
├── docker-compose-sim.yml
├── docker-compose-monitoring.yml
├── docker-compose-metrics.yml
├── launch.sh
├── stop.sh
├── logs.sh
├── setup.sh
├── .env.example
├── README.md
└── CONFIG_README.md
```

---

## Notes

- This repository only orchestrates the runtime environment.
- Planner code should live in its own repository.
- Planner startup behavior is controlled through `.env`.
- Engineers typically use `LOCAL_PLANNER_MODE=external` during active development.
- PX4 OFFBOARD requires MAVROS to be connected and receiving continuous setpoints before mode switching.
- If the stack fails to start, first verify the Docker image tags in `.env`.