# One run, end to end: the flow and what it leaves behind

This follows one OSMO run from the CampaignSpec to the Grafana dashboard. At
each step it names what is written, where, by whom, and who reads it next. The
worked example is condo `calm-r1`, flown on 25 Sept 2026 as workflow
`sim-bridge-vio-62`. It passed, with ATE 0.104 m against a 1.0 m gate.

For commands and troubleshooting, see [osmo-runbook.md](osmo-runbook.md). For
the Kubernetes and OSMO ideas underneath, see
[osmo-kubernetes-concepts.md](osmo-kubernetes-concepts.md). Pictures:
- `docs/diagrams/osmo-end-to-end.png` covers onboarding through to the
  scorecard.
- `docs/diagrams/campaign-to-metrics.png` covers the spec through to what is
  stored and shown, marking what is built, what exists elsewhere, and what is
  still target.

```
CampaignSpec + ScenarioSpec + image catalog          (git)
   |  campaign.py run: plan, generate, check images, submit
   v
OSMO workflow, one per run                           (cluster)
   run group:  discovery-server, sim, autopilot, bridge, vio, pilot, recorder [, foxglove]
   evaluate:   vio-eval, spawn-eval, validate
   aggregate:  verdict
   |  every task's {{output}} -> object storage
   v
runs/<key>/ on the host  ->  vio-stress reports  ->  campaign_manifest.json
   |                                                     |
   +--> run registry (Postgres)  --> Grafana dashboard   +--> campaign status (CLI)
```

## The command

```bash
MNS_IMAGE_SET_FILE=/home/mnsuser/M-S-Simulation-Runtime-Stack/images/image-set.generated.yaml \
MNS_SIM_REAL_EVAL_IMAGE=dhdevspace/auto_mns:sim-real-eval-worker-bcb899f \
python3 osmo/campaign.py run vio-osmo-condo --only calm-r1 --no-viz
```

- `MNS_IMAGE_SET_FILE` picks the image set: the main checkout's, which pins
  runtime host 303a5c.
- `MNS_SIM_REAL_EVAL_IMAGE` is a scorer that has `vio-stress`. The catalog's
  `-latest` worker predates it.
- `--no-viz` leaves the live Foxglove task out.

## Timeline of run 62

| Time (UTC+8) | Registry status | What was happening |
| --- | --- | --- |
| 11:40:17 | `pending` | plan done; attempt 2 of `calm-r1` created (attempt 1 is run 60, backfilled) |
| 11:40:27 | `submitted` | `osmo workflow submit` returned `sim-bridge-vio-62`; OSMO had it PENDING |
| 11:40:47 | `running` | KAI placed the whole run group on the GPU node |
| 11:42:27 | `evaluating` | recorder done (73.7 s recorded); the evaluate group started |
| 11:43:03 | (ended) | verdict exited 0; workflow COMPLETED |
| 11:43:07 | `passed` | evidence pulled back, registry written |

That is about 2 min 50 s from submit to verdict for a 51 s flight window.
After this, `vio-stress` scores every `done` run of the campaign on the host.

## A campaign's timeline: four XFS runs on one GPU

`osmo/campaign.py run vio-osmo-xfs` (25 Sept) submits all four runs at once.
KAI then flies them one gang at a time. Times are OSMO's own, as the registry
records them.

| Run | Workflow | Queued | Flight group | Wait before evaluate | Evaluate + verdict | ATE rmse |
| --- | --- | --- | --- | --- | --- | --- |
| calm-r1 | sim-bridge-vio-65 | 5 s | 128 s | 138 s | 47 s | 0.817 m |
| calm-r2 | sim-bridge-vio-66 | 132 s | 129 s | 8 s | 71 s | 0.523 m |
| wind-6-r1 | sim-bridge-vio-67 | 286 s | 128 s | 138 s | 45 s | 0.620 m |
| wind-6-r2 | sim-bridge-vio-68 | 412 s | 128 s | 9 s | 45 s | 0.659 m |

The whole matrix took 10 min 15 s, submit to last verdict. Two things to read
off it:

- **Every flight group took the same time**, wind or calm, at 128-129 s. The
  wind-6 runs' longer end-to-end times are all queue.
- **A run's evaluation waits behind the next run's flight.** When calm-r1's
  flight ended, calm-r2's gang was placed on the GPU node first, and
  calm-r1's evaluate group waited 138 s for it to finish.
- **The GPU was busy meanwhile.** The flight groups ran back to back, with
  gaps of 5 s, 31 s and 5 s. The 31 s is two evaluations holding the node's
  cores until the next gang fitted.
- **What running evaluation on the service node would change.** Evaluation
  needs no GPU. Moving it there gets each run's verdict about 2 minutes
  sooner, and removes the 31 s gaps, about 5% of this matrix. It is mostly
  a latency gain, not a throughput one. Not done yet.

## Step by step

### 1. Inputs, in git

| Artifact | Path | Read by |
| --- | --- | --- |
| CampaignSpec | `scenarios/vio-osmo-condo/CampaignSpec.yaml`: variants, repeats, mission route, `evaluation.gates`, `extensions.mns.omega` | `campaign plan`, the executor (gates, route, timeout) |
| ScenarioSpec | the one the CampaignSpec's `scenario:` names: level pack, vehicle, rig, weather, estimator, `runtime.features` | `campaign plan` (merged per variant), the stack generator |
| Route | the CampaignSpec's `mission.trajectory` file | the executor, as base64 JSON in `route_b64` |
| Image catalog | `images/catalog.yaml`, rendered to `images/image-set.generated.yaml` | the executor: every image is pinned by digest there |

### 2. Plan and generate, on the host

`campaign.py run` shells into the product shell and does not re-implement it.

| Artifact | Path under `generated/campaigns/<campaign>/` | Written by | Notes |
| --- | --- | --- | --- |
| Materialised spec | `<key>/ScenarioSpec.yaml` | `./product.sh cli campaign plan` | one per run key, the variant's overrides merged in; owned by root |
| Stack | `stacks/<key>/` | `./product.sh cli runtime --no-run` | see below |

The stack is what the workflow mounts at `/workspace/generated/<stack>`:

| In `stacks/<key>/` | What it is |
| --- | --- |
| `config/unreal-airsim/settings.json` | the simulator's settings: vehicle, cameras, IMU, wind |
| `config/topic_names.yaml`, `config/sim2real/topics.yaml` | topic names and the evaluator's role map (ground truth, estimate, clock) |
| `config/vio/` | the estimator's config (OpenVINS, kalibr chain) |
| `config/dds/` | Fast DDS profiles |
| `config/scenario/`, `asset-packs/`, `content-packs/` | the level and asset packs the ScenarioSpec pins |
| `docker-compose.yml` | the compose form of the same stack. OSMO does not use it, but it shows what the generator decided |
| `generated-manifest.json`, `execution-context.json`, `scenario-artifacts-manifest.json` | the generator's own record of its inputs |

### 3. Check images, then submit

- **Image check:** `campaign.py` compares the image ID the node holds under
  each tag with the host image that carries the pinned digest (see the
  runbook's "Images" section). A mismatch refuses the run unless
  `--allow-image-drift`.
- **Submit:** `osmo workflow submit osmo/sim-bridge-vio.workflow.yaml` with one
  `--set` and one `--set-string` carrying every value: stack, autopilot, viz,
  route, gates, record time, the six images.
- **Result:** a workflow ID, `sim-bridge-vio-62`. It is the run's identity
  everywhere below.

### 4. In the cluster

| Group | Tasks | Output (under `s3://osmo/workflows/<workflow>/<task>/`) |
| --- | --- | --- |
| `run` (all or nothing, KAI) | discovery-server, sim, autopilot, bridge, vio, pilot, **recorder** (lead), foxglove if viz | recorder: `bag/bag_0.mcap`, `bag/metadata.yaml`, `mission.json` |
| `evaluate` | **vio-eval** (lead), spawn-eval, validate | vio-eval: `vio.json`, `vio.md`, `window.json`, `estimate.tum`, `ground_truth.tum`; spawn-eval: `spawn.json`; validate: `validation.json` |
| `aggregate` | **verdict** | `verdict.json`; its exit code is the run's pass/fail |

Object storage is the chart's localstack. The host reaches it at
`<osmo-worker IP>:<NodePort>`, which `campaign.py` reads live.

### 5. Evidence back on the host: `runs/<key>/`

`run_one` syncs each task's output into the run directory. It then writes
the files the scorecard and the registry read.

| File | From | Contents (run 62) |
| --- | --- | --- |
| `bag/bag_0.mcap` | recorder | the flight: ground truth, estimate, IMU, TF, camera_info, clock. 24 MB. Opens in Foxglove |
| `mission.json` | recorder | how the flight ended: `announced: true`, `pilot_exit: 0`, `recorded_sec: 73.7`, message counts per topic (`/ov_msckf/odomimu`: 11103) |
| `eval/vio-eval/window.json` | vio-eval | the flight window cut from the recording: 19.1-70.5 s, airborne 47.4 s, path 7.46 m, whole-recording ATE 0.557 m for contrast |
| `eval/vio-eval/vio.json`, `vio.md` | vio-eval | trajectory metrics over the window: ATE rmse 0.104 m, max 0.355 m, RPE rmse 0.256 m, rotation, yaw drift |
| `eval/vio-eval/estimate.tum`, `ground_truth.tum` | vio-eval | the trimmed trajectory pair the host scorer uses |
| `eval/spawn-eval/spawn.json` | spawn-eval | did the vehicle stay in the world (`spawn_ok`, z range, fall rate) |
| `eval/validate/validation.json` | validate | recording checks: `valid`, `failed_checks`, which checks were skipped, how each role was resolved |
| `eval/verdict/verdict.json` | verdict | `mns.verdict.v1`: `rc` and one row per gate. Run 62: spawn pass; `ate_rmse_m` 0.104, max 1.0, pass |
| `validation.json` | executor | a copy of this attempt's `eval/validate/validation.json`, where the platform's scorecard looks |
| `topics.yaml` | executor | the stack's role map, copied so the bundle is self-describing |
| `run.json` | executor | `mns.vio_run_meta.v1`: campaign, key, variant, repeat, workflow ID and status, every task's status, platform axes, gates, the images block (pinned, submitted, node image IDs), viz |
| `logs/<workflow>/<task>.log`, `<task>.sidecar.log` | executor | only for a run that did not pass: every task's output, from Loki (or `osmo workflow logs` if Loki is down) |

**One directory per run key, not per attempt.** A re-fly writes into the same
`runs/<key>/`, and a sync adds files without deleting old ones. So:
- the top-level `validation.json` is always re-copied from this attempt's
  validate output;
- failure logs go under the workflow ID;
- the registry keeps one row per attempt, so older attempts' numbers survive
  there even when their files are overwritten.

### 6. Scoring, on the host: `reports/`

After every run is back, `evaluate()` runs the platform's `vio-stress` from
the sim_real_eval image once per `done` run. It uses the trimmed pair from
vio-eval.

| File | Contents |
| --- | --- |
| `reports/<key>.json` | `categories.vio_stress.run` (ATE and RPE rmse, recovery) and `.comparison` (translation and rotation stats). Run 62: ATE rmse 0.104 m, matching vio-eval |
| `reports/<key>.md` | the same, readable |

### 7. The campaign record: `campaign_manifest.json`

`mns.vio_campaign_manifest.v1` is rewritten after every submit and every run:
- one `RunRecord` per run key: status `done`/`failed`, workflow ID,
  `recording_valid`, `failed_checks`;
- at the top level: the spec, gates, omega tier and verifies, platform axes.

`osmo/campaign.py status vio-osmo-condo` renders it as the platform's
scorecard. `osmo/campaign.py reindex vio-osmo-condo` rebuilds it from
`runs/*/run.json`. A manifest is local, derived data: it is not in git.

### 8. The run registry, then Grafana

`campaign.py` writes the registry as it goes (see the runbook's registry
section for the full table):

| Table | Run 62's rows |
| --- | --- |
| `campaigns` | `vio-osmo-condo`: name, spec path, gates, platform, git SHA |
| `runs` | attempt 2: `passed`, `workflow_ref` sim-bridge-vio-62, submitted/started/eval/ended times, tasks, images, viz, `recording_valid` true |
| `gate_results` | `spawn` pass; `ate_rmse_m` 0.104 within `{max: 1.0}` pass |
| `metrics_summary` | ATE rmse 0.1042 m, ATE max 0.3548 m, RPE rmse 0.2560 m, ATE rot 4.90 deg, yaw drift 2.58 deg/min, flight 51.4 s |
| `artifacts` | bag (24 MB), eval (1.3 MB), reports `.json` and `.md` |

Grafana (http://localhost:3000/d/tevv-campaign-progress) reads it through the
read-only role:
- campaign progress and pass rate;
- the latest attempt of each run;
- gates per run;
- ATE per run;
- attempts per day.

### 9. Logs, alongside all of it

While the run flies, Alloy reads every task's container log on the node into
Loki. The same happens for the OSMO control plane. Each line carries the task,
its source (task, sidecar or control plane) and the workflow ID. Grafana's
"TEVV run logs" dashboard picks a run from the registry and shows:
- its verdict lines;
- errors across tasks;
- lines per task;
- any one task's output.

[osmo-logs.md](osmo-logs.md) explains why this, and not `osmo workflow
logs`, is the complete copy, and what it deliberately leaves out.

## Where to look

| Question | Look at |
| --- | --- |
| Is my campaign done, and how is it doing? | Grafana "Campaign progress", or `v_campaign_progress` |
| Did this run pass, and which gate failed? | Grafana "Gates per run", `eval/verdict/verdict.json`, or `osmo workflow logs <wf> --task verdict` |
| Why did it fail? | registry `failure_reason` and `error`; Grafana "TEVV run logs" for that run; `runs/<key>/logs/<wf>/` |
| What did the drone actually do? | `bag/bag_0.mcap` in Foxglove; `mission.json` for how it ended |
| Is the number trustworthy? | `eval/validate/validation.json` (recording checks) and `eval/spawn-eval/spawn.json` |
| Which images flew? | `run.json` `images`, or registry `runs.images` |
| What did the simulator get told? | `stacks/<key>/config/unreal-airsim/settings.json` |
| How did this attempt compare with the last? | registry `runs` and `metrics_summary`, one row per attempt |
| The full metric set, across campaigns | ClickHouse `metric_results`, not yet fed from OSMO runs |

## Not yet in the flow

- **Prometheus:** live, run-labelled rates.
- **The simulator's event stream:** MetricsEmitter to Kafka to ClickHouse has
  no consumer under OSMO.
- **The TEVV web dashboard** reading the registry instead of its manifest
  folder.
