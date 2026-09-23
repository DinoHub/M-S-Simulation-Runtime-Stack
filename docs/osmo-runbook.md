# Running a TEVV stack under OSMO

How to get from a clean workstation to a green OSMO run, and what to do when
one fails. [How the runtime stack maps onto OSMO](osmo-mapping.md) explains
*why* each piece is shaped the way it is; this page is the sequence.

The run this describes is `osmo/sim-bridge-vio.workflow.yaml`: a simulator, the
ROS 2 bridge and OpenVINS gang-scheduled as one group, with a lead task whose
exit code is the verdict.

## What it proves, and what it does not

**Proves:** a level pack mounts inside a pod; the runtime host renders headless
on a cluster GPU; the bridge reaches the simulator across pods over RPC; an
estimator under test consumes the bridge's topics; and a lead task turns all of
that into a single exit code.

**Does not:** fly. There is no autopilot and no pilot in the group, so the
vehicle sits at its spawn point. Nothing here exercises the recording gates,
`validate_recording`, or `sim-real-eval`.

## Once per workstation

Three commands need root, and nothing else does. The runtime's default has to
be `nvidia` because kind gives no way to select a runtime per node, and the
volume-mount form of GPU injection has to be enabled because that is the only
form kind can express:

```bash
sudo nvidia-ctk runtime configure --runtime=docker --set-as-default
sudo nvidia-ctk config --in-place --set accept-nvidia-visible-devices-as-volume-mounts=true
sudo systemctl restart docker            # stops every running container
sudo sysctl -w fs.inotify.max_user_instances=512
```

Check both halves before building anything, because the second failure mode is
silent — the node comes up with no GPU and every workflow queues forever:

```bash
docker info --format '{{.DefaultRuntime}}'                       # nvidia
docker run --rm -v /dev/null:/var/run/nvidia-container-devices/all \
  ubuntu:24.04 nvidia-smi -L                                     # lists the GPU
```

The CLIs live under `~/.local` and need no root: `kind`, `kubectl`, `helm`, and
the `osmo` client (whose installer hardcodes `/usr/local` and calls `sudo`, so
extract its embedded tarball instead — see [osmo-mapping.md](osmo-mapping.md)).
`nvkind` needs Go, also installable into `~/.local`.

## Once per cluster

```bash
osmo/setup-local-osmo.sh gpu        # or `cpu` to exercise the control plane only
```

That script is the whole recipe: the 6.3.1 chart tree, the cluster, KAI,
CloudNativePG, the GPU operator, a render gate that proves the chart names no
binary the published image lacks, the install, and four workarounds for
upstream bugs. It refuses to start the GPU variant unless docker's default
runtime is already `nvidia`.

Then log in and prove the control plane on its own before involving a stack:

```bash
osmo login http://localhost --method=dev --username=testuser
osmo pool list                                                   # default ONLINE, GPU 1
osmo workflow submit ~/OSMO-6.3.1/deployments/workflows/verify-gpu.yaml
osmo workflow query verify-gpu-1                                 # COMPLETED
```

### Three settings the chart does not apply

Each is needed once per cluster, and none of them is guessable from the error
it causes. `osmo config show <TYPE> > f.json`, edit, `osmo config update <TYPE>
[name] --file f.json`:

| Config | Change | Symptom without it |
| --- | --- | --- |
| `POOL default` | `platforms.default.allowed_mounts: ["/workspace"]` | *Task with platform: default does not allow mount: /workspace* |
| `WORKFLOW` | `credential_config.disable_registry_validation: ["docker.io", "registry-1.docker.io"]` | *Unable to authenticate for pulling image …* at submit, for an image the run never pulls |
| `POD_TEMPLATE default_compute` | `imagePullPolicy: IfNotPresent` on both containers | every task `FAILED_IMAGE_PULL` despite the image being in the node |

## Per run

```bash
# 1. Generate. stackgen decides what the run IS; the workflow only schedules it.
./product.sh cli runtime --scenario /workspace/scenarios/vio-osmo-condo --no-run

# 2. Put the images in the compute node's containerd store.
kind load docker-image --name osmo --nodes osmo-worker2 \
  dhdevspace/auto_mns:tevv-runtime-host-v1.0.0 \
  dhdevspace/auto_mns:tevv-airsim-ros2-bridge-humble-v1.0.0 \
  dhdevspace/auto_mns:vio-estimator-openvins-69488123

# 3. Validate, then submit.
osmo workflow validate osmo/sim-bridge-vio.workflow.yaml
osmo workflow submit   osmo/sim-bridge-vio.workflow.yaml
osmo workflow query    sim-bridge-vio-1
osmo workflow logs     sim-bridge-vio-1
```

`validate` is worth the habit: it checks the schema, the mounts and the images
without scheduling a pod, and it catches most mistakes in seconds rather than
after a five-minute UE5 boot.

A different scenario is `--set stack=<dir under generated/>`. A different
estimator is a different `extensions.mns.vio_estimator` block in the
ScenarioSpec — the workflow does not name OpenVINS anywhere except as an image
default.

## Reading a green run

```
PASS: odom (/ov_msckf/odomimu)            199.93 Hz
PASS: imu (/imu/data)                     200.03 Hz
PASS: cam_left (/camera/front/image_raw)   30.25 Hz
PASS: cam_right (/camera/front_right/…)    30.30 Hz
score rc=0
```

The floors are far below the nominal rates on purpose: this asserts the
pipeline is alive end to end, not that it is fast. A rate regression is a
different test with different thresholds.

Milestones worth grepping for in the `sim` task, in order. If the chain stops,
it stops at the thing that is wrong:

```
MNS_PACK_SET_MOUNTED environment=condo     the level pack mounted
MNS_ENVIRONMENT_READY id=condo             the level finished loading
MNS_SCENARIO_ORIGIN_READY                  the scenario origin resolved
MNS_VEHICLE_SPAWN name=Drone1              the vehicle exists
```

## Exit codes

The lead's exit code is the run's verdict, and the three values are not
interchangeable. This is the bridge's vocabulary, adopted rather than
reinvented:

| Code | Meaning | OSMO | Registry |
| --- | --- | --- | --- |
| `0` | every floor met | COMPLETE | — |
| `1` | a real verdict: the stack ran and was wrong | FAIL, **never retried** | FAILED |
| `42` | readiness timeout: the platform was not ready | RESCHEDULE | INFRA_FAILED |
| `137` | OOM | RESCHEDULE | INFRA_FAILED |

The `1` / `42` split is the point. A slow UE5 boot must never be recorded as a
stack failure, or developers end up chasing platform flakiness as their bug.

## When it fails

Every discovery failure in this system produces the *same* symptom — five tasks
`RUNNING`, an empty ROS graph — so the table is ordered by how to tell them
apart rather than by likelihood.

| Symptom | Cause | Fix |
| --- | --- | --- |
| Every task sits in `SCHEDULING`, forever, with no error | the gang does not fit one node — see below | shrink the group's CPU budget |
| Task `FAILED_IMAGE_PULL` | pod template forces a registry pull | `imagePullPolicy: IfNotPresent` (above) |
| `FAILED_SERVER_ERROR`, no pod | `RuntimeClass "nvidia" not found` | GPU operator missing, or the CPU shim in `setup-local-osmo.sh` |
| Rejected at submit: *does not allow mount* | platform `allowed_mounts` is empty | above |
| Rejected at submit: *Platform cpu does not exist* | there is only one platform, `default` | drop `platform:`; ask for a GPU with `gpu: 1` |
| Rejected at submit: *Extra inputs are not permitted* | `workflow.labels` does not exist in 6.3.1 | remove it |
| `sim` exits: *cannot create directory '/simrunner'* | the image runs as uid 1000 | stage under `/tmp` |
| `bridge` logs *Connection refused* then *PARTIAL REMAP* | it launched before the sim answered | dial-wait first; **this one warns rather than fails**, and the remap silently drops the camera renames |
| `vio` exits 127: *ros2: not found* | overriding `command` bypasses `/ros_entrypoint.sh` | source ROS in the script |
| Lead exits 1 on `AMENT_TRACE_SETUP_FILES` | `set -u` versus ROS's `setup.bash` | drop `-u` |
| Lead's graph is only `/parameter_events /rosout` | see below |
| PX4 boots, connects to the sim, then `Startup script returned with return value: 65280` | the image bakes `PX4_INSTANCE=1`; compose overrides it to 0 and the task did not | `PX4_INSTANCE: "0"` — see below |
| MAVROS does a `VER` round-trip with the FCU but `connected` never goes true; pilot gate times out | MAVROS on the router's `AirSim_Inbound` (14580), a single-peer server AirSim floods | `connection: {mavros_udp_port: 14555, mavros_local_port: 14556}` in the ScenarioSpec |
| Pilot reports touchdown; ground truth sinks at 0.7 m/s for the rest of the run | landed past the level's floor; PX4 commands land speed into nothing | traverse sized to the level (`fly_distance_m`); `spawn-eval` now reports it |

### A gang that does not fit looks exactly like a hang

Gang scheduling is all-or-nothing: if one task will not fit, the whole group
stays `PENDING` and retries indefinitely. There is no failure, no timeout that
fires quickly, and `osmo workflow query` shows every task `SCHEDULING`. The
reason is only in the pod events:

```bash
kubectl -n default describe pod <any pending pod> | grep -A6 Events:
#   PodSchedulingErrors: Resources were found for 6 pods while 7 are required
#   for gang scheduling ... 1 node(s) didn't have enough resources: CPU cores
```

**Budget the sidecar.** Every pod carries an `osmo-ctrl` container whose
request counts against the same node. The chart ships it at 1 CPU, so a
seven-task group spends 7 cores before any work — which is what took this
group past a 24-core node when the autopilot and pilot were added. Trimming it
once helps every workflow:

```bash
osmo config show POD_TEMPLATE > pt.json     # default_ctrl.spec.containers[0]
# requests: cpu 250m, memory 256Mi
osmo config update POD_TEMPLATE default_ctrl --file ctrl.json
```

Then size the tasks themselves for what they do — a task that waits on a topic
and commands a flight does not need the simulator's budget. Add the per-task
requests plus the sidecar and keep the total under the node's allocatable CPU,
with room for the evaluators that follow.

### PX4: one environment variable, five wrong theories first

PX4 SITL under OSMO exited 255 on every run while booting cleanly under
compose. Eliminated by runs before the cause was found: a race on the
trailing `mavlink status`; compose crash-looping too (it does not —
`RestartCount=0`); rcS's `[: Illegal number:` (appears with compose's exact
env; benign); the pod's long FQDN (a resolved IP failed identically); a CPU
limit (6 CPU failed identically).

The cause came from diffing the compose container's environment against the
task's, line by line. The PX4 image bakes `PX4_INSTANCE=1` into its ENV, and
the generated compose overrides it to `0` — which reads like a default being
restated, so it was the one variable the workflow did not carry. Left at 1,
`run_airsim_sitl.sh 0` launches px4 as instance 0 (it takes `$1` for `-i` and
never exports `PX4_INSTANCE`) while every router script, reading
`${PX4_INSTANCE:-0}`, builds instance 1: system ID 2, router 5761, mavlink
18571. `mavlink start` creates nothing, every stream fails, and the trailing
`mavlink status` returns non-zero into rcS.

The general lesson is the method, not the variable: **when a container works
under compose and fails under OSMO, diff the two environments before
theorising.** `docker exec <compose container> env | sort` against the task's
`environment:` block finds a missing override in a minute; five plausible
theories cost an afternoon.

### MAVROS: the router has an endpoint for it, and it is not AirSim's

With PX4 up, MAVROS did a `VER` round-trip with the FCU and then never set
`connected`. It was attached to `udp://:14560@<px4>:14580`. In the PX4
image's own router template, 14580 is `AirSim_Inbound` — *"must match
AirSim's ControlPortRemote"* — a `Mode=Server` endpoint, and the template
says of that mode: *"tracks a single peer, so a second client there would
steal the link."* AirSim floods it with HIL at hundreds of Hz; MAVROS on it
gets the replies to its own requests and almost none of PX4's broadcast
heartbeats, and `connected` is a heartbeat judgement.

The router binds `MAVROS_UDP` on **14555** for exactly this. Set per vehicle
in the ScenarioSpec under `connection:` (`VehicleConnection`, fields 8–9) —
no generator change needed to run. The generator default at
`stackgen/autopilots/px4.py:48` is a separate fix: its comment claims 14580
was verified to fly on an older PX4 image whose router did not bind 14555,
and the two drifted past each other.

Verified where it counts — on compose, the working path — before touching the
workflow: `CON: Got HEARTBEAT, connected. FCU: PX4 Autopilot` 35 s after the
stack came up, then OFFBOARD, armed, a 28 m traverse and a landing. The first
real flight of this scenario.

### The level has edges

That compose flight also showed the vehicle landing where condo has no floor:
`z` went from −0.7 m at touchdown to −178 m over the remaining recording at a
steady 0.7 m/s — PX4's land speed, commanded into nothing, because there was
nothing to detect touchdown against. The pilot reported "touched down" from
MAVROS's local frame the whole time. The spawn point's floor is measured
(`z = −1.22 m`, at rest); 28 m along +X there is none. Traverse distance is
part of a scenario's correctness, not a tuning knob, and `spawn-eval` now
reports "sank below its start after flying" as its own finding so the
trajectory evaluator cannot charge the descent to the estimator.

### The empty-graph family

Four distinct causes, one symptom. In the order they were found:

1. **`{{host:<task>}}` in `ROS_DISCOVERY_SERVER`.** Fast DDS parses that
   variable as a locator and accepts only an address, so a DNS name is rejected
   — *"Wrong locator passed into the server's list"* — and the participant then
   discovers nothing. Resolve it with `getent` first. The name is fine for RPC,
   which is ordinary TCP.
2. **`${VAR}` inside an XML locator field.** Fast DDS does not expand
   environment variables there. It fails to parse as an IPv4 literal and takes
   the whole profile down, after which the participant falls back to SIMPLE
   discovery. **The ROS 2 daemon swallows the parse errors**, so `--no-daemon`
   is the only way to see the cause.
3. **A plain CLIENT's graph holds only what it already matches.** So
   `ros2 topic list` returns almost nothing, and `ros2 topic hz` — which
   resolves a topic's type from the graph before subscribing — reports no data
   on a perfectly healthy run.
4. **QoS.** Sensor topics publish BEST_EFFORT and never match a default
   RELIABLE subscription.

Which is why the lead measures with a subscriber that names its own types
(`/tmp/score.py`) rather than asking the graph anything. To reproduce a
measurement by hand, exec into the lead's pod — containers are named after
their tasks:

```bash
kubectl -n default get pods -o custom-columns=NAME:.metadata.name --no-headers
kubectl -n default exec <pod> -c score -- bash -lc '
  source /opt/ros/humble/setup.bash; source /ws/install/setup.bash
  export ROS_DISCOVERY_SERVER=<discovery-server pod IP>:11811
  python3 /tmp/score.py'
```

## Transport: this runs on RPC, and SHM is not merely untested

`TRANSPORT=rpc`, deliberately and unavoidably.

**Unavoidable under OSMO.** iceoryx2 needs a shared `/dev/shm`, a shared
`/tmp/iceoryx2` *and the same IPC namespace*. OSMO `volumeMounts` can give two
tasks the same host path but not the same IPC namespace, and guarantees no
co-location, so shared memory cannot come up between separate tasks at all.
Testing it would mean running the simulator and the bridge as one task under a
supervisor, which redefines the profile.

**Excluded by the estimator, on compose too — for a reason that has expired.**
With `extensions.mns.vio_estimator` enabled, the generator forces
`ENABLE_VIO=false` (`stackgen/generators/compose.py:94-106`), which is the
switch that would otherwise turn on `enable_iceoryx_fisheye`. The comment there
gives the reason:

> the shipped bridge's iceoryx image subscriber dies on a duplicate
> `cadence_rate_hz` declaration, so the SHM route delivers nothing at all (the
> sim logs `NumRecipients=0` while publishing at 30 Hz)

**That defect is fixed.** TEVV-Airsim-ROS2-Bridge `896ab2f`, *"stop the fisheye
image subscriber aborting on startup"* (2026-08-08), removed the duplicate
declaration; it is on `main` and an ancestor of `459952ee`, the commit the
pinned `v1.0.0` bridge image was built from. So the generator's exclusion now
rests on a stale premise and should be re-tested and either lifted or
re-justified. `ENABLE_VIO` in the stack `.env` overrides the default either
way, which the generator comment states explicitly — so the experiment costs
one environment variable, not a code change.

**And this scenario has no fisheye camera.** `vio-osmo-condo` declares two
`image_type: 0` (Scene) pinhole cameras at 640×480, FOV 80, inherited from
`vio-reference`. The SHM path carries fisheye captures specifically.
`scenarios/contract-probe-condo/` is the nearest thing that does declare one
(FOV 190, 1344×1344, no estimator) — though its `environment:` block names the
ue582 pack ids and would need the same one-block edit `vio-osmo-condo` has.

So the honest position: **the fisheye SHM path is untested here**, and it was
untested before this work began. What blocks it is now two things rather than
three — the generator's estimator/SHM exclusion, and a scenario that declares
fisheye cameras on the right channel. Under OSMO a third applies regardless:
sim and bridge would have to be one task.

## Evidence, evaluators and the verdict

The run is three groups, sequenced by `inputs: [{task: <upstream>}]`, which
OSMO reads as *"cannot be scheduled until that task has COMPLETED"* and
validates for declaration order and cycles:

```
runtime   sim | bridge | vio | discovery | recorder=lead
   |  evidence complete
evaluate  vio-eval              <- one container per question
   |
aggregate verdict=lead          <- the only place pass/fail is decided
```

The split is the point. **Evaluators report; the aggregate judges.** An
evaluator's non-zero exit means the evaluator broke, which is a platform
fault, so `vio-eval` carries `COMPLETE: "0,1"` and only a crash reschedules.
The verdict comes from reading `eval/*.json`, and returns `42` when there is
nothing to read rather than passing an unjudged run.

Adding a question is a task in `evaluate` plus a gate in the aggregate.
Nothing else changes.

### Two traps that produce a green run with no meaning

Both were hit, and both are worse than an error because they pass.

**Do not record with `ros2 bag record`.** It resolves each topic's type from
the graph before subscribing, and a plain CLIENT's graph holds only what it
already matches — so it creates the bag, subscribes to nothing, and exits 0
with `message_count: 0`. The damage surfaces two groups later as the
evaluator reporting missing topics, which points at the evaluator rather than
the recorder. The recorder here names its types and writes through
`rosbag2_py.SequentialWriter`, and refuses to exit 0 on an empty bag.

This is the same root cause as `ros2 topic hz` and `ros2 topic list` returning
nothing — see the empty-graph family above. Stated once more because it keeps
arriving in new clothes: **under a discovery server, any ROS 2 tool that
infers a type from the graph will silently do nothing.**

**Check what `estimate` resolves to.** `sim-real-eval` addresses topics by
role. Its built-in `sim` profile *and* the stack's generated
`config/sim2real/topics.yaml` both map `estimate` to `/odom` — the bridge's
relay of the simulator's own kinematics, not the filter under test. Evaluating
that compares ground truth against a copy of itself and passes every time.
The ScenarioSpec declares the real answer as
`extensions.mns.vio_estimator.odom_topic`; stackgen does not yet write it into
the role map, so the workflow overrides it with an explicit `--topics` file.
Worth fixing at source.

### A note on where evidence lives

`/runs` is a stand-in. The platform expects a task to write `{{output}}` and a
downstream task to declare `inputs:`, which is also what any multi-node cluster
requires. On this control plane that upload does not land, for a reason worth
knowing since it is silent:

- The `workflow_data` credential has `addressing_style: null`, and with a
  custom `override_url` OSMO 6.3.1 defaults to **virtual-hosted** addressing
  (`src/lib/data/storage/backends/s3.py:153`). Control-plane pods escape it
  because the chart sets `AWS_S3_FORCE_PATH_STYLE=true` on them; **workflow
  task pods get no such variable.** localstack does not recognise
  `osmo.localstack-s3.osmo` as a virtual host, parses `workflows` out of the
  path as the bucket, and answers `NoSuchBucket`.
- And `src/cli/data.py:56` discards the result of `upload_objects()`, so
  `osmo data upload` exits 0 even when every file failed. The task logs
  *"All Outputs Uploaded"* and goes COMPLETED with nothing stored.

The fix is `addressing_style: path` on the three workflow credentials
(`osmo config update WORKFLOW`). The second item is a genuine 6.3.1 bug and
will bite on any transient upload failure; it is worth filing upstream along
with the chart gap that omits `addressing_style` from
`quick-start/templates/config-setup.yaml`.

## Four more ways a run lies, found on runs 25 and 26

| symptom | cause | fix |
| --- | --- | --- |
| workflow COMPLETED, no `pilot`/`autopilot` tasks, recorder ran the default 300 s | `osmo workflow submit --set a=1 --set b=2`: both `--set` and `--set-string` are `nargs="+"` and argparse keeps the **last** occurrence, so every value but the final one was dropped and the template rendered its defaults | one `--set` and one `--set-string`, each carrying all its values (`campaign.py: submit`) |
| a campaign asked for two runs planned one | the platform's `campaign plan --only` is `nargs="+"` too, so `--only a --only b` plans b alone | one flag, every key. Any `nargs="+"` option in these CLIs takes its values together, never repeated |
| verdict rc=0 with no estimate in the bag | the gate named `ate_rmse_m` (the scorecard's vocabulary), the trajectory report spells it `ate_trans_m.rmse`, and a metric found nowhere was a WARN | aliases for the platform's names; a gate no report measured is a FAIL |
| pilot: `position estimate available at z=-3.36 m`, then `arming refused` for three minutes | PX4 EKF height not settled at boot (`Preflight Fail: height estimate not stable`), pilot proceeded after a fixed 5 s; run 24 read 0.04 m and armed at once | pilot gate waits for six seconds of local height within a metre of zero and spanning under 15 cm; one retry on a refused arming |
| `campaign status` empty; `sim-real-eval: invalid choice: 'vio-stress'` | the `-latest` worker image on Docker Hub is from 2026-08-09, before the scorer existed; the platform runs it from source inside its own process | build the worker from `MnS-Integration-Platform/tools/sim_real_eval` and point `MNS_SIM_REAL_EVAL_IMAGE` at it; `campaign.py evaluate <id>` re-scores a manifest |

And one the task table cannot show: a pilot exits 0 or 1 as COMPLETED, so a
flight that never armed looks like a flight. The recorder now writes
`mission.json` beside the bag -- whether the pilot announced, its exit code,
message counts -- and the executor reads it before calling a run `done`.

Runs 27 and 28 finished the set. The readiness gate that waited for a
near-zero EKF height hung forever on a vehicle whose local origin PX4 had put
at 37.70 m: rock steady, nowhere near zero. The gate now asks only for
stillness (under 0.5 m of span over six seconds), gives up after two minutes
and flies anyway -- the arming retry is there for exactly that -- and says out
loud what it is waiting for, which run 27 could not, having buffered its
stdout into a pipe that the gang tore down.

Then run 28 flew the route and reported 1.602 m against a 1.0 m gate. That
number was true and useless: the same bag scores **0.046 m airborne** and
0.109 m over takeoff-to-landing. The ten seconds between touchdown and the
bag closing are the parked divergence, and they were most of the metric. So
`vio-eval` now cuts the flight out first -- takeoff minus three seconds to
landing plus one, from ground truth -- writes it as the TUM files
sim-real-eval already accepts, and scores that. The whole-recording number is
measured too and written to `window.json` as `whole_recording_ate_rmse_m`, a
name no gate matches, because the drift is a real property of this estimator
and only its *position in the metric* was the lie.

## The first flight's number was garbage, and why

Run 24 flew: MAVROS connected 11 s after the group came up, OFFBOARD, armed,
2.75 m, 8 m traverse, touchdown, 83 MB bag, three evaluators, a verdict. The
ATE was 5.5e4 m. Working back from that, under compose first, in the order
the evidence arrived:

| suspect | test | result |
| --- | --- | --- |
| OSMO itself | same scenario, same pilot, under compose | diverged identically -- exonerated |
| IMU body frame (the bug that cost three sessions in September) | gyro axes against body rates derived from ground-truth *orientation*, not the bridge's own twist | identity, 1.00/1.00/1.00 -- FLU, fixed |
| stereo rig | pixel diff and disparity between the two topics | 16 px at 2.9 m, identical stamps, correct intrinsics |
| camera timing | image stamp vs IMU stamp at receipt | capture-time stamps, 71 ms latency, 20 Hz |
| estimator config | diff against `tevv_ws/testing/calibration-campaign-v1-stereo` | byte-identical but for a renamed topic |
| the scene | `simGetImages` at four headings and two heights | see below |

The vehicle spawns in a 3.5 m bedroom whose walls are 2.2 m tall and have no
ceiling above them. The corridor mission climbs to 2.75 m. Above the walls the
stereo pair looks across the whole apartment and into the sky: every feature
is at infinity, translation is unobservable, and the filter integrates the
accelerometer alone -- tens of metres per second within ten seconds. It then
landed on top of a wall (ground truth +2.24 m, exactly wall height); the
compose repeat missed the wall and fell out of the level.

A route sized to the room (`scenarios/vio-osmo-condo/routes/bedroom-loop.yaml`,
1.2 m up, metre legs, a yaw sweep, back to the start) gives **0.356 m ATE rmse
over a 7.0 m path** in flight. This is working VIO, in line with the reference
campaign's stereo numbers.

Then the second finding. Over the whole bag the same flight scores 1.75 m,
and the part after touchdown alone 2.08 m and climbing: the estimator this
stack ships holds well in motion and runs away at about 2 m/s^2 the moment
the vehicle is parked (`zupt_only_at_beginning: true` means nothing catches
it, and the stereo updates evidently do not hold it either). The compose
runner never sees this because it stops the bag when the pilot exits. Under
OSMO the recorder is its own task, so the pilot now publishes `/mission/done`
(latched) when it finishes, whatever its exit code, and the recorder closes
the bag four seconds later. `record_sec` is the cap for a pilot that never
gets there, and the whole window when nothing flies.

Two smaller things run 24 also taught: the recorder gates on ground truth,
not estimator odometry (OpenVINS publishes nothing under ZUPT, so gating on
it starts the bag at takeoff), and the `validate` evaluator was handed the
run directory instead of its `bag/`, exited 2, and under
`ignoreNonleadStatus: false` took the two evaluators beside it down with it
after they had written their reports. An evaluator's exit range is now
`0,1,2`: it reports, it never vetoes.

## A variant that changes nothing

The first full campaign -- calm and wind-6, two repeats each -- returned
0.04, 0.05, 0.07 and 0.07 m. A tight spread across a 6 m/s wind difference is
a result worth doubting, and it was wrong: the two variants ran identical
worlds.

The override was authored as `environment.conditions.weather.wind`, copied
from `scenarios/vio-reference`. The `Environment` message declares
`time_of_day` and `weather` and sets `unknown_fields: allow`, so the nested
spelling validates, survives into each run's materialised ScenarioSpec, and
is then dropped: `stackgen`'s `_normalize_conditions` reads the top-level
`conditions` and `environment.weather`, nothing else. Both variants generated
`"conditions": {}` and `SCENARIO_CONDITIONS_ENABLED=false`.

Authored at `environment.weather`, the same value reaches
`scenario_conditions.json` and the sim starts with
`SCENARIO_CONDITIONS_ENABLED=true`, so the path is live and only the spelling
was wrong. **`scenarios/vio-reference` is authored the inert way today**, which
means its published wind sweep varied nothing -- worth fixing there and worth
a schema change upstream, since a field that validates and does nothing is the
worst of both.

The general lesson for a campaign under any executor: a variant is not proven
by appearing in the spec. Diff two generated stacks before believing a sweep.

**And a live path is still not physical wind.** Reaching
`scenario_conditions.json` proves the sim received the value, not that the
vehicle felt it. The runtime host's `ScenarioConditionsSkyAdapter` (TEVV-Airsim)
sends `weather.wind` to visual weather only -- UltraDynamicWeather and AirSim's
weather effects -- and never calls AirSim's physics `setWind`; the generated
`settings.json` has no `Wind` either. The ground truth shows it: calm and wind-6
runs of this campaign hovered at the same tilt, 0.31-0.34 deg, where a vehicle
holding position in real wind must lean into it. The value is a 0-10 visual
intensity (at or below 1 read as a 0-1 fraction), not m/s. The tevv_ws harness
this replaced sent `wind N 0 0` over RPC, which was physics wind; the move to a
declared condition kept the traceability and lost the physics.
`tevv-campaign validate` now warns on any wind and fails the nested spelling
(platform `feat/campaign-target-osmo`). The second lesson: diff the vehicle's
motion, not only the stacks.

## Workflow anatomy

`osmo/sim-bridge-vio.workflow.yaml` is structure only: groups, tasks, images,
resources, environment, exit codes. Each task's script is a file in
`osmo/files/`, shipped with `localpath:` and run from `/tmp`. Only one file is
still inline: the four-line `topics.yaml` the evaluators read, because it
carries the `est_topic` value from the template. Every other value a script
needs arrives as an environment variable, so the scripts are plain shell and
Python that can be read and tested on their own.

| task | script | why it is the way it is |
| --- | --- | --- |
| discovery-server | `discovery_server.sh` | pods have no multicast, so DDS discovery needs a server; every ROS task is its client |
| sim | `sim.sh` | stages the generated stack's config into a writable dir (the checkout is mounted read-only, the image runs as uid 1000); takes the scenario's simulator flags from the stack's `host-launch-args.json` (weather, time of day, render overrides, seeds) and refuses to start if one names a file it did not stage, falling back to a fixed list for stacks generated before that file existed; renders off-screen, never `-NullRHI`, which drops the cameras; with ArduPilot, patches `UdpIp` to the autopilot pod's address, since AirSim sends ArduPilot its sensors |
| autopilot | `autopilot_px4.sh` / `autopilot_ardupilot.sh` | waits for the sim's RPC port instead of compose's fixed sleep; hands SITL the sim's IP, not its ~100-character pod name. PX4 needs `PX4_INSTANCE=0` (the image bakes 1). ArduPilot runs the launch script directly as root: the entrypoint's gosu drop makes SITL exit silently in a pod |
| bridge | `bridge.sh` | resolves the discovery server to an IP (Fast DDS rejects names there); waits for the sim's RPC, or the topic remap is silently partial; MAVROS on PX4's router `MAVROS_UDP` 14555 (not `AirSim_Inbound` 14580) or ArduPilot's TCP 5760 |
| vio | `vio.sh` | OpenVINS with the stack's generated config; publishes nothing until the vehicle moves |
| pilot | `pilot.sh`, `fly_mission_mavros.py`, `announce_done.py` | waits for MAVROS and a still EKF height; flies the route; retries a refused arming once; always announces `/mission/done`. Exits 0 or 1 as COMPLETE so a failed flight cannot take the gang down |
| recorder (lead) | `record.sh`, `record.py` | subscribes with named types (a plain DDS client's graph cannot resolve them); starts on ground truth, stops a few seconds after `/mission/done`; writes `mission.json`; with viz, holds the gang open after the bag closes |
| foxglove | `foxglove.sh` | a SUPER_CLIENT, so foxglove_bridge sees every topic; can never fail the run |
| vio-eval | `flight_window.py` + sim-real-eval | cuts the flight out of the bag (resting height read where the estimator starts), then scores that |
| spawn-eval | `spawn_check.py` | free fall, sinking after landing, or fell through and was put back |
| validate | `validate_recording.py` | the recording's own gates; writes the `validation.json` the scorecard reads |
| verdict | `verdict.py` | the only pass/fail: the CampaignSpec's gates, with scorecard names aliased to the report's; a gate nothing measured fails |

Editing rules that each cost a run to learn:

- **Jinja tags inline, never on their own line inside a command.** A tag line
  renders as a blank line, and a blank line after a trailing `\` ends the
  command -- every later argument dropped. Now moot for the scripts, which have
  no template in them; still true for `args:` blocks.
- **No Jinja in comments** -- they are rendered too; a literal `{% if %}` in a
  comment broke the whole template.
- **Booleans from `--set` are strings.** `--set viz=false` gives `"false"`,
  which a bare `{% if viz %}` calls true; test `viz | string | lower == 'true'`.
- **One `--set` and one `--set-string`, each with every value** -- both are
  `nargs="+"` and a repeated flag keeps only its last.

## Watching a run

A pod has no screen and the v1 runtime host has no Pixel Streaming (see
`docs/osmo-pixel-streaming.md`), so the view into a live run is the vehicle's
own: its cameras, TF, ground truth and the estimate, over Foxglove.

```bash
osmo/campaign.py run vio-osmo-condo --only calm-r1 --viz    # adds the foxglove task
osmo/campaign.py watch sim-bridge-vio-55                    # prints ws://172.27.0.4:30765
osmo/campaign.py watch sim-bridge-vio-55 --tunnel           # or: ws://localhost:8765, held
```

The address is the same on every run: the GPU node's IP on the `kind` docker
network (`osmo-worker2`, 172.27.0.4 on this cluster) and node port **30765**,
so a connection saved once in Foxglove keeps working. Kubernetes only hands
out node ports in 30000-32767, which is why it is not 8765 itself. If 30765 is
still held -- the previous run's pod not yet gone -- `watch` says so and takes
an assigned port instead; `--node-port N` picks another, `--node-port 0` always
takes an assigned one. The node IP changes only when the cluster is rebuilt.

`ws://localhost:8765` without a held tunnel needs the port mapped out of the
kind node, which kind only does at cluster creation: an `extraPortMappings`
entry `containerPort: 30765, hostPort: 8765` on a node in
`osmo/kind-osmo-cluster-config.gpu.yaml`, then a rebuild. Not done yet: 8765
is also what a compose stack's `foxglove_bridge_d1` publishes, and the two
would collide whenever both run.

In Foxglove: *Open connection -> Foxglove WebSocket -> the printed address*.
Useful panels: Image on `/camera/front/image_raw`; 3D with `/tf`,
`/ground_truth/odom` and `/ov_msckf/odomimu`; Plot on
`/ground_truth/odom.pose.pose.position.z`. Measured on run 55, through either
path: camera 30 Hz (~28 MB/s), IMU and estimate 200 Hz, ground truth 50 Hz,
173 topics. From another machine: `ssh -L 8765:<printed ip:port> <this host>`
and connect to `ws://localhost:8765`.

**How it reaches you.** foxglove_bridge runs in the gang as one more ROS 2
node, subscribing to the other pods over DDS. By default `watch` puts a
NodePort Service in front of that pod and exits: the address is the pod's
node on the kind network, which this host routes to. The Service is owned by
the pod, so Kubernetes deletes it with the pod -- nothing to keep running,
nothing to clean up. `--tunnel` is the fallback: a `kubectl port-forward` to
localhost, held until the run ends.

**How long it stays up.** Normally the gang ends a minute after touchdown,
when the recorder (the lead) closes the bag. On a viz run the recorder then
holds the gang for viewers: the foxglove task publishes `/viz/viewers` (TCP
connections on its port, read from `/proc/net/tcp`, so it counts NodePort and
tunnel clients alike), and the run is released once nobody has been connected
for `viz_idle_sec` (60 s) or after `viz_hold_sec` (600 s) at most. Connect
within a minute of the flight ending and it stays as long as you do. The
evidence is final before the hold starts; only the verdict waits.

What makes it work, each learned the hard way:

- **`watch` never touches OSMO's API.** On the quick-start deployment
  `osmo workflow port-forward` and `osmo workflow query` both answered `504
  upstream request timeout` while a gang was loading. Pods are found by
  OSMO's labels (`osmo.workflow_id`, `osmo.task_name=foxglove`).
- **It waits for the port, not the pod.** OSMO's sidecar holds a task's
  command until the whole gang is up, so the container is running before
  foxglove_bridge listens.
- **The foxglove task is a SUPER_CLIENT**; everything else is a plain client.
  foxglove_bridge offers what the graph shows, and a plain client's graph
  holds only what it already matches.
- **The bridge is the Foxglove SDK server** (`foxglove.sdk.v1`). An old
  client offering only `foxglove.websocket.v1` is refused with HTTP 400.
- **The hold must not spin the recording node**, whose writer is closed by
  then (runs 48-49 died that way); it watches `/viz/viewers` on a new node.
- **A viewer can never fail a run** (`COMPLETE: "0-255"`).

`--chase-cam` (only with `--viz`) adds a third-person camera 1.5 m behind and
0.8 m above the drone, pitched down 20 degrees, on `/chase_Scene/image`. It
shares the bridge's one combined image budget with the stereo pair -- on run
49 the front camera fell from 30 Hz to 18 Hz -- so those runs go to their own
`<campaign>-viz` manifest and never sit in the scored one.

After a run, no connection needed: `runs/<key>/bag/bag_0.mcap` opens in
Foxglove directly -- ground truth against the estimate, TF, and the
camera_info stamps that show the rate the estimator was fed.

## ArduPilot, and XFS

Both run now. `vio-osmo-condo-ardupilot` scored 0.058 m on the bedroom route
(PX4: 0.05-0.07 m); `vio-osmo-xfs` flew the full 51 m yard box and scored
1.53 m (compose, same route: 1.02 m). Each took its own set of fixes, all of
the same kind as before -- the flow reporting success on a run that did
nothing useful.

| symptom | cause | fix |
| --- | --- | --- |
| ArduPilot armed, route "flown", ground truth never left (0, 0, 0), pilot exit 0 | GUIDED ignores setpoints until a takeoff command; the pilot's route path never sent one | MnS-Integration-Platform#109: take off to the route's first altitude, enter the route there |
| after that fix, a 1.2 m route flown at 3.4 m | `relative_to_start` read the origin after the takeoff | #109: origin read once, on the ground |
| PX4 on the XFS route: `Disarmed by auto preflight disarming`, never moved | PX4 disarms if not airborne within `COM_DISARM_PRFLT` (10 s); the reference route holds on the ground 8 s then climbs over 3 s | #109: PX4 enters a route 2 s before its first ascent |
| ArduPilot task exits 1 after "Loaded defaults", nothing on stderr | the image's entrypoint drops to uid 1000 with gosu when started as root; a pod starts as root, compose never does (`user: ${HOST_UID}:${GID}`). Same binary, args and pod: root flies at 333 FPS, gosu exits 1 | the task runs `run_ardupilot_airsim.sh` directly, as root. Why the dropped process dies is not isolated |
| AirSim never feeds ArduPilot | ArduPilot is the one connector the sim dials *out* to (UDP), at the generated settings' compose hostname | the sim task resolves the autopilot pod and patches `UdpIp` |
| MAVROS on `udp://:14550@` for both autopilots | an if-tag on its own line inside the bridge's backslash-continued `ros2 launch` renders as a blank line and ends the command; every later argument dropped | conditionals inline; both renders checked for a complete command |
| XFS: free fall at 8.33 m/s from spawn | the v1 XFS terrain sits tens of metres *above* the NED origin; the origin and the v2 build's yard coordinate (z = 72) are both underground | spawn measured by holding the vehicle 160 m up for the terrain to stream in and dropping it: ground at NED z = -18.70 at (423, -906); spawn at -21.0 |
| XFS yard: reference route's first leg is 10 m east | a container stack stands 4 m east of the spawn | the box mirrored west (`routes/yard-box.yaml`) |
| XFS run 41: 10.4 m ATE, PX4 local origin 36 m before takeoff | World Partition streams terrain in after spawn; physics started first, the vehicle fell 49 m and was put back within 5 s. First and last samples agreed, so spawn-eval passed it | spawn-eval flags a drop of more than 5 m below the resting height; run 41 now reads `spawn_ok: false`. The race itself is the runtime host's to fix |
| XFS scored against the whole bag after a drop | the flight-window cut read the resting height from the first 50 samples, mid-fall | resting height read where the estimator starts, which is on a still vehicle by construction |

The recorder now also carries the two `camera_info` topics: one small
message per frame, stamped like the frame, so the camera rate the estimator
was fed is in the evidence (run 42: a steady 30 Hz, the bridge's cap). The
1.0 m gate was sized for condo's 7.5 m path; on XFS's 51 m it is a 2% drift
bound. Left as it is, and worth deciding per level.

## Authoring for OSMO: what you edit, and what the run reads from it

Two files are yours, per campaign, under `scenarios/<campaign>/`. Everything
under `generated/` is rewritten on every run; an edit there is lost.

- **`ScenarioSpec.yaml`** is one world to fly: the level, the vehicle and its
  rig, the weather, the estimator. It reaches the run only as the files the
  generator writes from it.
- **`CampaignSpec.yaml`** is an experiment over that world. `scenario:` points
  at the ScenarioSpec; `variants[].overrides` are ScenarioSpec fragments merged
  into it once per run; `repeats` and `seeds` say how many runs. `mission`,
  `evaluation` and `recording` never become part of a ScenarioSpec -- under
  OSMO they reach the run only as values `osmo/campaign.py` passes at submit.

```
CampaignSpec --scenario:--> ScenarioSpec (base)
   | variants[].overrides merged by `campaign plan`
   v
generated/campaigns/<id>/<run_key>/ScenarioSpec.yaml --`runtime --no-run`--> stacks/<run_key>/config/*  --> the tasks
CampaignSpec mission / evaluation --osmo/campaign.py submit--> --set / --set-string --> the workflow's values
```

### CampaignSpec, field by field

| field | workflow value | what it changes |
| --- | --- | --- |
| `scenario` | -- | the base spec `campaign plan` merges into |
| `variants[].overrides` | -- | anything a ScenarioSpec can say, per variant; see the next table for what of it reaches a task |
| `repeats`, `seeds` | -- | how many run keys. A seed is written into the run's spec as `seed:` and becomes the object-clutter seed in `scenario_runtime.json`, which the sim reads |
| `mission.autopilot` | `autopilot` | which autopilot task runs (PX4 or ArduPilot) and the bridge's MAVROS link (UDP 14555 or TCP 5760). **Must agree with the ScenarioSpec's `runtime.profile`**; nothing checks, and a mismatch flies one autopilot against a sim configured for the other |
| `mission.trajectory` | `route_b64` | the route, compiled to base64 JSON for the pilot. Absent: the workflow's corridor mission, whose distance, altitude and speed are workflow defaults, not spec fields |
| `mission.timeout_s` | `record_sec` | a cap on the recording; it normally ends 4 s after the pilot's `/mission/done` |
| `evaluation.gates` | `gates_json` | the verdict task's pass/fail. Scorecard names are aliased (`ate_rmse_m` -> `ate_trans_m.rmse`); a gate nothing measured fails |
| `evaluation.inputs.est_topic` | `est_topic` | the estimate the evaluators score. Keep it equal to the estimator's `odom_topic` |
| `evaluation.evaluator` | -- | the campaign-level scorer (vio-stress), run on this host after the matrix; writes `reports/` |
| `extensions.mns.omega` | -- | `tier` and `verifies`, into the manifest only |
| `mission.source`, `mission.done` | **ignored** | the pilot is always an external task; the run is done when it says so |
| `recording.topics`, `recording.keep` | **ignored** | the recorder's topic list is fixed in `osmo/files/record.py` |

### ScenarioSpec, block by block

| block | generated file (under `stacks/<run_key>/config/`) | read by |
| --- | --- | --- |
| `environment.{id,version,artifact_digest}` | `content-packs/resolved-pack-set.json` | sim; the level must be in the v1 pack store |
| `environment.weather`, `time_of_day`, `wind_mps` / `wind_from_deg` | `scenario/scenario_conditions.json`; `unreal-airsim/host-launch-args.json` where the generator emits it | sim |
| top-level `conditions.weather` / `conditions.time_of_day` | `scenario/scenario_conditions.json`, **instead of** `environment.weather` / `time_of_day`: the generator takes the top-level block whole and the two are not merged, so a variant's `environment.weather` under a base with `conditions.weather` is dropped and flies the base world. `time_of_day`, like `weather`, needs `enabled: true` or the host keeps the level's sky. The platform's `tevv-campaign validate` fails both (`conditions.shadowed.*`, `conditions.time_of_day.*`); `osmo/campaign.py` does not check | sim |
| `runtime.profile` | `unreal-airsim/settings.json` (vehicle type, ArduPilot's UDP pair) | sim |
| `vehicles[0].start` | `settings.json` | sim: the spawn |
| `vehicles[0].cameras`, `sensors`, `dynamics` | `settings.json`, `topic_names.yaml` | sim and bridge |
| `extensions.mns.vio_estimator.config_dir` | `vio/estimator_config.yaml`, the kalibr chains | vio |
| `seed` (set per run by `seeds`) | `scenario/scenario_runtime.json`, `object_clutter.yaml` | sim |

What a ScenarioSpec says and OSMO does **not** honour, each a silent no-op
rather than an error:

- **Images.** They are the workflow's `default-values`, not the generated
  `.env`. A different image is `--set <x>_image=...` or an edit there.
- **`runtime.features.mavros`.** The workflow starts MAVROS whenever the run
  flies.
- **`ros_domain_id`.** Every task is a client of the gang's discovery server.
- **`vio_estimator.launch_args`.** `vio.sh` launches stereo with two cameras,
  whatever the block says.
- **The vehicle's name.** The workflow's `vehicle` default is `Drone1` and the
  executor does not pass another; `runtime_name` must be `Drone1`.
- **A second vehicle.** Generated into `settings.json`, never flown or recorded.

### The whole tree

```
<checkout>/                                    the one the cluster mounts at /workspace
  scenarios/<campaign>/                        YOURS
    CampaignSpec.yaml
    ScenarioSpec.yaml
    openvins/                                  estimator_config.yaml, kalibr_imu_chain.yaml, kalibr_imucam_chain.yaml
    routes/*.yaml                              mission.trajectory
  osmo/
    campaign.py                                the executor: run, watch, status, evaluate, reindex
    sim-bridge-vio.workflow.yaml               structure and default-values (images live here)
    files/                                     one script per task -- behaviour is edited here, not in the YAML
    kind-osmo-cluster-config*.yaml, setup-local-osmo.sh
  generated/campaigns/<id>/                    WRITTEN FOR YOU
    campaign_manifest.json                     what `status` and the dashboard read
    <run_key>/ScenarioSpec.yaml                base + variant, merged by `campaign plan`
    stacks/<run_key>/
      config/unreal-airsim/                    settings.json, host-launch-args.json
      config/scenario/                         scenario_conditions.json, scenario_runtime.json,
                                               environment_parameters.json, object_clutter.*
      config/scenario-plugin/                  scenario_plugin.json
      config/content-packs/                    resolved-pack-set.json and the level pack
      config/vio/                              the estimator config, copied from openvins/
      config/topic_names.yaml                  the bridge's renames; never hand-write topic names
      config/{px4,ardupilot,sim2real,metrics}/
      docker-compose.yml, .env                 compose only; OSMO reads neither
      source/ScenarioSpec.yaml
    runs/<run_key>/                            the evidence, pulled back from object storage
      bag/bag_0.mcap                           opens in Foxglove directly
      mission.json                             announced, pilot_exit, recorded_sec, per-topic counts
      run.json                                 workflow id, task states, viz flags
      topics.yaml, validation.json
      eval/vio-eval/                           estimate.tum, ground_truth.tum, window.json, vio.json|md
      eval/spawn-eval/spawn.json
      eval/validate/validation.json
    reports/<run_key>.json|md                  vio-stress
  generated/campaigns/<id>-viz/                --chase-cam runs: the same, plus viz/<run_key>/ScenarioSpec.yaml
```

Typical changes, and where they go:

| to... | edit |
| --- | --- |
| add a condition to sweep | a new `variants[]` entry with an `overrides:` fragment |
| fly more or fewer times | `repeats`, or `seeds` |
| change the flight | `routes/<name>.yaml`, or point `mission.trajectory` at another |
| switch autopilot | `mission.autopilot` **and** `runtime.profile`, together |
| tighten or loosen the pass mark | `evaluation.gates` |
| another level or spawn | `environment` and `vehicles[0].start` |
| another estimator | `extensions.mns.vio_estimator` and its `config_dir`; the image in the workflow's `vio_estimator_image` |
| a task's behaviour | `osmo/files/<task script>` |

## A campaign: N runs, N workflows, one scorecard

```bash
osmo/campaign.py run      vio-osmo-condo            # materialise, generate, submit, collect, evaluate
osmo/campaign.py run      vio-osmo-condo --only calm-r1 wind-6-r1
osmo/campaign.py evaluate vio-osmo-condo            # score the manifest again, after a scorer fix
osmo/campaign.py reindex  vio-osmo-condo            # rebuild the manifest from the evidence on disk
osmo/campaign.py status   vio-osmo-condo            # the platform's own scorecard
```

A `--only` run updates the runs it touched and leaves the rest of the manifest
alone. It did not always: re-running one key used to rewrite the manifest with
that key alone, quietly dropping three finished runs off the scorecard. Which
is why `reindex` exists -- every bundle carries a `run.json` with what the
record needs, so the manifest is derivable from the evidence rather than the
other way round, and a lost or truncated one is rebuilt without re-flying
anything.

`osmo/campaign.py` is an executor, not a runner. The platform's campaign
runner already validates the spec, expands `variants × seeds × repeats`,
merges each variant's overrides into a materialised ScenarioSpec, generates a
stack per run, scores the evidence, and renders `status`. The executor reuses
every one of those by shelling into the product shell — `campaign plan`
writes every run's spec to disk, `runtime --no-run` generates its stack — and
replaces only the middle of the runner's `run_one`: instead of a compose
command it submits the workflow, polls it, and pulls the evidence back.

Where things land, and why the layout is fixed:

```
<checkout>/generated/campaigns/<id>/
  campaign_manifest.json      mns.vio_campaign_manifest.v1 — what `status` reads
  <run_key>/ScenarioSpec.yaml written by the platform (as root: the product shell is)
  stacks/<run_key>/           written by the generator (as this user); the workflow's `stack=`
  runs/<run_key>/             bag/, eval/*/…, validation.json, topics.yaml, run.json
  reports/<run_key>.json      the campaign-level evaluator (vio-stress)
```

`<checkout>` is the one the cluster mounts at `/workspace` (read from the
kind config), even when the executor runs from a worktree beneath it: that
checkout owns the pack store the generator needs, and a `stack=` value is a
path under its `generated/`. The stacks sit beside the run directories, not
inside them, because `campaign plan` creates those as root and the generator
runs as the invoking user.

Nothing downstream reads anything else, so an executor that lands these files
gets `campaign status` — and the dashboard's campaign view — unchanged.

Evidence comes out of object storage through the **localstack NodePort**,
with the aws CLI on the `kind` docker network. `osmo data download` cannot do
it from the host: the credential names `http://localstack-s3.osmo:4566`, a
cluster-internal name. Node IP and port are read live; a rebuilt cluster
changes both.

### Where the omega fields live

The root `CampaignSpec` message rejects unknown keys and the contract is a
separately vendored repository, so the fields `omega.yaml` has and
`mns.campaign.v1` lacks ride where the schema leaves room:

| omega | in the CampaignSpec | consumed by |
| --- | --- | --- |
| `tier`, `verifies` | `extensions.mns.omega` | manifest top level; a registry, when one exists |
| `evaluation.gates` | `evaluation.gates` (the message allows unknown fields) | the workflow's verdict, as `GATES` JSON |
| `platform.{sim,autopilot,middleware,comms}` | **not authored** — derived from `runtime.profile` | manifest and `run.json` |
| `matrix` | `variants[]` — structured overrides, strictly more expressive | the platform's own expansion |
| `repeats` | `repeats` — omega has no equivalent | the platform's own expansion |

The compose runner ignores all of it, which is the point: one file drives
both targets. Promoting `mns.omega.*` and `gates` to first-class fields is a
contract-repo change to propose once this has run.

### Run status, and what it does not mean

A run whose `recorder` task COMPLETED is `done` — it flew and produced
evidence — even if the workflow's own verdict FAILED. That keeps the compose
runner's semantics: run status says whether the flight happened; the
campaign-level evaluator and the gates say whether it was any good. A verdict
FAIL on a `done` run lands in `failed_checks` as `gate`, where the scorecard
shows it.

## Teardown

```bash
helm uninstall osmo -n osmo --wait
kind delete cluster --name osmo          # or: nvkind cluster delete --name osmo
```

The cluster holds roughly 10 GB of images in its node stores, so deleting it is
also the quickest way to reclaim that space.
