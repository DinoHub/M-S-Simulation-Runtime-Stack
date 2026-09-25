# Evaluation on OSMO: what each evaluator measures, and who decides

A TEVV run is flown once and judged by several separate services. Each one
answers one question and writes one file. Exactly one of them, the `verdict`,
turns those answers into pass or fail. This page lists every service:
- what question it answers;
- how it answers it;
- what it writes;
- where it runs;
- who reads its output.

Related pages:
- [osmo-run-flow.md](osmo-run-flow.md): where each file lands.
- [osmo-logs.md](osmo-logs.md): logs, which never decide anything.
- [osmo-runbook.md](osmo-runbook.md): the commands.

## The rule the design follows

**Evaluators measure; one place judges.**
- No evaluator fails a run because of what it measured. It reports, and exits
  0 or 1 as COMPLETE so the workflow carries on.
- The `verdict` task reads every report and applies the CampaignSpec's
  `evaluation.gates`. That is the only pass/fail decision.
- Adding an evaluator means adding a gate to the verdict, not changing what
  any evaluator does. This keeps "what happened" separate from "is that good
  enough".

Two more rules follow:
- **A physically invalid run is judged before its score is believed.** If the
  vehicle fell out of the world, the trajectory error measures the fall, not
  the estimator. `spawn` is therefore a gate of its own, checked first.
- **A gate that nothing measured fails.** A missing number is not a pass. Run
  26 passed on a gate for a metric no report contained; that is why this rule
  exists.

## The services

```
recorder (bag, mission.json)
   |
   +--> vio-eval ----> estimate.tum, ground_truth.tum, vio.json, window.json --+
   +--> spawn-eval --> spawn.json ---------------------------------------------+--> verdict --> verdict.json, exit code
   +--> validate ----> validation.json (advisory) -----------------------------+       |
                                                                                        v
                        host: vio-stress (sim_real_eval) --> reports/<key>.json   run registry, Grafana
```

Evaluation and aggregation run on the **service node** (`osmo-worker`, OSMO
platform `cpu`), not the GPU node. They need no GPU and no `/workspace`, and
on the GPU node they queued behind the next run's flight. See "Where they run"
below.

### 1. `vio-eval`: how far was the estimate from the truth?

| | |
| --- | --- |
| Question | Over the flight, how far did the estimator's trajectory drift from ground truth? |
| Image | `sim_real_eval` (catalog pin) |
| Input | the recorder's bag: `/ground_truth/odom` and the estimate topic (`evaluation.inputs.est_topic`, default `/ov_msckf/odomimu`) |
| How | `flight_window.py` cuts the **flight window** from the recording. That is from 3 s before the vehicle climbs 0.3 m above its resting height, to 1 s after touchdown. It writes the trimmed trajectory pair. Then `sim-real-eval trajectory` computes ATE and RPE over that pair. |
| Writes | `window.json` (window, airborne time, path length, whole-recording ATE for contrast); `estimate.tum`, `ground_truth.tum`; `vio/vio.json`, `vio/vio.md` |
| Metrics | `ate_trans_m` (rmse, mean, median, p95, max), `ate_rot_deg`, `rpe_trans_m`, `rpe_rot_deg`, `scale_recovered`, `yaw_drift_deg_per_min`, `duration_s` |
| Exit | 0 or 1 COMPLETE; 137 (OOM) RESCHEDULE |

**Why the window matters.** Scoring the whole recording mixes in the parked
seconds before takeoff and after landing, and the shipped OpenVINS drifts
about 2 m/s² while parked. On XFS run 65, whole-recording ATE was 1.545 m and
flight-window ATE 0.817 m.

The resting height is read where the estimator starts, not at the top of the
bag. On XFS the vehicle once fell through unstreamed terrain and was put back
47 m higher, which made the whole recording look airborne.

### 2. `spawn-eval`: did the vehicle stay in the world?

| | |
| --- | --- |
| Question | Was the flight physically valid, or did the vehicle fall through the level? |
| Image | bridge (ROS 2 and rosbag2) |
| Input | the bag's `/ground_truth/odom` |
| How | `spawn_check.py` measures the fall rate: the drop over time with no horizontal motion. Anything above `max_fall_ms` (default 0.5 m/s) is a fall. |
| Writes | `spawn.json`: `spawn_ok`, `reason`, first, last and minimum z, `fall_rate_ms`, `horizontal_travel_m`, duration |
| Exit | 0 or 1 COMPLETE; it reports and never judges |

It exists because an XFS run spawned under the terrain and fell at 8.33 m/s.
Its ATE was a number about the fall.

### 3. `validate`: is the recording sound?

| | |
| --- | --- |
| Question | Can any score from this recording be trusted? |
| Image | bridge |
| Input | the bag, plus the run's role map (ground truth, estimate, clock topics) |
| How | `validate_recording.py` runs one check per defect this project has shipped (list below). A check whose inputs were not recorded is **skipped**, never failed. |
| Writes | `validation.json`: `valid`, `failed_checks`, `skipped_checks`, per-check detail, how each role was resolved |
| Exit | 0, 1 or 2 COMPLETE |
| Status | **advisory**: shown in the registry and on the scorecard (`valid`), not a gate |

| Check | Catches | The defect it came from |
| --- | --- | --- |
| `image_stamps` | non-monotonic, stalled or slow image stamps | capture-time stamping |
| `fresh_frames` | consecutive identical frames | a stalled publisher |
| `stereo_skew` | left and right stamped apart | unpaired stereo |
| `truth_valid` | duplicate stamps, jumps, low rate in ground truth | collider displacement |
| `truth_not_estimate` | "truth" that is the flight controller's own estimate | a whole bridge release (PX4 EKF as truth) |
| `fc_nominal` | failsafe or estimator reset in the flight controller | a wall, then a blind landing |
| `imu_gravity` | gravity on the wrong axis at rest | a frame sign |
| `imu_flu` | gyro axes disagreeing with the truth twist | a doubly-rotated twist |
| `twist_body_frame` | odometry twist not in the body frame (REP 105) | |
| `intrinsics` | `camera_info` not matching the calibration | |

### 4. `verdict`: the one decision

| | |
| --- | --- |
| Question | Given every report, did this run meet the CampaignSpec's gates? |
| Image | `sim_real_eval` (for Python; the script is `osmo/files/verdict.py`) |
| Input | every `*.json` the evaluate group produced, and `GATES` (the CampaignSpec's `evaluation.gates` as JSON) |
| How | spawn first: a spawn failure is a FAIL. Then each gate. `max` and `min` bounds are applied to the metric, found by name (`ate_rmse_m` = `ate_trans_m.rmse`, `ate_max_m`, `rpe_rmse_m`). Finally every gate that no report measured is a FAIL. |
| Writes | stdout `PASS:`/`FAIL:` lines; `verdict.json` (`mns.verdict.v1`: `rc`, one row per gate with value, bound, passed, detail; `missing`) |
| Exit | **0** gates met (COMPLETE) · **1** a real verdict, the stack ran and was wrong (FAIL, never retried) · **42** no evidence to judge, a platform failure (RESCHEDULE) |

The three exit codes are not interchangeable:
- **1** is a result about the thing under test.
- **42** says the platform failed to produce evidence. OSMO retries it.

The registry maps them to `passed`, `failed` with `gate_failed` or
`spawn_failed`, and `infra_failed` with `no_evidence`.

**If the file and the task disagree, the task wins.** The registry takes the
status from `verdict.json` unless the verdict task's own status says
otherwise. A file on disk can be stale; a task status cannot. Run 73 was once
recorded as passed from an earlier attempt's file.

### 5. `vio-stress`: the campaign-level scorer, on the host

| | |
| --- | --- |
| Question | The campaign's scorecard: per run and per segment, how did the estimator do, in the platform's vocabulary? |
| Image | `sim_real_eval` with `vio-stress`. The catalog's `-latest` predates it, so `MNS_SIM_REAL_EVAL_IMAGE` names one that has it. |
| Where | the host, after every run of the campaign is back (`campaign.py` `evaluate()`), once per `done` run |
| Input | vio-eval's trimmed pair (`--est`, `--gt`); the whole bag if there is none |
| Writes | `reports/<key>.json` and `.md`: `vio_stress.run` (ATE and RPE rmse, ATE max, scale, yaw drift, coverage, tracking gaps, recovery ATE, alignment), `.segments`, `.comparison` |
| Read by | `campaign.py status` (the scorecard columns), the registry's report artifacts |

It is the same scorer the compose runner uses (the platform's
`EVALUATORS = {"vio-stress": …}`), so a campaign reads the same whether it
flew on this host or on OSMO. Its ATE should equal vio-eval's, since it scores
the same pair. If they differ, one of them read stale evidence.

### 6. The run registry: where results are kept, not made

`campaign.py` writes the registry from the files above:
- the verdict's rows go to `gate_results`;
- vio-eval's headline numbers go to `metrics_summary`;
- validate's `valid` and `failed_checks` go on the run row;
- the reports go to `artifacts`.

It computes nothing of its own, except `failure_reason`, which classifies the
workflow's end.

## Where they run, and why

| Group | Tasks | Node | OSMO platform | Why |
| --- | --- | --- | --- | --- |
| `run` | discovery-server, sim, autopilot, bridge, vio, pilot, recorder, foxglove | GPU node `osmo-worker2` | `default` | sim needs the GPU; sim, bridge and vio mount `/workspace`, which only that node has |
| `evaluate` | vio-eval, spawn-eval, validate | service node `osmo-worker` | `cpu` | no GPU and no mounts needed; inputs come from object storage, which runs on that node |
| `aggregate` | verdict | service node | `cpu` | as above |
| host | vio-stress | this workstation | | the campaign-level scorer runs after the matrix |

The `cpu` platform is set up by `osmo/cpu-platform.sh`:
- it adds a pod template selecting `node_group: service`, and the platform;
- it loads the bridge and sim_real_eval images onto that node;
- the platform allows no host mounts, so a task that asks for `/workspace`
  there is refused at submit rather than starting on an empty path.

`campaign.py images` checks the evaluation images on both nodes.

**What moving them changed.** On the GPU node, a run's evaluation waited
behind the next run's flight gang: 138 s on XFS. Two evaluations then held
the node's cores long enough to delay the next gang by 31 s. Off the GPU node:
- in a queued campaign, each run's verdict no longer waits for the next
  flight, which saves about 2 minutes per run;
- the gaps between flights close;
- an evaluation can never stop a gang from fitting.

A **single** run gains nothing. XFS run 80, alone on the cluster, started
evaluating 34 s after its flight ended. On the GPU node with nothing queued
that took 8-9 s. The difference is pod start-up on the service node. It is a
latency gain for a matrix, not for one run, and not a throughput gain: on XFS
the flights already ran almost back to back.

Run 80 was the first flown this way: XFS wind-6 with native physics wind
(generator v1.0.1), flight group on `osmo-worker2`, evaluate and aggregate on
`osmo-worker`. It passed, ATE 0.445 m, with a 1.20 deg lean into the wind.

## Adding an evaluator

1. Add a task to the `evaluate` group with `inputs: [{task: recorder}]`,
   `resource: eval` or `eval-light`, and an image loaded on the service node.
2. Write one JSON report to `{{output}}`. Exit 0 or 1 as COMPLETE, whatever
   it measured.
3. `campaign.py` downloads it to `runs/<key>/eval/<task>/`. Add the task
   name to the download list in `run_one`.
4. If it should decide pass or fail, add the metric to the CampaignSpec's
   `evaluation.gates`. The verdict finds it by name in the reports. If the name
   is new, add an alias in `verdict.py`.
5. If it should appear in the registry's headline numbers, add it to
   `SUMMARY_METRICS` in `campaign.py`.
