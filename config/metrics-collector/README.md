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

Suggested wiring: publish `RunState{RUNNING}` when you start exploration; poll
`GetExplorationStatus` and publish `RunState{COMPLETED}` when all agents return to
`IDLE` (area covered) — or `{ABORTED}` on `CANCELLED`. For search missions,
`TARGET_DETECTED` is the natural terminal → `COMPLETED`.

For a **no-planner smoke test** of the dashboard side only, the
`exploration-mock-generator` service (`docker-compose-monitoring.yml`) emits synthetic
exploration metrics to Prometheus — it does not move the drone or feed this collector.

## Gotchas

- Container shows **unhealthy** = cosmetic: health server can't bind `:8888`
  (`Errno 98 Address already in use` on host net). Collector node runs fine.
- `entrypoint.sh` / `ingest_to_es.py` are single-file bind mounts — editing them
  replaces the inode; **recreate the container** to pick up changes (`--force-recreate`).
- The container is **one-shot** (`restart: "no"`). After a full finalize it tends to
  exit. For repeated exploration runs, finalize manually per run (collector stays alive),
  or stop/recreate per run.
