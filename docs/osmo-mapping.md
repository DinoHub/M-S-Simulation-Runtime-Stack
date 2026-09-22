# The runtime stack under NVIDIA OSMO

How this repository maps onto [NVIDIA OSMO](https://nvidia.github.io/OSMO/),
what a generated stack would have to give up to run as an OSMO workflow, and
what is already in place. Companion to the bridge's own
[`docs/OSMO.md`](https://github.com/DinoHub/TEVV-Airsim-ROS2-Bridge/blob/feat/tevv-platform-contract/docs/OSMO.md),
which covers the same ground from the ROS 2 side.

Read `docs/how-it-fits-together.md` first if the phases and the generated-stack
layout are not already familiar. (That page arrives with PR #80; until it merges
the link below dangles.) To run one rather than understand one, go to
[Running a TEVV stack under OSMO](osmo-runbook.md). For how this repository's
declarations line up against the platform's `omega.yaml` contract, see
[Working backwards from omega.yaml](omega-mapping.md).

## 1. Where this repository sits

OSMO is an orchestrator: it takes a workflow of tasks, gang-schedules them as
pods across one or more Kubernetes clusters, injects their inputs from object
storage, and turns the lead task's exit code into a verdict.

This repository is not an orchestrator and does not become one. It is the
**distribution**: the image catalog, the pack locks, the scenario declarations
and the compose entry points that decide *what* runs. Today those are handed to
`docker compose` on one workstation. Under OSMO the same declarations are
handed to a cluster instead. Everything above the compose file — catalog,
channels, packs, scenarios, campaigns — is unchanged by the move. Everything
inside it changes a lot, because a generated stack leans on five things a pod
does not have.

| OSMO concept | Nearest thing here | Notes |
| --- | --- | --- |
| workflow | one flight of one scenario | A `ScenarioSpec.yaml` materialized into a generated stack. |
| group | the generated stack | All of the stack's containers, gang-scheduled together. |
| task | one compose service | `unreal-airsim`, `airsim_bridge_c1`, `ardupilot-drone-0`, ... |
| `lead: true` | none yet | Nothing in a generated stack owns the verdict; `validate_recording` runs after the fact. |
| `barrier: true` | `depends_on` | Weaker than what compose gives us — see §3. |
| `{{host:<task>}}` | compose service DNS | Direct equivalent. |
| `exitActions` | none | Exit codes are not currently a contract here. |
| platform / pool | none | Hardware selection; the channel is content, not hardware. |
| data layer (S3) | `.mns/<channel>/pack-store` | Content-addressed already; see §4. |
| digest-pinned images | `images/catalog.yaml` | Already the single source of truth; see §5. |
| `workflow.labels` | campaign run ids | Max 16, immutable, settable with `--label k=v`. |

**A channel has no OSMO equivalent, and should not get one.** A channel is a
coherent tuple of engine build, host capability id, image set and pack lock. In
OSMO that tuple survives as the set of digests passed to a workflow, not as a
platform or a pool — those select hardware. Pinning the channel into a pool
would make hardware and content inseparable, which is exactly the coupling
`images/catalog.yaml` exists to avoid.

## 2. What a generated stack actually asks for

Taken from `generated/4-fisherma-fly/docker-compose.yml`, an ArduPilot flight
over the Fisherman's Cabin level:

| Service | Image role | Host coupling it declares |
| --- | --- | --- |
| `unreal-airsim` | `tevv_runtime_host` | `ipc: host`, `/dev/shm`, `/tmp/iceoryx2`, `/tmp/.X11-unix`, `XAUTHORITY`, `DISPLAY` |
| `airsim_bridge_c1` | `ros2_bridge` | `ipc: host`, `/dev/shm`, `/tmp/iceoryx2`, X11 |
| `foxglove_bridge_c1` | `ros2_bridge` | `ipc: host`, `/dev/shm` |
| `ardupilot-drone-0` | `ardupilot` | config mount, output mount |
| `qgroundcontrol-x11` | `qgc` | X11 only, `shm_size: 2gb` |
| `iceoryx-init` | `tevv_runtime_host` | one-shot, `network_mode: none`, seeds `/tmp/iceoryx2` |

Three of those six cannot be lifted into separate pods as they stand.

## 3. The four things that do not translate

### 3.1 Shared memory forces the sim and the bridge into one task

iceoryx2 zero-copy needs a shared `/dev/shm`, a shared `/tmp/iceoryx2` **and
the same IPC namespace**. The generated stack gets all three from `ipc: host`.
OSMO `volumeMounts` can hand two tasks the same host path, but it does not
share an IPC namespace and gives no same-node guarantee, so the transport
cannot come up between two tasks.

Two ways out, both real decisions:

- **RPC transport on the cluster.** `TRANSPORT=rpc` already exists in the
  entrypoint. Cameras and lidar then ride the AirSim RPC path. Costs bandwidth
  and frame rate, and it is the path that was publishing nothing until the
  runtime host was rebuilt with iceoryx2 — worth re-measuring before adopting.
- **One task running both under a supervisor.** Keeps SHM, but the bridge stops
  being a task and becomes a process inside the sim task. That redefines the
  `cosys-airsim-ue5` profile the platform spec draws as two pods.

The bridge's `docs/OSMO.md` assumes the first. Nothing here contradicts that;
it is simply untested on this stack.

### 3.2 X11 is not available, but headless already is

Four of the six services mount `/tmp/.X11-unix` and pass `DISPLAY`. A pod has
no X server.

The runtime host is already fine: `AIRSIM_HEADLESS=true` maps to
`-RenderOffScreen -NoSound -Unattended -NoSplash` (`launch.sh:78`,
`Makefile:156`), cameras still render. **This contradicts the bridge's
`docs/OSMO.md`, which lists a headless sim entrypoint as outstanding
runtime-stack work — it exists.** What is outstanding is smaller: the generated
compose still emits `DISPLAY=${DISPLAY:-:0}` unconditionally
(`docker-compose.yml:69,149,260`), so the generator needs a headless mode that
drops the X11 mounts rather than merely adding a flag.

`qgroundcontrol-x11` is X11 by nature and has no place in an OSMO group. It is
an operator's window, not part of a run; a headless run flies through MAVROS.

### 3.3 `depends_on` conditions are stronger than a gang barrier

The stack orders startup three ways: `service_started`,
`service_healthy` (the bridge waits on the runtime host's health check) and
`service_completed_successfully` (everything waits on `iceoryx-init`).

OSMO offers `barrier: true`, which holds every task until every image is pulled
and every container is *ready*. That is weaker in one direction and absent in
another:

- Container ready is not the same as the AirSim RPC port answering. The bridge
  covers this itself with `AIRSIM_DIAL_WAIT_SEC` and exit 42, which is the
  right shape — a readiness timeout is infrastructure, not a stack failure.
  Nothing in this repository speaks that contract yet.
- There is no gang equivalent of a one-shot init task that must *finish* before
  the others start. `iceoryx-init` would have to become an init container
  inside whichever task owns `/tmp/iceoryx2`, which under §3.1 is the combined
  sim task anyway.

Also worth carrying over deliberately: `ignoreNonleadStatus` defaults to
`true`, which restarts a dead non-lead task alone while the rest of the run
continues. A bridge rejoining a mid-flight graph corrupts the recording
silently. Any workflow generated from here must set it to `false`.

### 3.4 Nothing here produces a verdict from an exit code

OSMO decides a group by its lead task's exit code. A generated stack has no
lead: it is flown, then `validate_recording` and `sim-real-eval` are run
against the recording afterwards, by the campaign runner or by hand.

Under OSMO that inverts. One task becomes the lead, runs the flight and the
gates, and exits with the verdict. The pieces exist in the platform
(`validate_recording.py`, `check_calibration.py`, `sim_real_eval/`); what is
missing is the exit-code contract that separates a stack failure from a
platform failure. The bridge already uses `0` / `1` / `42` for exactly that
split, and this repository should adopt the same three rather than invent a
fourth vocabulary.

## 4. Packs and the data layer

This is the part that fits OSMO better than compose does.

A pack is already a content-addressed blob: `PackStore` is
`index.json` plus `blobs/sha256/<digest>/`, and the lock names every pack by
`artifact_digest`. OSMO's data layer injects inputs into task containers from
S3-compatible object storage — the same shape, one level up. A pack store is a
local mirror of what would be a bucket.

Two frictions:

- **Staging is by hard link.** Since v1.0.0 the shell hard-links pack payloads
  from the store into `authoring-data` instead of copying them. Measured on the
  v1 channel: `du -sh .mns/v1` reports 15 GB, `du -sh -l .mns/v1` — which counts
  each link separately — reports 29 GB, and a staged 1.7 GB `.ucas` payload has
  `links=2`. The store and the staged tree cost one copy between them. Hard
  links need one filesystem; across tasks there is none, so under OSMO every
  task that needs a pack pays for its own copy and the 29 GB number is the one
  that applies.
- **Size.** Electric Dreams is 12.3 GB and is published in seven parts because
  of a release-asset limit. Injecting that per run is not viable; it wants to
  be a warm cache on the node, which OSMO does not model.

So the honest read: small packs map cleanly onto OSMO inputs, large level packs
want a node-local cache that is a platform decision, not a repository one.

## 5. Images: the catalog is already the right shape

`images/catalog.yaml` is one row per image with an explicit digest, and every
env file, image set and pack-lock pin is rendered from it and verified offline
on every PR (`tools/images.sh verify`). OSMO wants digest-pinned images from a
registry the cluster can reach. That is the same discipline, so the work is
rendering, not redesign: another generated output beside
`images/*.generated.env` that emits the `--set` pins a workflow needs.

Two caveats from recent history:

- The generated stack's `.env` refers to images by mutable tag
  (`ROS2_IMAGE=...ros2-bridge-humble-latest`). The catalog has the digests; the
  generator does not use them. Under OSMO that must change — a moving tag
  breaks reproducibility of a run.
- `tevv-runtime-host-v1.0.0` was republished in place with different content.
  On one workstation that is confusing; across a cluster that pulls per node it
  is a run that silently means two different things.

## 6. What is set up on this machine

Local OSMO quickstart, installed without `sudo` (everything under
`~/.local`, per the NVIDIA quickstart otherwise):

```bash
# CLIs -> ~/.local/bin (kind 0.30.0, kubectl 1.37.0, helm 3.19.0, osmo 6.3.1)
curl -sSLo ~/.local/bin/kind https://kind.sigs.k8s.io/dl/v0.30.0/kind-linux-amd64
curl -sSLo ~/.local/bin/kubectl "https://dl.k8s.io/release/v1.37.0/bin/linux/amd64/kubectl"
curl -sSL https://get.helm.sh/helm-v3.19.0-linux-amd64.tar.gz | tar xz && mv linux-amd64/helm ~/.local/bin/
chmod +x ~/.local/bin/{kind,kubectl,helm}

# The osmo CLI installer hardcodes /usr/local and shells out to sudo. Extract
# its embedded tarball instead: the payload is a plain tgz after the marker.
curl -fsSL https://raw.githubusercontent.com/NVIDIA/OSMO/refs/heads/main/install.sh | bash   # needs sudo
#   -- or, without --
V=$(curl -sSL https://github.com/NVIDIA/osmo/releases/latest/download/version.txt | tr -d '[:space:]')
curl -sSfL "https://github.com/NVIDIA/OSMO/releases/download/$V/osmo-client-installer-$V-linux-x86_64.sh" -o osmo-inst.sh
tail -n +$(awk '/^__ARCHIVE_BELOW__/ {print NR + 1; exit}' osmo-inst.sh) osmo-inst.sh | tar -xz -C ~/.local/opt
ln -sf ~/.local/opt/osmo/osmo ~/.local/bin/osmo

git clone https://github.com/NVIDIA/OSMO.git ~/OSMO
kind create cluster --config osmo/kind-osmo-cluster-config.yaml   # this repo
kubectl config use-context kind-osmo
```

Then KAI and CloudNativePG exactly as the quickstart gives them, and the OSMO
umbrella chart last. `osmo/kind-osmo-cluster-config.yaml` in this repository is
the CPU-only (Option B) cluster: three nodes, `control-plane` / `control-plane`
/ `compute` node pools, gateway NodePort 30080 mapped to host port 80.

### Do not use the umbrella chart from `main`

`deployments/charts/osmo` on `main` cannot install against any published image.
It says `appVersion: "6.3.1"` but was created on 2026-08-11, 50 days after the
6.3.1 release, and the label was never bumped. Its bootstrap Jobs run
`osmo-bootstrap`, `identity-bootstrap`, `internal-tls-bootstrap`,
`mek-lifecycle`, `service-auth-bootstrap` and `/osmo/bootstrap-step`, none of
which exist in `nvcr.io/nvidia/osmo/service:6.3.1` (same digest as `:latest`,
distroless, entrypoint `/usr/bin/shelless_ulimit`). No values combination
avoids them: `_bootstrap-gate.tpl:66` injects a `bootstrap-credentials` init
container into all eight consumer Deployments unconditionally. Pinning to an
older commit does not help either — at the `6.3.1` tag the directory does not
exist at all. `ci/deployment-test/check-head-chart-contract.sh:13-14` renders
that chart only against same-revision `nvstaging/osmo` images, which is why.

### What does work: the `quick-start` chart from the 6.3.1 tag

```bash
git -C ~/OSMO fetch --depth 1 origin refs/tags/6.3.1:refs/tags/6.3.1
git -C ~/OSMO worktree add ~/OSMO-6.3.1 6.3.1
helm dependency update ~/OSMO-6.3.1/deployments/charts/quick-start   # file://../service 1.3.1
kubectl label node osmo-worker node_group=service --overwrite        # the chart selects on
kubectl label node osmo-worker2 node_group=compute --overwrite       # node_group, not ours
helm upgrade --install osmo ~/OSMO-6.3.1/deployments/charts/quick-start \
  --namespace osmo --create-namespace --set global.osmoImageTag=6.3.1 \
  --set-string service.services.postgres.nodeSelector.node_group=service \
  --set-string service.services.redis.nodeSelector.node_group=service \
  --set-string service.services.localstackS3.nodeSelector.node_group=service \
  --wait --timeout 20m
osmo login http://localhost --method=dev --username=testuser
```

It bundles postgres 15.1, redis 7.0, localstack for S3 and envoy, and disables
oauth2-proxy and authz, so nothing has to be stood up by hand. Its bootstrap
Jobs run `alpine/k8s` and `alpine/curl`, never the OSMO image. Before
installing, render it and grep for the six binaries above — an empty result is
the gate that the chart and the image agree.

Four things still needed fixing by hand on 6.3.1; all four are upstream bugs,
not local misconfiguration:

| Symptom | Cause | Fix applied |
| --- | --- | --- |
| `osmo-backend-operator-token` exits 52 in a loop | The job talks to `http://osmo-service` directly, but the service answers **only** requests arriving through the Envoy gateway — it closes the connection otherwise, and `set -e` kills the job on curl's exit 52. `osmo-config-setup` in the same chart correctly uses `http://quick-start.osmo.svc.cluster.local`. | Recreate the job with the gateway URL. |
| ...and before that, the same job fails even on the create path | The chart pre-creates `backend-operator-token` holding `placeholder-token-will-be-replaced-by-job`, so the job takes the "test the existing token" branch into the same dead end. | Delete the placeholder secret so it takes the create branch. |
| Every workflow fails `FAILED_SERVER_ERROR` | The default pod template sets `runtimeClassName: nvidia`; a CPU-only kind cluster has no such RuntimeClass, so the API server rejects every pod with `RuntimeClass "nvidia" not found`. | `RuntimeClass nvidia` with `handler: runc` as a shim. The GPU operator supplies the real one. |
| Task `{{output}}` upload never lands | The data credential is created without `addressing_style`, so the client builds virtual-hosted URLs (`http://osmo.localstack-s3.osmo:4566/...`) that do not resolve. Setting `addressing_style=path` is accepted but not honoured in 6.3.1. | Give the localstack pod `hostname: osmo` / `subdomain: localstack-s3`, which makes the virtual-hosted name resolve. Fixes connectivity; the task-output upload is still a no-op — see below. |

### Where it stands

`hello_world.yaml` runs to `COMPLETED` and its task log comes back, so
scheduling, the gang machinery and log capture all work. Workflow specs, task
specs, events and logs are written to S3 by the control plane. On the GPU
cluster `verify-gpu.yaml` also completes, with `nvidia-smi` reporting the host
RTX 5080 and driver 580.173.02 from inside a scheduled pod.

**The task `{{output}}` round trip does not work.** `verify-object-storage.yaml`
gets `produce` to `COMPLETED` but nothing appears under
`workflows/<id>/produce/`, so `consume` finds an empty input. A lead task can
therefore still produce a verdict through its **exit code**, which is what the
workflow in this repository relies on, but anything that expects to hand a
`validation.json` to a later task through `{{output}}` needs this resolved or a
host `volumeMounts` path instead.

### Cluster settings a workflow needs, which no error explains

Three, all applied to the running control plane rather than the chart, and all
needed again on a fresh cluster:

| Setting | Why |
| --- | --- |
| `POOL` → `platforms.default.allowed_mounts: ["/workspace"]` | Empty by default, so any `volumeMounts` entry is refused with *"Task with platform: default does not allow mount"*. |
| `WORKFLOW` → `credential_config.disable_registry_validation: ["docker.io", "registry-1.docker.io"]` | Submission validates that every image can be pulled. The images are `kind load`ed into the node and the repository is private, so validation fails on an image the run never pulls. |
| `POD_TEMPLATE` → `default_compute` containers get `imagePullPolicy: IfNotPresent` | Otherwise every task fails `FAILED_IMAGE_PULL` despite the image sitting in the node's containerd store. |

### Three assumptions the bridge's own OSMO notes get wrong

Worth stating because they are easy to inherit:

- **`workflow.labels` does not exist in 6.3.1.** The spec model forbids extra
  keys, so a labelled workflow is rejected outright. Labels arrived later.
- **There is no `cpu` or `gpu` platform.** The quick-start chart provisions
  exactly one, named `default`; naming another fails with *"Platform cpu does
  not exist in pool default"*. A GPU is requested with `gpu: 1` on the resource.
- **`{{host:<task>}}` cannot be used for DDS discovery.** Fast DDS in Humble
  parses `ROS_DISCOVERY_SERVER` as a locator and accepts only an address, so
  the Kubernetes DNS name is rejected — *"Wrong locator passed into the
  server's list"* — and the participant then discovers nothing at all. Resolve
  it with `getent` first. The name is fine for RPC, which is ordinary TCP.

The failure mode of that last one is the reason to care: every task reaches
`RUNNING`, the simulator mounts its level and spawns the vehicle, and the run
looks healthy while nothing on the ROS graph can see anything.

### Measuring a ROS graph from another pod

Getting the lead to see the run took four attempts and is worth writing down,
because every wrong answer looks the same from outside — an empty graph under
a healthy stack.

- **Fast DDS does not expand `${VAR}` inside XML locator fields.** It fails to
  parse as an IPv4 literal and takes the whole profile with it, after which the
  participant falls back to SIMPLE discovery. The ROS 2 daemon swallows the
  parse errors, so `--no-daemon` is the only way to see the cause. The bridge
  repo's `config/fastdds-discovery-superclient.xml` has this defect.
- **A plain CLIENT's graph holds only what it already matches.** So
  `ros2 topic list` returns `/parameter_events /rosout`, and `ros2 topic hz`
  — which resolves a topic's type from the graph before subscribing — reports
  nothing at all.
- **SUPER_CLIENT lists the topics but did not deliver data** on 6.3.1;
  `ros2 topic info` showed the publisher as `_NODE_NAME_UNKNOWN_`.
- **What works is a subscriber that names its own types.** No graph lookup, no
  profile, no daemon: it subscribes, the discovery server relays the match to
  the publisher, and data flows. QoS is part of it — sensor topics publish
  BEST_EFFORT and never match a default RELIABLE subscription.

Measured from the lead's pod on `sim-bridge-vio-11`, which COMPLETED:

```
PASS: odom (/ov_msckf/odomimu)            199.93 Hz
PASS: imu (/imu/data)                     200.03 Hz
PASS: cam_left (/camera/front/image_raw)   30.25 Hz
PASS: cam_right (/camera/front_right/...)  30.30 Hz
score rc=0
```

### Host constraints worth knowing

- `fs.inotify.max_user_instances` is 128; kind recommends 512. Raising it needs
  `sudo sysctl`. Three nodes have come up without it.
- The GPU path (Option A) needs `nvkind` and the GPU operator, neither
  installed. It is the prerequisite for running the runtime host in-cluster.
- Root filesystem is the real constraint, though less than first feared: the
  runtime host image is **2.32 GB**, not tens of GB, because the level lives in
  the pack and not the image. Sim, bridge and estimator together are about
  9.5 GB, and a kind node keeps its own copy of each.

## 7. A staged path, if this is pursued

Each stage proves one thing and is the prerequisite of the next. Stages 0 and 1
are the machine; 2 onward are this repository.

| Stage | Proves | Owned by |
| --- | --- | --- |
| 0 | CLIs and a kind cluster | done, §6 |
| 1 | OSMO control plane, `verify-hello.yaml` completes | blocked upstream, §6 |
| 2 | The bridge as a cluster task against a host sim | TEVV-Airsim-ROS2-Bridge, its `osmo/bridge-certify-hostsim.workflow.yaml` |
| 3 | The runtime host in-cluster, headless, on GPU | **done** — `osmo/sim-bridge-vio.workflow.yaml` |
| 4 | Sim, bridge and an estimator as one group, with a verdict | **done** — `sim-bridge-vio-11` COMPLETED, lead exit 0 |
| 4b | A flight, and the recording gates over it | not started — no autopilot or pilot in the group yet |
| 5 | A campaign as N labelled workflows | the campaign runner; one materialized ScenarioSpec per workflow, already how it works |

Stage 3 is done, by a route worth recording: rather than teach the generator a
headless profile, the workflow schedules a stack the generator has already
written and overrides only what a pod forbids — `-RenderOffScreen` for
`-windowed`, `/tmp` for `/simrunner` because the image runs as uid 1000, and
the checkout bind-mounted at `/workspace` so the twelve generated config files
are read rather than restated. That keeps stackgen the single source of truth
for what a run is; the workflow only decides where it runs.

Two generator changes remain independently worth making, whether or not OSMO
is adopted: emit image digests instead of moving tags, and a headless profile
that drops the X11 mounts rather than only adding the flag.
