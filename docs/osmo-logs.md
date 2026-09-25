# Logs on OSMO: what Loki and Alloy are for

This page explains what Loki and Alloy do for a TEVV run on OSMO, what they
deliberately do not do, and how to tell whether they are doing it. For how to
install them and the exact settings, see the runbook's "Logs: Loki and Alloy"
section ([osmo-runbook.md](osmo-runbook.md)). For where logs sit among
everything a run leaves behind, see [osmo-run-flow.md](osmo-run-flow.md).

## The job, in one sentence

Every line printed by every task of every OSMO run, and by the OSMO control
plane, is kept for 30 days. You can find it by workflow in seconds, and it is
complete even when OSMO's own log copy is not.

That sentence is the contract. Everything below explains how it is met, and
what falls outside it.

## Why we need it

Before Loki, a run's logs lived only in OSMO, read with `osmo workflow logs`.
That copy fails in three ways:

| Problem | Measured |
| --- | --- |
| **It drops lines under load** | a 20,000-line burst came back as 10,001 lines on one read and 1,040 on another; condo run 64's simulator printed 7,675 lines and OSMO returned 966. The simulator's own log says `Maximum logging rate exceeded, N lines have been dropped!` |
| **It is one task at a time, with no search** | "which task printed a Traceback?" meant fetching every task's log and grepping by hand |
| **Nothing joins it to a campaign** | OSMO knows workflows, not campaigns, run keys or attempts |

A failed run's logs are the first thing anyone opens. Losing 87% of the
simulator's lines exactly when it is busiest makes a failure hard to explain.

## The pieces, and who does what

```
 GPU node / service node                          service node              host
 +------------------------------+
 | workflow pod (one per task)  |
 |   task container ------------+--> /var/log/pods/.../<task>/0.log
 |   osmo-ctrl sidecar ---------+--> /var/log/pods/.../osmo-ctrl/0.log
 +------------------------------+        |
 | OSMO control-plane pods -----+--> /var/log/pods/osmo_*/...
 +------------------------------+        |
                                         v
                              Alloy (one per node) --push--> Loki ---query---> Grafana :3000
                                                             (30 days)        campaign.py
                                                                  ^
                              run registry (Postgres) --workflow_ref joins-+
```

### Alloy: the collector

**Its job.** Alloy runs one pod on each worker node. It reads the node's
container log files while pods are alive, labels each line, and pushes it to
Loki.

**How it tells lines apart.** OSMO runs a task's command under `osmo_exec`,
which re-emits the command's stdout and stderr on the container's stderr, with
a `YYYY/MM/DD HH:MM:SS` prefix. So the node's container log holds every line
the task printed. It is the complete copy; OSMO's own copy is not.

Alloy asks Kubernetes about each pod and turns what it learns into labels:
- OSMO's pod labels `osmo.workflow_id` and `osmo.task_name`;
- the container, which says whether a line came from the task or from OSMO's
  `osmo-ctrl` sidecar;
- the namespace, which separates workflow pods (`default`) from the control
  plane (`osmo`).

It strips `osmo_exec`'s prefix so lines read as the task printed them, and it
keeps the container runtime's nanosecond timestamp.

**Why it must be running before the run.** OSMO deletes a task's pod seconds
after the task ends, and the pod's log files go with it. Alloy reads as lines
are written. It cannot reach back for a pod that is already gone, so a run
flown while Alloy was down has no logs in Loki. The on-disk fallback below
still covers failed runs.

**What it must not do.** It must not take room from the run.
- A run's gang needs about 22 of the GPU node's 24 cores, and KAI places the
  whole gang or nothing.
- So Alloy requests 50m CPU and 64Mi. If a gang ever stops fitting after a
  change to Alloy, that is the first thing to check.

It does not read the control-plane node, where only Kubernetes itself runs.

### Loki: the store

**Its job.** Loki keeps the lines for 30 days and answers queries by label
and by content. It is one process with a filesystem store on a 20Gi volume, on
the service node next to the run registry. So it is up whether or not the GPU
node is.

**How it stays small.** Loki indexes only a few labels. Everything else it
scans.

| Indexed label | Values | Why indexed |
| --- | --- | --- |
| `job` | `osmo/workflow`, `osmo/control-plane` | the first cut: a run's tasks, or OSMO itself |
| `task` | `sim`, `bridge`, `vio`, `pilot`, `recorder`, `verdict`, ...; or `osmo-service`, `osmo-worker`, `osmo-logger`, ... | the question is almost always about one task |
| `source` | `task`, `sidecar`, `control-plane` | the task's own output against OSMO's plumbing |
| `namespace`, `stream` | `default`/`osmo`; `stdout`/`stderr` | cheap, occasionally useful |

The workflow ID and pod name are attached to each line as **structured
metadata**. They are not labels. A new run therefore adds no new streams: a
run is a filter (`| workflow_id="…"`), not a new index entry. That matches the
architecture spec (section 9.3), and it keeps the index the same size no
matter how many runs a campaign has.

**How big it gets.** A condo run is about 11,000 lines, roughly 1.2 MB. At that
rate 20Gi holds far more than 30 days of any realistic schedule on this
workstation.

**What it must not do.**
- **Decide pass or fail.** Gates are `verdict.json`, stored in the run
  registry. Logs explain a result; they never decide one. A "FAIL" string in a
  log is evidence to read, not a verdict to count.
- **Hold metrics or events.** Rates, timings and counters belong to
  Prometheus (not built yet). The simulator's structured event stream belongs
  to ClickHouse. Loki holds text.
- **Hold artifacts.** Bags, trajectories and reports stay in `runs/<key>/` and
  object storage.

### Grafana: where people look

Two dashboards read Loki and the registry together:
- **"TEVV campaign progress"**: its workflow column links to that run's logs,
  framed to the run's own time window.
- **"TEVV run logs"**: pick a run by campaign, run key and attempt (from the
  registry). It shows:
  - the run's registry row;
  - the verdict's gate decisions;
  - errors and failures across every task;
  - lines per task over time;
  - any single task's output;
  - OSMO's sidecar;
  - control-plane lines that name the workflow.

### The run registry: the join

Nothing in the cluster knows a campaign, a run key or an attempt, because
OSMO 6.3.1 cannot put custom labels on pods. The registry's `runs` row maps
`workflow_ref` (the OSMO workflow ID) to all three. That is why the run-logs
dashboard picks runs from the registry rather than from Loki.

### campaign.py: the copy that stays with the evidence

For a run that did not pass, `campaign.py` saves every task's lines into
`runs/<key>/logs/<workflow>/<task>.log` and `<task>.sidecar.log`. It fetches
them from Loki, and falls back to `osmo workflow logs` when Loki cannot be
reached. The files outlive Loki's 30 days and travel with the bag. A passed
run's logs stay in Loki only.

## What is not covered

| Gap | Why | What covers it instead |
| --- | --- | --- |
| Runs from before 25 Sept 2026, or flown while Alloy was down | their pods and container logs are gone; OSMO's copy is incomplete, so no backfill | failed runs' logs on disk (from `osmo workflow logs`); the registry |
| Logs older than 30 days | retention | `runs/<key>/logs/` for failed runs |
| The host side of a campaign (`campaign.py`'s own output, `vio-stress` on the host) | not in the cluster | the terminal, and the files each step writes |
| A task that prints so much it slows OSMO | the sidecar ships every line to OSMO before the task can end. A 20,000-line burst held its pod 16.5 minutes. Loki does not change that | print less. The simulator's per-frame `UnrealImageCapture` warnings are the obvious candidate |
| High availability | one Loki, one volume, a workstation cluster | the on-disk copies of failed runs |

## Using it

In Grafana: http://localhost:3000/d/tevv-run-logs. In Explore, or from a
terminal (`curl` to `http://<osmo-worker IP>:31100/loki/api/v1/query_range`):

```
# the verdict's decisions for one run
{job="osmo/workflow", task="verdict"} | workflow_id="sim-bridge-vio-64" |~ "^(PASS|FAIL|verdict rc)"

# anything that looks like a failure, in any task
{job="osmo/workflow", source="task"} | workflow_id="sim-bridge-vio-64" |~ "(?i)error|traceback|fatal"

# how loud each task was, per minute
sum by (task) (count_over_time({job="osmo/workflow"} | workflow_id="sim-bridge-vio-64" [1m]))

# was the run stuck before it started: OSMO's sidecar (inputs, barrier, uploads)
{job="osmo/workflow", source="sidecar"} | workflow_id="sim-bridge-vio-64"

# the control plane's view of a run (submit, scheduling, cleanup)
{job="osmo/control-plane"} |= "sim-bridge-vio-64"

# across runs: every PX4 failsafe this week
{job="osmo/workflow", task="autopilot"} |= "Failsafe activated"
```

A query that must return every line, such as `campaign.py` saving a run's
logs, must page by splitting time windows. Loki's `limit` is not "the earliest
N lines" across streams, so paging from the last timestamp skips lines. On
run 64 that returned 4,293 of 7,675. `campaign.py`'s `loki_logs()` does it the
right way.

## Is it working?

| Check | Healthy |
| --- | --- |
| `kubectl -n tevv get pods` | `loki-0` 1/1, one `alloy-*` 2/2 per worker |
| `curl http://<osmo-worker IP>:31100/ready` | `ready` |
| During a run, `sum by (task) (count_over_time({job="osmo/workflow"} \| workflow_id="<wf>" [5m]))` | every running task counting up |
| After a run, the sim task's line count in Loki | equal to what the task printed. On run 64 the saved `sim.log` and Loki's count were both 7,675 |
| A gang still schedules | the run leaves PENDING as it did before Alloy |

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| A run has no lines at all | Alloy was not running on that node during the run | `kubectl -n tevv get pods -l app.kubernetes.io/name=alloy -o wide`; `kubectl -n tevv logs <alloy pod> -c alloy` |
| Lines from some tasks but not others | those pods ran on a node without a ready Alloy | as above, for that node |
| Grafana's logs panels are empty but Loki answers `curl` | the Grafana container is off the `kind` network, or the datasource points at an old node IP | re-run `osmo/setup-local-osmo.sh observability` |
| Pushes rejected in Alloy's log (`429`, rate limit) | a burst above the ingestion limits | raise `limits_config` in `osmo/observability/loki/values.yaml`, then re-run the install |
| Duplicate-looking lines after an Alloy restart | Alloy's read positions live in the pod and restart with it, so it re-reads live pods' files | none needed: Loki drops exact duplicates (same stream, timestamp and line) |

## Changing it

| What | Where |
| --- | --- |
| What is collected, labels, line cleanup | `osmo/observability/alloy/config.alloy` |
| Alloy's resources, mounts | `osmo/observability/alloy/values.yaml` |
| Retention, limits, storage size | `osmo/observability/loki/values.yaml` |
| The host address | `osmo/observability/loki/service.yaml` (NodePort 31100) |
| Dashboards | `osmo/observability/grafana/*.json` |

Apply any change with `osmo/setup-local-osmo.sh observability`. It runs `helm
upgrade` on both charts at pinned versions and republishes the dashboards, and
re-running it on an unchanged cluster changes nothing.
