# Swarm stress test

This test measures how many PX4 or ArduPilot vehicles one machine flies stably.

The test runs entirely through the dashboard:
- the spec is imported in the Author step;
- the stack is generated and launched by the dashboard;
- every vehicle is flown with the dashboard's flight controls (auto-takeoff, status, auto-land), which reach each autopilot over its MAVLink link.

| File | Does |
|---|---|
| `make_spec.py` | Writes `scenarios/<name>/ScenarioSpec.yaml` with N copies of a scenario's first vehicle on a grid, for PX4 or ArduPilot, with MAVROS on or off (`--no-mavros`). |
| `run_level.sh` | One level of the test: generates the scenario's stack, launches it, runs `stress.py` and stops the stack. |
| `stress.py` | Runs the test itself:<br>1. Waits for every vehicle to report ready.<br>2. Measures the sim's speed (sim seconds per wall second, from `/clock`), CPU, memory and GPU.<br>3. Takes every vehicle off at once.<br>4. Polls each one through the hover.<br>5. Lands them all.<br>The last line it prints is a JSON summary. |

```bash
python3 tools/swarm-stress/make_spec.py scenarios/demo-blocks/ScenarioSpec.yaml 60 --autopilot px4 --no-mavros
# import scenarios/px4-swarm-60-lean in the Author step (or POST /api/scenario/import), then:
tools/swarm-stress/run_level.sh px4-swarm-60-lean 60 120
```

The scenarios flown below are committed as `scenarios/px4-swarm-{5,10,20,30,60}`, `px4-swarm-60-lean` and `ardu-swarm-{5,60}`, `ardu-swarm-60-lean`. All of them use the blocks level with vehicles 4 m apart.

## Results, 5 October 2026

**Machine and setup:** 24 threads, 62 GB of RAM, RTX 5080, with the dashboard and metrics services running alongside. Each run hovered every vehicle for 2 minutes. Images: `mns-stacks` services.34 and dashboard live.24.

| Vehicles | Autopilot | MAVROS | Ready after | Took off | Airborne through the hover | Landed | Sim speed (x real time) | CPU used | Load average | Memory |
|---|---|---|---|---|---|---|---|---|---|---|
| 5 | PX4 | on | 1 s | 5 | 5 | 5 | 1.00 | 6 cores | 7 | 2.4 GB |
| 10 | PX4 | on | 48 s | 10 | 10 | 10 | 1.00 | 10.5 cores | 13 | 4.0 GB |
| 20 | PX4 | on | 41 s | 20 | 20 | 20 | 1.00 | 18 cores | 25 | 7.6 GB |
| 30 | PX4 | on | 60 s | 30 | 30 | 30 | 0.93 (lowest 0.75) | 22 cores | 55 to 110 | 11.7 GB |
| 60 | PX4 | on | 97 s | 60 | 60 | 60 | 0.56 | 21 cores | 140 to 178 | 23 GB |
| 60 | PX4 | off | 55 s | 60 | 60 | 60 | 0.98 (lowest 0.84) | 17 cores | 55 | 7.7 GB |
| 5 | ArduPilot | on | 72 s | 5 | 5 | 5 | not measured | 9 cores | 15 | 2.5 GB |
| 60 | ArduPilot | on | 130 s | 60 | 60 | 52 within 2 min, the rest still descending | 0.77 (lowest 0.60) | 20 cores | 133 to 146 | 20 GB |
| 60 | ArduPilot | off | 98 s | 60 | 60 | 60 in 11 s | 0.98 | 16 cores | 69 | 7.6 GB |

How to read these numbers:
- **Lockstep.** Both autopilots run in lockstep with AirSim (`LockStep: true`, `SteppableClock`). An overloaded machine therefore slows the simulation; it does not drop vehicles. No vehicle fell out of its flight mode in any run.
- **The real-time ceiling.** With MAVROS on, it is about 20 vehicles on this machine. With MAVROS off, 60 PX4 or 60 ArduPilot vehicles stay at real time.
- **ArduPilot's extra sim cost.** ArduPilot costs the simulator more than PX4: about 7 cores against about 4 at 60 vehicles.

## Where the CPU goes

Per vehicle, measured on the 5-vehicle PX4 stack:

| Process | CPU |
|---|---|
| MAVROS, inside the AirSim bridge | about 0.3 core |
| The bridge's own multirotor node | about 0.06 core |
| PX4 SITL | about 0.1 core |
| ArduPilot SITL | about 0.05 core |

MAVROS takes in about 670 MAVLink messages a second from PX4. It republishes:

| MAVROS topics | Rate |
|---|---|
| IMU, local position, odometry, velocity | 100 Hz |
| Altitude, global position | 50 Hz |
| GPS raw | 20 Hz |
| Magnetometer | 9 Hz |
| Static pressure | 13 Hz |
| State, battery | 1 Hz |

The AirSim bridge's own topics:

| Bridge topics | Rate |
|---|---|
| IMU | 200 Hz |
| GPS fix, barometer, magnetometer, ground truth odometry, pose | 50 Hz |
| `/clock` | about 47 Hz |
| `/tf` | about 71 Hz |

`runtime.features.mavros: false` is the swarm setting. Anything that needs the autopilot's ROS topics needs MAVROS: an autonomy stack, or the test plan's pilot. The dashboard's flight controls do not.

## Found on the way (fixed in mns-stacks)

1. **Docker address pools ran out.** Every vehicle's `agent_internal` network took a subnet from the Docker daemon's default pools. Those pools hold about 31 networks, shared with every other project, so a 20-vehicle stack failed with "all predefined address pools have been fully subnetted". Each vehicle now gets its own subnet: a /24 under one /16 per stack, in 10.200 to 10.249.
2. **Viewer ports collided.** Vehicle viewers were published on 8765 + index - 1. Vehicle 3 then landed on 8767 (the dashboard Replay's bridge) and vehicle 6 on 8770 (the metrics service), and those viewers stayed in Created. Vehicle viewers now skip 8764, 8767 and 8770.
3. **The sim lost its address.** With 60 ArduPilot SITL containers on the sim network, Docker gave the sim's static address (.10) to one of them, and the sim failed with "Address already in use". The sim network now hands out dynamic addresses only from its upper half.

## Still open

- **Silent launch failure.** The dashboard's launch answers "running" when the stack failed to start. Check that `stress.py` finds the stack's sim container.
- **Monitor's vehicle list goes empty** while the backend is short of CPU (load above about 50).
- **A port overlap from vehicle 41 up.** AirSim's per-vehicle `ControlPortLocal` (14540 + index) reaches the shared `ControlPortRemote` 14580 at vehicle 41. It was harmless at 60 vehicles.
