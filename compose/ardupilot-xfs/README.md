# ardupilot-xfs scenario

Four ArduPilot SITL drones + AirSim XFS + per-drone ROS2 bridges, one
per `agent_internal-N` docker network. Optionally pair with the
autonomy team's mesh on `agent_external`.

## Modes

| Mode | Command | When to use | Persona |
|---|---|---|---|
| **Solo dev/test** | `./launch.sh ardupilot-xfs` | Bring up sim only. No autonomy team running. | Dev |
| **Autonomy integration** | `./launch.sh ardupilot-xfs --with-agent-external` | Pair with autonomy_stack-1..3 on agent_external (the team's compose creates that network). | Autonomy |
| **Legacy single-bridge** | `./launch.sh ardupilot-xfs --legacy-bridge` | Reproduce the old `ros2-x11-node` + `sim-router` topology (regression / fallback). | Dev |

`--legacy-bridge` is incompatible with `--with-agent-external` (legacy
already includes `sim-router` on agent_external). Other useful flags:
`--headless` (off-screen UE5), `--with-monitoring`, `--with-metrics`,
`--all`.

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

### The few env knobs you actually flip

Edit the **runtime-stack root** `.env` (NOT
`compose/ardupilot-xfs/.env` — that's vestigial and not auto-loaded).
Full reference is in `<repo-root>/.env.example`. The keys you most
often touch:

| Key | Default | Purpose |
|---|---|---|
| `DRONE_{1..4}_DOMAIN_ID` | `1..4` | Per-drone ROS_DOMAIN_ID. Set all four to a single value (e.g. `20`) if your team aligns on a flat domain. |
| `LOCAL_OBS_TARGET_FRAME` | `map` | `target_frame` for per-vehicle `pointcloud_registration_node`. Use `base_link` for per-drone REP-105 (`{vehicle}/base_link`); any other string passes through literally. |
| `VEHICLE_{1..4}_NAME` | `Copter{1..4}` | Override per-drone vehicle key in `settings.json`. |
| `AIRSIM_BRIDGE_IMAGE` | `tevv-airstack-ros2-x11-node-multi-agent-bridge` | Pin a different bridge image for testing. |

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

The dev-side `./test-per-drone-bridges.sh` script does this for all
four drones and reports PASS/FAIL.

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

Two complementary tools, both from `compose/ardupilot-xfs/`:

| Tool | Validates | Touches the FCU? |
|---|---|---|
| `./test-per-drone-bridges.sh` | `/CopterN/*` topic delivery on each agent_internal-N | No (read-only) |
| `./test-per-drone-mavros.sh` | Full MAVROS chain: arm, GUIDED, takeoff, setpoint, land — per drone, in isolated containers on `agent_internal-N` | **Yes — flies the drones.** |

For autonomy alignment on a flat domain, set the matching env BEFORE
running either tool:

```bash
export DRONE_1_DOMAIN_ID=20 DRONE_2_DOMAIN_ID=20 \
       DRONE_3_DOMAIN_ID=20 DRONE_4_DOMAIN_ID=20 \
       BRIDGE_TEST_D1_DOMAIN_ID=20 BRIDGE_TEST_D2_DOMAIN_ID=20 \
       BRIDGE_TEST_D3_DOMAIN_ID=20 BRIDGE_TEST_D4_DOMAIN_ID=20
```

### Switch to legacy bridge

```bash
./launch.sh ardupilot-xfs --legacy-bridge
# Brings up sim-netns + sim-router + ros2-x11-node instead of per-drone bridges.
# Fire MAVROS inside ros2-x11-node:
docker exec -it ardupilot-xfs-ros2 \
  bash compose/ardupilot-xfs/scripts/launch_4_mavros.sh
```

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

## File inventory

| File | Purpose |
|---|---|
| `docker-compose.yml` | Main scenario — runs the per-drone (default) and legacy services behind compose profiles. |
| `docker-compose.mavros-test.yml` | 4× `mavros_dN` for the MAVROS integration test (separate compose project). |
| `docker-compose.bridges.yml` (+ `.bridges.override.yml`, `.bridge-test.yml`, `.networks.yml`) | Vendored standalone bridges + topic-delivery validation. Used by `./test-per-drone-bridges.sh`. |
| `test-per-drone-bridges.sh` | Topic-delivery validation. |
| `test-per-drone-mavros.sh` | MAVROS integration test (real flight). |
| `scripts/test_one_drone_mavros.py` | Per-drone arm/takeoff/setpoint/land mission, run via `docker exec` inside `mavros_dN`. |
| `scripts/{launch_4_mavros,bringup_safe,use_settings,mavros_velocity_demo}.{sh,py}` | Legacy / dev helpers — see comments in each file. |
| `tools/generate_compose.py` (+ `tools/README.md`) | Regenerate `docker-compose.bridges.yml` from upstream Cosys-AirSim. |

## Stop everything

```bash
./stop.sh ardupilot-xfs   # cleans up all profiles
./test-per-drone-mavros.sh --teardown   # if mavros containers were up
./test-per-drone-bridges.sh --teardown  # if bridge-test containers were up
```
