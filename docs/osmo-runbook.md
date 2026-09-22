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

**Also excluded by the estimator, on compose too.** With
`extensions.mns.vio_estimator` enabled, the generator forces `ENABLE_VIO=false`
(`stackgen/generators/compose.py:94-106`), which is the switch that would
otherwise turn on `enable_iceoryx_fisheye`. The comment there says why, and it
is a defect rather than a preference:

> the shipped bridge's iceoryx image subscriber dies on a duplicate
> `cadence_rate_hz` declaration, so the SHM route delivers nothing at all (the
> sim logs `NumRecipients=0` while publishing at 30 Hz)

So an estimator and SHM cameras are mutually exclusive in a generated stack
today, on compose as much as on a cluster.

**And this scenario has no fisheye camera anyway.** `vio-osmo-condo` declares
two `image_type: 0` (Scene) pinhole cameras at 640×480, FOV 80. The SHM path
carries fisheye captures specifically. Nothing here would exercise it even if
the transport allowed.

So: **the fisheye SHM path remains untested**, it was untested before this work
began, and three independent things would each have to change to test it — the
bridge's iceoryx subscriber defect, the generator's estimator/SHM exclusion,
and a scenario that actually declares fisheye cameras. Under OSMO a fourth
would: sim and bridge in one task.

## Teardown

```bash
helm uninstall osmo -n osmo --wait
kind delete cluster --name osmo          # or: nvkind cluster delete --name osmo
```

The cluster holds roughly 10 GB of images in its node stores, so deleting it is
also the quickest way to reclaim that space.
