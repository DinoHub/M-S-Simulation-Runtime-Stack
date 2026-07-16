# ardupilot-xfs scenario

`NUM_DRONES` ArduPilot SITL drones + AirSim XFS + per-drone ROS2
bridges (one per `agent_internal-N` docker network). Optionally pair
with the autonomy team's mesh on `agent_external`.

The production `docker-compose.yml`, `docker-compose.mavros-test.yml`,
and `config/unreal-airsim/xfs/settings-ardupilot.json` are **generated**
from Jinja templates by `tools/generate_scenario.py`. The single source
of truth is the runtime-stack root `.env` (`NUM_DRONES`,
`VEHICLE_PREFIX`, port bases, etc.). `./launch.sh ardupilot-xfs`
auto-regenerates if drift is detected.

## Modes

| Mode | Command | When to use | Persona |
|---|---|---|---|
| **Solo dev/test** | `./launch.sh ardupilot-xfs` | Bring up sim only. No autonomy team running. | Dev |
| **Autonomy integration** | `./launch.sh ardupilot-xfs --with-agent-external` | Pair with autonomy_stack-N on agent_external (the team's compose creates that network). | Autonomy |

Other useful flags: `--headless` (off-screen UE5), `--with-pixel-streaming`
(browser viewer at http://localhost:80 via the WebRTC signalling sidecar —
default OFF since most runs don't open the browser), `--with-monitoring`,
`--with-metrics`, `--all`.

## What `./launch.sh ardupilot-xfs` actually brings up

Default flow (no flags) is an `N=NUM_DRONES` stack. Containers (with
`NUM_DRONES=4`, the shipped default — eleven in total):

| Container(s) | Count | Purpose | Network |
|---|---|---|---|
| `ardupilot-xfs-airsim` | 1 | UE5 + AirSim plugin (Xfs map) | host |
| `ardupilot-xfs-drone-{0..N-1}` | N | ArduCopter SITL, one per drone | host |
| `airsim_bridge_d{1..N}` | N | Per-drone AirSim → ROS 2 bridge | agent_internal-{1..N} |
| `ardupilot-xfs-qgc` | 1 | QGroundControl viewer | host |

Add `--with-agent-external` to also start N× `zenoh-bridge-{1..N}` on
agent_internal-N + agent_external.

Add `--with-pixel-streaming` to also start
`ardupilot-xfs-pixel-streaming-signalling` (WebRTC signalling sidecar; UE5
dials it via `-PixelStreamingURL`; browser viewer at http://localhost:80).
Default off — the sidecar's WebRTC encode pipeline isn't free and most
runs don't open the browser.

### Boot sequence

`docker compose` starts everything in parallel; `depends_on` and healthchecks impose the actual ordering. Approximate timing on a warm host (numbers from `start_period:` values + observed cold starts):

```
T=0s    docker compose up -d
        │
        ├─► signalling                (start_period 20s)
        │       │
        │       └─ healthy ──┐
        │                    ▼
        ├─► airsim-xfs    waits for signalling healthy|release controls the archive format (-Compressed) but secretly hard-codes -clientconfig=Development regardless. Asking for "release" gave a Development binary in a compressed pak. Shipping was unreachable from the surface.


        │       │              ↓ then UE5 cold-start (~5–30s)
        │       └─ healthy when nc -z 41451 passes (RPC port open)
        │                    │
        │                    ▼
        │       N× airsim_bridge_dN   waits for airsim-xfs healthy
        │                              ↓ ~6s ROS 2 launch + RPC connect
        │                              └─ healthy → /CopterN/* publishing
        │
        ├─► N× ardupilot-drone-{0..N-1}  start in parallel, no deps
        │       └─ healthy at T~90–120s (SITL warmup)
        │
        └─► qgc           start_period 15s, no deps
```

End-to-end (warm cache): `/Copter1/registered_point_cloud` publishing within ~30–40s of `./launch.sh`. Cold UE5 cache adds 15–30s of shader compile to `airsim-xfs healthy`.

The `airsim_bridge_d*` services intentionally gate on `airsim-xfs: condition: service_healthy` in the template.
Earlier `service_started` left the bridge to race AirSim's RPC port and silently land with zero sensor publishers — see Troubleshooting under "For dev" if you ever see `Publisher count: 0`.

### Per-drone port plan

| Drone | Vehicle | INSTANCE_NUM | MAVLink TCP | FDM TCP | FDM UDP |
|-------|---------|--------------|-------------|---------|---------|
|   1   | Copter1 |       0      |     5760    |   9002  |   9003  |
|   2   | Copter2 |       1      |     5770    |   9012  |   9013  |
|   3   | Copter3 |       2      |     5780    |   9022  |   9023  |
|   4   | Copter4 |       3      |     5790    |   9032  |   9033  |

Pattern: `5760 + 10*INSTANCE_NUM` for MAVLink, `9002 + 10*INSTANCE_NUM`
for FDM TCP, `+1` for FDM UDP. SYSID_THISMAV = INSTANCE_NUM + 1.

## For autonomy (integrating)

Once the runtime stack is up with `--with-agent-external`, your
`autonomy_stack-N` containers attach to the same `agent_internal-N`
docker network as `airsim_bridge_dN` and consume:

- **Sensor + state topics** under `/CopterN/*` (lidar, IMU, barometer,
  magnetometer, registered point cloud) — published by `airsim_bridge_dN`
  reading AirSim's RPC.
- **`/shared/*` topics** routed in/out via the per-drone `zenoh-bridge-N`
  on agent_external. Topic ownership (asymmetric, sim side):
  - sim publishes `/shared/sim/*` and `/shared/swarm/*`
  - sim subscribes `/shared/drone_N/*` (drone-owned)

You don't talk to AirSim's RPC directly. The bridge does that for you.

### Network ownership contract

`agent_internal-{1..N}` is **co-owned** by the sim stack and the
autonomy stack. Both compose files attach to the same docker network,
but only one creates it. Five rules to keep the contract clean:

- **Both compose files MUST declare each network with `external:
  true, name: agent_internal-N`.** Without `name:`, docker prefixes
  the network with the compose project name (`<project>_agent_internal-N`),
  and the two teams end up on parallel networks that look the same
  but aren't connected. Silent split-brain — docker doesn't warn
  you. Sim's compose follows this convention; verify autonomy's does
  too.
- **Whoever runs first creates the network; the other side
  attaches.** `launch.sh:ensure_agent_internal_networks()` is
  idempotent: if the network exists it just logs the actual subnet
  and skips creation. So either start order works.
- **Subnet authority: whoever creates first wins.** Sim's default is
  `${AGENT_INTERNAL_SUBNET_BASE}.${n}.0/24` (`172.28.${n}.0/24`).
  If autonomy creates with a different prefix, sim's launch logs
  `agent_internal-1: 172.20.1.0/24 (expected 172.28.1.0/24) — using
  existing (likely autonomy-owned)` and continues. **DDS uses
  container hostnames over docker DNS, not IPs**, so the subnet
  difference is cosmetic — sensor topics still flow.
- **`agent_external` is autonomy-owned. Sim never creates it.** The
  zenoh bridges' `external: true` declaration on `agent_external`
  fails fast if autonomy isn't up — that's intentional. For solo
  sim testing without the autonomy compose, pre-create it manually:
  ```bash
  docker network create agent_external \
    --subnet=172.28.0.0/24 --gateway=172.28.0.254
  ```
- **Subnet collision is the one real failure mode.** If autonomy's
  network already occupies `172.28.0.0/16` for some other purpose
  and sim tries to create at the same prefix, `docker network
  create` errors. The launcher will surface that error directly;
  override `AGENT_INTERNAL_SUBNET_BASE` in `.env` to a non-colliding
  prefix.

### The few env knobs you actually flip

Edit the **runtime-stack root** `.env` (NOT
`compose/ardupilot-xfs/.env` — that's vestigial and not auto-loaded).
Full reference is in `<repo-root>/.env.example`. The keys you most
often touch:

| Key | Default | Purpose |
|---|---|---|
| `DRONE_{1..4}_DOMAIN_ID` | `1..4` | Per-drone ROS_DOMAIN_ID. Set all four to a single value (e.g. `20`) if your team aligns on a flat domain. |
| `LOCAL_OBS_TARGET_FRAME` | `map` | `target_frame` for per-vehicle `pointcloud_registration_node`. Use `base_link` for per-drone REP-105 (`{vehicle}/base_link`); any other string passes through literally. |
| `LOCAL_OBS_BUFFER_SEC` | `5.0` | Rolling-buffer length (s) per vehicle. Bigger = denser map, more CPU. The publish callback re-voxelizes the full buffer each tick, so this scales linearly. |
| `LOCAL_OBS_VOXEL_SIZE` | `0.10` | Voxel leaf size (m). Smaller = denser map, more CPU. Defaults pair with `LOCAL_OBS_BUFFER_SEC=5.0` for ~20 Hz publish. |
| `VEHICLE_{1..4}_NAME` | `Copter{1..4}` | Override per-drone vehicle key in `settings.json`. |
| `AIRSIM_BRIDGE_IMAGE` | `dhdevspace/auto_mns:airsim-ros2-bridge` | Pin a different bridge image for testing. |

The previous defaults (`LOCAL_OBS_BUFFER_SEC=30`, `LOCAL_OBS_VOXEL_SIZE=0.05`)
measured ~1.7 Hz on the per-drone bridges — every publish tick re-merges +
voxel-filters the whole buffer (~1500 scans at 50 Hz lidar input) under a
single mutex shared with the input callback. The retuned defaults trade map
density for ~20 Hz responsiveness; flip them back via the env vars above if
you need the denser cloud and have the CPU headroom.

Example for flat-domain + base_link mode:

```bash
# In <repo-root>/.env
LOCAL_OBS_TARGET_FRAME=base_link
DRONE_1_DOMAIN_ID=20
DRONE_2_DOMAIN_ID=20
DRONE_3_DOMAIN_ID=20
DRONE_4_DOMAIN_ID=20
```

### Verify your integration

From inside any container attached to `agent_internal-1` and on the
matching `ROS_DOMAIN_ID`:

```bash
ros2 topic list | grep ^/Copter1/
ros2 topic echo --once --qos-reliability best_effort /Copter1/Imu
```

For full sensor + flight validation, run `./test-per-drone-mavros.sh`
from `compose/ardupilot-xfs/` (real flight per drone — see the
"For dev" section).

## For dev (running solo)

### Bring up

```bash
# from runtime-stack root
./launch.sh ardupilot-xfs

docker ps --format '{{.Names}} {{.Status}}' | grep -E 'ardupilot-xfs|airsim_bridge_d'
# Expect (default): ardupilot-xfs-{drone-{0..3}, airsim, qgc, pixel-streaming-signalling}
#                   airsim_bridge_d{1..4}
# (No zenoh-bridge-* unless --with-agent-external.)
```

### Validate

From `compose/ardupilot-xfs/`:

| Tool | Validates | Touches the FCU? |
|---|---|---|
| `./test-per-drone-mavros.sh` | Full MAVROS chain: arm, GUIDED, takeoff, setpoint, land — per drone, in isolated containers on `agent_internal-N`. Reads `NUM_DRONES` from root `.env`. | **Yes — flies the drones.** |

Sensor smoke-test from inside any bridge container:

```bash
docker exec airsim_bridge_d1 bash -lc \
  'source /airsim_ros2_ws/install/setup.bash && \
   timeout 5 ros2 topic hz /Copter1/registered_point_cloud'
```

For autonomy alignment on a flat domain, set the matching env BEFORE
running either tool:

```bash
export DRONE_1_DOMAIN_ID=20 DRONE_2_DOMAIN_ID=20 \
       DRONE_3_DOMAIN_ID=20 DRONE_4_DOMAIN_ID=20 \
       BRIDGE_TEST_D1_DOMAIN_ID=20 BRIDGE_TEST_D2_DOMAIN_ID=20 \
       BRIDGE_TEST_D3_DOMAIN_ID=20 BRIDGE_TEST_D4_DOMAIN_ID=20
```

### Troubleshooting

**`/CopterN/*` topics list but `ros2 topic echo` shows nothing.**
Two distinct causes; check in this order.

1. **Bridge missed AirSim's RPC boot window.** The bridge's
   `rpc_dynamic` vehicle discovery races AirSim's RPC port (41451)
   coming up; if RPC isn't accepting yet, the bridge falls back to an
   explicit vehicle list AND the `multirotor_node` hangs at "Loading
   settings from AirSim server..." with no retry. Symptom: `ros2 topic
   info -v /CopterN/LidarSensor1/points` reports `Publisher count: 0`.
   Compose now gates the bridge on `airsim-xfs: service_healthy` — but
   if you ever recreate the bridge while AirSim is still cold, restart
   only the bridge once AirSim is healthy:
   ```bash
   docker compose --profile per-drone-bridge restart airsim_bridge_dN
   ```
2. **QoS mismatch on raw lidar / odom topics.** Sensor topics publish
   `BEST_EFFORT`; default subscribers are `RELIABLE` and never match.
   `/CopterN/registered_point_cloud` is published `RELIABLE` so default
   `ros2 topic echo` works on it, but `/CopterN/LidarSensor1/points`
   and `/CopterN/ground_truth/odom` need:
   ```bash
   ros2 topic echo --qos-reliability best_effort /CopterN/LidarSensor1/points
   ```
   In **rviz2**, set the topic display's **Reliability Policy** to
   **Best Effort** for raw lidar/odom. PointCloud2 also needs the
   **Fixed Frame** to match the cloud's `header.frame_id` (`map` by
   default, `<vehicle>/base_link` if `LOCAL_OBS_TARGET_FRAME=base_link`).
3. **`ros2 topic echo` works but rviz2 shows nothing on
   `registered_point_cloud` (especially after flying far from launch).**
   The cloud's per-message size is roughly
   `LOCAL_OBS_BUFFER_SEC × lidar_rate × 1/voxel_volume × 20 bytes`
   (point_step is 20: x/y/z + 4-byte SSE pad + intensity). Defaults
   (`buffer=30s`, `voxel=0.15m`, lidar `PointsPerSecond=200000`) produce
   ~540 KB messages — single-fragment under DDS's ~1 MB cliff,
   visually dense in rviz. Empirical sizing table:

   | Voxel | ~Points / msg | ~Bytes / msg | DDS-safe? |
   |---|---|---|---|
   | 0.05 m | 720K | 14 MB | ❌ rviz drops |
   | 0.10 m | 91K | 1.8 MB | borderline |
   | **0.15 m** (default) | **27K** | **540 KB** | ✓ |
   | 0.20 m | 11K | 220 KB | ✓ |
   | 0.50 m | 600 | 12 KB | ✓ but visually sparse |

   If you've lowered voxel to chase detail and the cloud goes blank
   in rviz, you've crossed the fragmentation cliff — `ros2 topic
   echo` still works (same docker network, `ipc:host` SHM bypasses
   fragment loss), but rviz2 on the host sees fragment loss and
   silently drops most messages. Either raise the voxel or run rviz2
   inside an `agent_internal-N`-attached container.

   rviz2 settings for large-cloud streams:
   - **Fixed Frame**: match the cloud's `frame_id` (`map` if
     `LOCAL_OBS_TARGET_FRAME=map`, `<vehicle>/base_link` if `=base_link`).
   - **Reliability Policy**: Reliable (the cloud publishes RELIABLE).
   - **Queue Size**: 1 — don't backlog 40 MB messages.
   - **Decay Time**: 0 — display the latest message only.
   - **Style**: Points (cheaper than Spheres / Boxes).
4. **Cloud renders empty even though display status is OK** (and the
   raw `/CopterN/LidarSensor1/points` renders fine). With
   `LOCAL_OBS_TARGET_FRAME=base_link`, the registered cloud is in
   the drone's **current** body frame. Points captured at past body
   positions get re-expressed in the current body frame on every
   publish — so a point captured 5 m in front of the drone at takeoff
   is, after flying 800 m forward, at body-frame `X ≈ -800 m`. With
   rviz Fixed Frame also set to `Copter1/base_link`, the camera is
   pinned to the drone (origin) and those points are 100s of metres
   off-screen of the default frustum. rviz IS rendering them — they
   just live way outside the camera view.

   The clean fix is **rviz-side only** (no `.env` change, cloud's
   `frame_id` stays `Copter1/base_link` for downstream consumers):
   - Set rviz Fixed Frame to `map`. The TF chain
     `Copter1/base_link → Copter1/odom → map` already exists.
   - Right-click the 3D viewport → "Focus Camera on" `Copter1/base_link`
     (or hit `f` after selecting the cloud display) to recenter on
     the drone.

   If you'd rather the cloud itself live in world frame (some autonomy
   stacks prefer this), set `LOCAL_OBS_TARGET_FRAME=map` in `.env` —
   but that changes the topic's `frame_id` and is a downstream-contract
   change, not just a viz tweak.

## MAVROS data path (how `test-per-drone-mavros.sh` actually moves a drone)

Each `mavros_dN` container runs on `agent_internal-N`. There is **no**
direct AirSim path for MAVROS — it talks MAVLink-over-TCP to the host:

```
┌─────────────────────────────────────────────────────────────────┐
│ mavros_dN container (on agent_internal-N)                       │
│   ros2 launch airsim_ros_pkgs mavros_bringup.launch.py          │
│     vehicle:=CopterN  target_system_id:=N                       │
│     fcu_url:=tcp://host.docker.internal:{5760+10*(N-1)}         │
└────────────────────────────┬────────────────────────────────────┘
                             │ host.docker.internal:host-gateway
                             ▼ (MAVLink-over-TCP)
┌─────────────────────────────────────────────────────────────────┐
│ host networking                                                 │
│   ardupilot-drone-{N-1} container (network_mode: host)          │
│     ArduCopter SITL  SYSID_THISMAV=N                            │
│     listening tcp://0.0.0.0:{5760+10*(N-1)}                     │
└────────────────────────────┬────────────────────────────────────┘
                             │ FDM bridge
                             ▼ (TCP 9002+10*(N-1) / UDP 9003+10*(N-1))
┌─────────────────────────────────────────────────────────────────┐
│ ardupilot-xfs-airsim container (network_mode: host)             │
│   UE5 + AirSim plugin renders Copter{N}                         │
│   AirSim RPC on host:41451  ←  consumed by airsim_bridge_dN     │
│                                (NOT by mavros_dN)               │
└─────────────────────────────────────────────────────────────────┘
```

A setpoint round-trip:

> `test_one_drone_mavros.py` publishes a `Twist` on
> `/Copter1/mavros/setpoint_velocity/cmd_vel_unstamped` inside
> `mavros_d1`. MAVROS converts it to `SET_POSITION_TARGET_LOCAL_NED`
> MAVLink and sends it to `ardupilot-drone-0` over TCP 5760
> (host-gateway). The SITL accepts the setpoint, computes motor
> outputs, and pushes drone state to AirSim via the FDM channel.
> AirSim updates UE5; sensors are read back via RPC by
> `airsim_bridge_d1`, which republishes `/Copter1/*` on
> `agent_internal-1`.

Two gotchas to know:

- **`target_system_id` matters.** ArduPilot's `SYSID_THISMAV =
  instance + 1`. `mavros_dN` must address sysid `N`, otherwise
  commands tagged for system 1 are silently ignored by SITLs 2/3/4.
- **Position telemetry is best-effort.** ArduPilot SITL on TCP doesn't
  always stream `LOCAL_POSITION_NED`, so `test_one_drone_mavros.py`'s
  PASS criterion is state transitions (`connected`, `mode=GUIDED`,
  `armed=true`) plus successful service-call returns — not measured
  displacement. If you need real position deltas, query AirSim RPC
  directly or set `SR1_POSITION` in `default_params.parm`.

## MIGHTY local planner (drone 1)

One command reproduces the whole verified flow (stack → MAVROS → takeoff →
planner → mission):

```bash
./run-mighty-demo.sh                  # bring-up only, prints the goal command
./run-mighty-demo.sh --goal 20 0 3    # + fly a single /goal
./run-mighty-demo.sh --with-metrics   # + metrics-collector flies mission.json
./run-mighty-demo.sh --teardown       # stop demo extras (keeps main stack)
```

Data path:

```
mission.json ─► scenario_controller (metrics-collector, host net, domain 1)
                    │ /goal (PoseStamped, 5 Hz)
                    ▼
        mighty_d1 (agent_internal-1, image mighty_algo_only)
          reads  /Copter1/ground_truth/odom
                 /Copter1/registered_point_cloud   (MUST be map-frame!)
          writes /Copter1/mavros/setpoint_raw/local (PositionTarget, 100 Hz)
                    │
                    ▼
        mavros_d1 ─(sim_net TCP 172.30.0.21:5760)─► ArduCopter GUIDED
```

Four integration facts, learned the hard way:

1. **World-frame cloud required.** MIGHTY consumes
   `registered_point_cloud` raw (no TF transform). The repo default
   `LOCAL_OBS_TARGET_FRAME=base_link` feeds it body-frame points → its
   world map is garbage → `goal is not free!` + planner segfault. The
   demo script exports `LOCAL_OBS_TARGET_FRAME=map` and recreates
   `airsim_bridge_d1` if needed. This changes the cloud's `frame_id`
   contract for every other consumer on drone 1 — flip `.env` only if
   that's globally acceptable.
2. **Params file needs a wildcard header.** The image's baked
   `mighty.yaml` starts with `mighty_node:`, which never matches the
   node launched inside `/NX01` — every param silently ignored
   (compiled defaults: `use_free_start=0` → `Start is not free` +
   segfault). Our working copy `config/experiments/mighty.yaml` uses
   `/**/mighty_node:` and is the file `run_mighty_d1.sh` mounts by
   default (via `run-mighty-demo.sh`).
3. **Broken colcon chain in the image.**
   `adaptor_ws/install/setup.bash` doesn't register `mighty_adaptor`;
   `scripts/run_mighty_d1.sh` exports `AMENT_PREFIX_PATH`/`PYTHONPATH`
   manually. The image runs as `appuser` with workspaces under
   `/workspace/generated/mighty` — the old `MIGHTY-docker/run.sh`
   sources `/root/*` paths that don't exist here.
4. **Mission-tuned params.** `force_goal_z: false` (upstream forces
   every goal to z=1 m), `goal_seen_radius: 1.5` (upstream 5.0 stops
   replanning 2-3 m short of the goal — outside the metrics
   controller's 0.5 m tolerance), `use_free_goal: true` (a waypoint
   beyond sensor range is in unknown space; upstream refuses to plan
   to it and the drone stalls), `v_max: 2.5` (1.0 can't beat the 120 s
   waypoint timeout on long legs).

## ROS_DOMAIN_ID strategy

| Setup | What to set |
|---|---|
| Standalone test | Nothing. Default: drone N → domain N. |
| Flat autonomy domain (e.g. 20) | `DRONE_{1..4}_DOMAIN_ID=20` (and `BRIDGE_TEST_D{1..4}_DOMAIN_ID=20` for the validation tool). |

**SHM caveat at flat domain.** The four bridges share `/dev/shm`
(`ipc: host`), so FastDDS's SHM transport discovers them all on the
same domain — `ros2 topic list` from inside one bridge shows the other
three's topics too. Production consumers (`autonomy_stack-N`) don't
share `/dev/shm` with the bridges, so they only see their own
per-drone slice over the docker network. Cosmetic, not a correctness
issue.

## Scaling drone count

Drone count is `NUM_DRONES` in the runtime-stack root `.env`. The
canonical recipe — TL;DR, knob table, worked example, generator
commands — lives in the
[root README's "Generating the ardupilot-xfs scenario"](../../README.md#generating-the-ardupilot-xfs-scenario)
section.

The rest of this section is ardupilot-xfs-specific: per-drone
overrides and the end-to-end verification runbook.

### Per-drone overrides

To customize a specific drone's vehicle name or `ROS_DOMAIN_ID`, set
the per-drone override in `.env`. Defaults are
`${VEHICLE_PREFIX}{N}` and `{N}`:

```bash
VEHICLE_5_NAME=ground_drone_1   # appears in settings.json + airsim_bridge_d5 launch
DRONE_5_DOMAIN_ID=20            # ROS_DOMAIN_ID for drone 5's bridge
```

### End-to-end test flow

```bash
# 1. Set count in source of truth
sed -i 's/^NUM_DRONES=.*/NUM_DRONES=2/' .env

# 2. Verify the generator's invariants (doesn't write)
python3 tools/generate_scenario.py --self-test

# 3. Validate compose syntax against current .env (writes if drift)
python3 tools/generate_scenario.py
docker compose -f compose/ardupilot-xfs/docker-compose.yml \
  --profile per-drone-bridge --profile agent-external config --quiet

# 4. Bring up
./launch.sh ardupilot-xfs

# 5. Smoke-test sensor flow
docker exec airsim_bridge_d1 bash -lc \
  'source /airsim_ros2_ws/install/setup.bash && \
   timeout 5 ros2 topic hz /Copter1/registered_point_cloud'

# 6. Flight test (per-drone arm/takeoff/setpoint/land)
./test-per-drone-mavros.sh

# 7. Teardown
./stop.sh ardupilot-xfs

# 8. Restore default
sed -i 's/^NUM_DRONES=.*/NUM_DRONES=4/' .env
python3 tools/generate_scenario.py
```

## File inventory

| File | Purpose |
|---|---|
| `docker-compose.yml` | **Generated** main scenario (per-drone bridges + agent-external profile). Source: `templates/docker-compose.yml.j2`. |
| `docker-compose.mavros-test.yml` | **Generated** N× `mavros_dN` services for the MAVROS integration test (separate compose project). Source: `templates/docker-compose.mavros-test.yml.j2`. |
| `templates/` | Jinja2 templates that drive the two generated compose files above. Edit these to change the production stack shape, then `python3 tools/generate_scenario.py`. |
| `test-per-drone-mavros.sh` | MAVROS integration test (real flight). Reads `NUM_DRONES` from root `.env`. |
| `scripts/test_one_drone_mavros.py` | Per-drone arm/takeoff/setpoint/land mission, run via `docker exec` inside `mavros_dN`. |
| `scripts/use_settings.sh` | Helper to swap an alternate AirSim settings file in. |

## Stop everything

```bash
./stop.sh ardupilot-xfs   # cleans up all profiles
./test-per-drone-mavros.sh --teardown   # if mavros containers were up
```
