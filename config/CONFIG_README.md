# Configuration Reference

This document describes the runtime configuration files used by the stack.

All main runtime configs live under `config/`.

---

## Coordinate Frame Note

Two important config files use different coordinate conventions:

- `config/unreal-airsim/condo/settings.json` uses **AirSim-side NED coordinates**
- `config/metrics-collector/mission.json` uses **ROS 2 ENU / map-frame coordinates**

### Quick reference

**NED**

- `+X` = North
- `+Y` = East
- `+Z` = Down

**ENU**

- `+X` = East
- `+Y` = North
- `+Z` = Up

### Example

A waypoint in `mission.json` such as:

```json
{ "x": -20.0, "y": 0.0, "z": 1.3 }
```

is in **ENU**, meaning:

- `x = -20.0` → 20 m West
- `y = 0.0` → no North/South offset
- `z = 1.3` → 1.3 m Up

The equivalent NED position would be:

- `x = 0.0`
- `y = -20.0`
- `z = -1.3`

When changing spawn locations or mission waypoints, make sure you are editing them in the correct frame.

---

## Config Layout

```text
config/
├── metrics-collector/
│   ├── evaluation.yaml
│   ├── evaluation.yaml.example
│   ├── mission.json
│   └── scenario_controller.yaml
├── qgroundcontrol/
│   ├── qgc_config/
│   └── user_config/
└── unreal-airsim/
    └── condo/
        ├── settings.json
        └── settings-template.json
```

---

## `config/metrics-collector/evaluation.yaml`

Defines the pass/fail rules used after each run.

Typical contents include:

- enabled metrics
- maximum path length
- maximum travel time
- goal reached requirement
- collision threshold
- optional trajectory metrics such as ATE / RPE

Use this file when you want to change how a run is evaluated.

Most commonly edited fields:

- `evaluation.metrics.*`
- `max_path_length_m`
- `max_travel_time_sec`
- `require_goal_reached`
- `max_collisions`
- `trajectory_metrics.*`

### Current example

- path length enabled
- travel time enabled
- goal reached required
- collisions enabled
- max path length: `500.0`
- max travel time: `300.0`
- max collisions: `0`
- trajectory metrics present but master `enabled: false`

---

## `config/metrics-collector/mission.json`

Defines the mission waypoints used for the run.

Typical contents include:

- `frame_id`
- ordered list of waypoints
- per-waypoint `x`, `y`, `z`
- optional `yaw_deg`

Use this file when you want to change the route flown during evaluation.

### Coordinate convention

`mission.json` waypoint coordinates are defined in the **ROS 2 ENU / map frame**.

This means:

- `+X` = East
- `+Y` = North
- `+Z` = Up

Most commonly edited fields:

- waypoint positions
- waypoint count
- altitude
- yaw at each waypoint

### Current example

- `frame_id: map`
- 5 waypoints
- alternating route between:
  - `(0.0, 8.9, 1.5)`
  - `(0.0, 0.0, 1.5)`

---

## `config/metrics-collector/scenario_controller.yaml`

Defines the scenario controller / supervisor behavior.

Typical contents include:

- vehicle name
- topic names
- odometry topic suffixes
- timeouts
- goal tolerance
- readiness checks
- auto start / shutdown behavior

Use this file when you want to change how the supervisor decides the run is ready, when it starts, and when it ends.

Most commonly edited fields:

- `vehicle`
- `connection_timeout`
- `telemetry_timeout`
- `telemetry_freshness_sec`
- `waypoint_timeout`
- `goal_tolerance`
- `auto_start_when_ready`
- `require_armed_before_start`
- `require_airborne_before_start`

### Current example

- vehicle: `Copter1`
- `connection_timeout: 120.0`
- `telemetry_timeout: 20.0`
- `telemetry_freshness_sec: 1.0`
- `goal_tolerance: 0.5`
- `auto_start_when_ready: true`
- `require_armed_before_start: true`
- `require_airborne_before_start: false`

---

## `config/unreal-airsim/condo/settings.json`

Defines the AirSim / Unreal runtime settings used by the simulation stack.

Typical contents include:

- API server port
- origin geopoint
- vehicle type
- spawn position
- PX4 connection ports
- enabled sensors
- sim clock / wind / time of day

Use this file when you want to change the simulated vehicle setup or AirSim-side runtime behavior.

### Coordinate convention

`settings.json` uses the **AirSim-side NED runtime frame** for positions such as vehicle spawn coordinates.

This means:

- `+X` = North
- `+Y` = East
- `+Z` = Down

So values here do **not** use the same coordinate convention as `mission.json`.

Most commonly edited fields:

- spawn location
- PX4 TCP / control ports
- enabled sensors
- lidar parameters
- vehicle type
- origin geopoint

### Current example

- `ApiServerPort: 41451`
- `SimMode: Multirotor`
- one vehicle: `Copter1`
- `VehicleType: PX4Multirotor`
- spawn:
  - `X: 10`
  - `Y: 0`
  - `Z: 1.3`
- PX4 TCP:
  - `UseTcp: true`
  - `TcpPort: 4560`
- control ports:
  - `ControlPortLocal: 14540`
  - `ControlPortRemote: 14580`
- enabled sensors:
  - Barometer
  - GPS
  - LidarSensor1

---

## `config/qgroundcontrol/`

Contains QGroundControl-related config for the runtime environment.

Use this only if QGC-specific behavior or user configuration needs to be updated.

This is typically edited less often than the metrics or mission configs.

---

## What to edit most often

For typical autonomy workflow, the most commonly edited files are:

- `config/metrics-collector/mission.json` — change the route
- `config/metrics-collector/scenario_controller.yaml` — change readiness / timeout behavior
- `config/metrics-collector/evaluation.yaml` — change pass/fail criteria

`config/unreal-airsim/condo/settings.json` usually changes less often and is mainly used when simulation-side setup needs to be updated.