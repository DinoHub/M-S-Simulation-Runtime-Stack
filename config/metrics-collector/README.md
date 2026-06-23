# metrics-collector

Unified benchmarking container (`docker-compose-metrics.yml`, image
`dhdevspace/auto_mns:metrics-collectorV2-release`, `network_mode: host`).
One entrypoint runs three processes:

- `metrics_collector_node` (C++) — observer; integrates odometry/collision against `/clock`.
- `scenario_controller.py` — goal-driven flight controller (Connect → Arm → Takeoff → Waypoints).
- `evaluate.py` + `ingest_to_es.py` — offline pass/fail + Elasticsearch export (run on finalize).

The collector code is baked into the image. `entrypoint.sh` and `ingest_to_es.py`
are **vendored** into this directory and bind-mounted over the image copies (see
`docker-compose-metrics.yml`) so they can be patched without a rebuild.

## Trigger modes

The collection lifecycle (when metrics start/stop) has three paths. Pick by planner type.

| Mode | Enable | Start / stop signal | Use for |
|------|--------|---------------------|---------|
| **Goal-triggered** (default) | `USE_RUN_STATE_TRIGGER=false` | start on first `/goal` (PoseStamped), end on goal reached / `MISSION_TIMEOUT_SEC` | point-to-point local planners (`mighty`, `dwa`, `teb`) |
| **run_state** (planner-agnostic) | `USE_RUN_STATE_TRIGGER=true` | `/run_state` `RunState{state:1 RUNNING}` → start, `{2 COMPLETED \| 3 ABORTED}` → stop | exploration / coverage / any non-goal planner |
| **External Bool** (manual) | either | `/metrics/start` `/metrics/stop` (`std_msgs/Bool {data:true}`) | ad-hoc recording / debugging |

Topic types: `/run_state` = `airsim_interfaces/msg/RunState`; `/metrics/start` `/metrics/stop` = `std_msgs/Bool`; `/goal` = `geometry_msgs/PoseStamped`.

## Goal-triggered mode (default — local planners)

```bash
CONFIG_ROOT=$PWD/config \
 docker compose -f docker-compose-metrics.yml --profile metrics up -d metrics-collector
```
`scenario_controller.py` flies the mission (`mission.json` waypoints). With
`scenario_controller.yaml: auto_start_when_ready: true` it auto-arms+starts; metrics
auto-start on the first `/goal` and finalize on goal reached. `evaluate.py` then runs
goal/path/time/collision checks and `ingest_to_es.py` pushes to ES → Grafana.

## run_state mode (planner-agnostic — exploration)

```bash
CONFIG_ROOT=$PWD/config USE_RUN_STATE_TRIGGER=true \
 docker compose -f docker-compose-metrics.yml --profile metrics up -d --force-recreate metrics-collector
```

In this mode the entrypoint **skips `scenario_controller.py`** (it has no goal flow,
throws, and would write a failing `controller_result.json` that poisons the verdict)
and clears any stale sidecar. The run lifecycle is driven entirely by `/run_state`,
which **you / your planner orchestrator publish** — the bundled `scenario_controller.py`
does not emit it.

```bash
# START collection (no /goal needed)
docker exec metrics-collector bash -lc 'source /opt/ros/*/setup.bash; \
 ros2 topic pub --once /run_state airsim_interfaces/msg/RunState \
   "{state: 1, run_id: \"expl-001\", scenario_id: \"airsim-condo\"}"'

# ... exploration planner flies the area ...

# STOP collection
docker exec metrics-collector bash -lc 'source /opt/ros/*/setup.bash; \
 ros2 topic pub --once /run_state airsim_interfaces/msg/RunState "{state: 2, run_id: \"expl-001\"}"'
```

`evaluation.yaml` is tuned for exploration: `goal_reached` + `require_goal_reached`
off (no single goal); `max_path_length_m` + `max_travel_time_sec` gates off (long path
= coverage, not failure). Surviving metrics: `collisions`, `travel_time`, informational
`path_length`. There is **no** coverage/frontier/map-% metric in this node yet — that is
net-new code.

## Input / output state

| | INPUT (consumed) | OUTPUT |
|--|------------------|--------|
| **trigger** | `/run_state` (or `/goal`, or Bool) | — |
| **data** | `ground_truth/odom`, `collision`, `/clock` | — |
| **on stop** | — | `/metrics/outputs/metrics.json` (auto) |
| **on finalize** | metrics.json | `evaluated.json` + ES `run-summaries` index + Grafana |

`metrics.json` is written automatically when collection stops. `evaluated.json` + ES
push only run on **finalize**, which the entrypoint executes when the foreground process
exits (goal mode: controller exit; run_state mode: collector exit / container stop).
To finalize a run_state run without stopping the container:

```bash
docker exec metrics-collector bash -lc \
 'python3 /metrics/evaluation/evaluate.py --metrics /metrics/outputs/metrics.json \
    --config /metrics/config/evaluation.yaml --output /metrics/outputs/evaluated.json \
    --trajectories-dir /metrics/outputs && python3 /metrics/scripts/ingest_to_es.py'
```

`ingest_to_es.py` (patched) sources `run_id`/`scenario_id` from `metrics.json` (the
per-run id carried by `/run_state`), falling back to the `RUN_ID`/`SCENARIO_ID` env vars.

## Exploration planner to test with run_state mode

**`simple_exploration`** in `autonomy_stack` (`/home/mnsuser/integration/autonomy_stack/exploration/`,
already built + installed):

- Node: `ros2 launch simple_exploration exploration_core_node.launch.py`
- Multi-drone area-partition explorer. Define the area via `SetSearchArea` /
  `PartitionSearchArea` services (`exploration_interfaces`).
- Exposes an `ExplorationStatus` lifecycle per agent — `GetExplorationStatus` srv /
  `ExplorationStatus.msg`: `IDLE=1, TRANSIT=2, BUSY=3, CANCELLED=4, TARGET_DETECTED=5`.

### Hands-off wiring: `tools/run_state_bridge.py`

`tools/run_state_bridge.py` does the wiring automatically: it polls
`/exploration/get_exploration_status` and publishes `/run_state` on transitions —
`RUNNING` when any agent goes TRANSIT/BUSY, `COMPLETED` when all agents return to
IDLE (area covered) or one hits TARGET_DETECTED, `ABORTED` on CANCELLED. So a full
exploration benchmark needs no manual `ros2 topic pub`.

Run it anywhere on the same `ROS_DOMAIN_ID` with both interfaces on the path
(`airsim_interfaces` for RunState, `exploration_interfaces` for the status srv):

```bash
source /opt/ros/airsim/setup.bash
source /home/mnsuser/integration/autonomy_stack/install/setup.bash
python3 tools/run_state_bridge.py --ros-args \
    -p run_id:=expl-001 -p scenario_id:=airsim-condo
```

Params: `status_service`, `run_state_topic`, `poll_period_sec` (1.0), `run_id`,
`scenario_id`, `target_detected_completes` (true), `publish_repeat` (3),
`oneshot` (true — exit after the terminal publish; false = reset and wait for the
next run). `/run_state` is published RELIABLE + TRANSIENT_LOCAL (latched) so the
collector reliably receives each transition.

Manual equivalent (no bridge): publish `RunState{RUNNING}` at start and
`RunState{COMPLETED}`/`{ABORTED}` at the end yourself (see commands above).

For a **no-planner smoke test** of the dashboard side only, the
`exploration-mock-generator` service (`docker-compose-monitoring.yml`) emits synthetic
exploration metrics to Prometheus — it does not move the drone or feed this collector.

## Run it end-to-end (user quickstart)

Validated exploration run, start to ES. Assumes the sim + monitoring (ES on host
`:9210`) are up — e.g. `./launch.sh px4-condo --all`.

**1. Start the collector in run_state mode.** `MISSION_TIMEOUT_SEC` must be a float
(`3600.0`, not `3600`) — the node aborts on an int. Bump it above your expected run
length so the collector doesn't self-exit mid-run (default 600s).

```bash
CONFIG_ROOT=$PWD/config USE_RUN_STATE_TRIGGER=true MISSION_TIMEOUT_SEC=3600.0 \
 docker compose -f docker-compose-metrics.yml --profile metrics up -d metrics-collector
```

**2. Launch the exploration planner** (`exploration_core_node` + your search area) —
this is the autonomy side; it must report agent state via
`/exploration/get_exploration_status`.

**3. Run the bridge** so `/run_state` is driven automatically. It needs both
`airsim_interfaces` and `exploration_interfaces` on the path. The
`tevv-airstack-ros2-x11-node-development` image carries humble + airsim_interfaces;
mount the built autonomy_stack for `exploration_interfaces`:

```bash
docker run -d --name run-state-bridge --network host --ipc host \
  -e ROS_DOMAIN_ID=0 -e FASTDDS_BUILTIN_TRANSPORTS=UDPv4 \
  -v /home/mnsuser/integration/autonomy_stack:/home/mnsuser/integration/autonomy_stack \
  -v $PWD:/ws/repo \
  --entrypoint bash dhdevspace/auto_mns:tevv-airstack-ros2-x11-node-development \
  -lc 'source /opt/ros/humble/setup.bash;
       source /airsim_ros2_ws/install/setup.bash;
       source /home/mnsuser/integration/autonomy_stack/install/setup.bash;
       exec python3 /ws/repo/tools/run_state_bridge.py --ros-args \
         -p run_id:=expl-001 -p scenario_id:=airsim-condo'
```

The bridge publishes `RUNNING` when exploration starts, `COMPLETED` when the area is
covered (all agents IDLE) / `ABORTED` on cancel. The collector records throughout and
writes `metrics.json` on `COMPLETED`.

**4. Finalize** (evaluate + push to ES/Grafana) — see the finalize command under
*Input / output state*. The run lands in the ES `run-summaries` index keyed by `run_id`.

> All four processes must share `ROS_DOMAIN_ID` (default 0) and `FASTDDS_BUILTIN_TRANSPORTS=UDPv4`.
> Run **exactly one** status server on `/exploration/get_exploration_status` — two
> servers make the bridge flap between states.

## Gotchas

- Container shows **unhealthy** = cosmetic: health server can't bind `:8888`
  (`Errno 98 Address already in use` on host net). Collector node runs fine.
- `MISSION_TIMEOUT_SEC` is a **double** — pass `3600.0`, not `3600`, or the collector
  aborts (`parameter 'mission_timeout_sec' has invalid type`). Default `600.0`; raise it
  for long exploration runs or the collector self-exits mid-run.
- `entrypoint.sh` / `ingest_to_es.py` are single-file bind mounts — editing them
  replaces the inode; **recreate the container** to pick up changes (`--force-recreate`).
- The container is **one-shot** (`restart: "no"`). After a full finalize it tends to
  exit. For repeated exploration runs, finalize manually per run (collector stays alive),
  or stop/recreate per run.
