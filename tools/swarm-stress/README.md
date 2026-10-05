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
| `stress.py` | Runs the test itself:<br>1. Waits for every vehicle to report ready.<br>2. Measures the sim's speed (sim seconds per wall second, from `/clock`), CPU, memory and GPU.<br>3. Takes every vehicle off at once.<br>4. Polls each one through the hover.<br>5. Lands them all.<br>The last line it prints is a JSON summary.<br>`POST_READY_CMD` runs once every vehicle is ready, before anything is measured; `{project}` is the stack's compose project. |
| `mavros_px4_slim.yaml`, `slim_mavros.py` | A MAVROS config that denies every plugin and allows only the twelve a flight stack uses, and a script that mounts it into every bridge of a generated stack. |
| `trim_streams.sh` | Lowers every PX4's onboard MAVLink stream rates at runtime (a swarm profile). |
| `formation.py` | Flies a launched PX4 or ArduPilot swarm in formation and brings it home, over MAVLink only. Every vehicle flies the same route as north,east offsets from its own start, so the spawn grid moves as one block; RETURN_TO_LAUNCH then lands each one at its start. All vehicles start at one shared instant. The last line it prints is a JSON summary. |
| `formation_agent.py` | One vehicle's part of `formation.py`, run inside its autopilot container (pymavlink ships there): mission upload, synchronized arm and start, follow to the landing. |

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

## Keeping MAVROS on, but slimmed (6 October 2026)

60 PX4 vehicles, the same machine and test. The sim speed is the median of the hover samples.

| 60 PX4 vehicles | MAVROS CPU per bridge (at 5 vehicles) | MAVLink messages/s into MAVROS | Sim speed (median, lowest) | Bridges' CPU | Load average | Memory |
|---|---|---|---|---|---|---|
| MAVROS as shipped | 31.6% | 672 | 0.55, 0.53 | 16.1 cores | 178 | 21.4 GB |
| Slim plugins (`slim_mavros.py`) | 12.4% | 672 | 0.92, 0.85 | 9.4 cores | 68 | 15.4 GB |
| Slim plugins and trimmed PX4 streams (`trim_streams.sh`) | 6.0% | 187 | 0.96, 0.95 | 9.3 cores | 69 | 15.5 GB |
| MAVROS off | none | none | 0.99, 0.84 | 6.1 cores | 58 | 8.0 GB |

All 60 vehicles were ready, took off, held and landed in every row. MAVROS still publishes state, IMU and local and global position with the slim plugins, and with trimmed streams local position arrives at 20 Hz instead of 100 Hz. The slim plugins do most of the work. Trimming PX4's streams mostly steadies the sim speed: the lowest sample went from 0.85 to 0.95. At 60 vehicles the bridges' CPU barely moves with trimming, because each bridge's remaining cost is MAVROS's fixed overhead and the bridge's own node.

The slim plugin list covers what flying needs:
- state, arming and modes (`sys_status`);
- `sys_time`;
- `command`;
- `imu`;
- local and global position;
- home position;
- altitude;
- the four setpoint plugins.

Anything that uses another plugin needs it added to the allowlist: missions (`waypoint`), parameters (`param`), RC override, the wind estimate and so on. The PX4 rates last until the PX4 container restarts. A permanent version would be a rate profile the generator writes into the PX4 startup script.

## Formation flight on XFS, 6 October 2026

`formation.py` flies the swarm as a block and brings it home, without the dashboard's flight controls (over MAVLink they offer only take off, hold, land and teleop):

```bash
tools/swarm-stress/formation.py px4-swarm-60-xfs-lean                                    # PX4
tools/swarm-stress/formation.py ardu-swarm-60-xfs-lean --route "0,60;40,60;40,0" --lead 45  # ArduPilot
```

- **Route.** By default: climb to 25 m, then 60 m west, 40 m north and 60 m east at 4 m/s, then RETURN_TO_LAUNCH. `--route "n,e;n,e;..."`, `--alt` and `--speed` change it.
- **How each vehicle is reached.** PX4 uses its router's MAVROS endpoint (UDP 14555), which a stack without MAVROS leaves free; the dashboard's flight controls keep the Control endpoint (14560). ArduPilot uses the SITL's TCP 5760 port, which MAVROS holds when it is on.
- **Arming.** PX4 arms in AUTO.MISSION. ArduPilot Copter will not arm in AUTO, so it arms in GUIDED, switches to AUTO and then gets MISSION_START.

The specs are `scenarios/px4-swarm-60-xfs-lean` and `scenarios/ardu-swarm-60-xfs-lean`. They were made from `stereo-xfs` with `make_spec.py --no-mavros`, with the cameras removed and the lock's `xfs-level` 1.0.2 pinned. The grid is 8 by 8 with 4 m spacing, starting at the container yard (423, -906, z -21) and running north and west.

| Run | Vehicles | Autopilot | Home after the route | Flight time per vehicle |
|---|---|---|---|---|
| 4 m grid by the containers, all at 25 m | 60 | ArduPilot | 60, but see below | 97 to 107 s |
| same | 60 | PX4 | 58; drones 47 and 55 spawned inside a container | not measured |
| 8 m grid in the clear, altitudes layered | 60 | ArduPilot | 60; no vehicle-vehicle or obstacle contact in the sim's events | 106 to 120 s |

What the first grid got wrong, from the simulator's events:
- **Spawned inside containers.** The 4 m grid at the container yard put drones 39, 47 and 55 inside a container stack. They collided with it from the first second.
- **Mid-air collisions.** Same-column pairs collided on the north/south legs: 1 and 9, 20 and 28, 7 and 15, 52 and 60, 37 and 45. Those pairs fly single file 4 m apart, and the drones started a second or more apart.
- **A drone through the ground.** After its mid-air collision, Copter1 landed without the sim registering ground contact. It kept descending at ArduPilot's 0.5 m/s landing speed through the terrain.

What fixed it:
- **Altitude by row.** `formation.py` flies each grid row 3 m higher (`--row-step`) and every other column 1.5 m higher (`--col-step`).
- **A clear grid.** The 60-vehicle ArduPilot spec now sits on an 8 m grid about 180 m west (x 422, y -1086). That site was found by scanning the level with AirSim's `simTestLineOfSightBetweenPoints`: every spot has 2 m of clearance, and every climb and leg was checked at its own altitude.
- **Spawn height per spot.** Each drone spawns 1.5 m above the ground measured under it.
- **The route for this grid** is `--route "0,60;40,60;40,0"` (east first).

The link and mode fixes:
- **ArduPilot TCP fallback.** ArduPilot SITL's TCP 5760 can keep a dead client in CLOSE_WAIT and stop serving. The agent then falls back to UDP 14552.
- **Modes and arming from the heartbeat.** Modes and arming are confirmed from the autopilot's heartbeat, not the COMMAND_ACK.
- **Landing.** A landing only counts after the route's last waypoint.

**Before flying elsewhere, check the start height.** The XFS start height (z -21, 2.3 m above the ground) was measured at the first vehicle's spot only, so check it before moving the grid onto uneven ground.

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
