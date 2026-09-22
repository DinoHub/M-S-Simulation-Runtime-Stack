# The runtime stack under NVIDIA OSMO

How this repository maps onto [NVIDIA OSMO](https://nvidia.github.io/OSMO/),
what a generated stack would have to give up to run as an OSMO workflow, and
what is already in place. Companion to the bridge's own
[`docs/OSMO.md`](https://github.com/DinoHub/TEVV-Airsim-ROS2-Bridge/blob/feat/tevv-platform-contract/docs/OSMO.md),
which covers the same ground from the ROS 2 side.

Read `docs/how-it-fits-together.md` first if the phases and the generated-stack
layout are not already familiar. (That page arrives with PR #80; until it merges
the link below dangles.)

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

### Known blocker, upstream

The umbrella chart on `NVIDIA/OSMO` `main` runs an `identity-bootstrap` binary
that the published image does not contain:

```
Error: failed pre-install: job osmo-identity-token-migration failed: BackoffLimitExceeded
  exec: "identity-bootstrap": executable file not found in $PATH
```

`nvcr.io/nvidia/osmo/service:6.3.1` and `:latest` are the same digest
(`sha256:33ee79c7...`) and predate the commit that introduced the binary
(`9364e8dc`, *Consolidate OSMO bootstrap into one sequenced Job* (#1414),
2026-09-18). Setting `imageTag=6.3.1` changes nothing because it is the same
image. Three ways forward: pin the chart to a commit before #1414, pull the
released chart from NGC with an API key, or wait for the image to catch up.

### Host constraints worth knowing

- `fs.inotify.max_user_instances` is 128; kind recommends 512. Raising it needs
  `sudo sysctl`. Three nodes have come up without it.
- The GPU path (Option A) needs `nvkind` and the GPU operator, neither
  installed. It is the prerequisite for running the runtime host in-cluster.
- Root filesystem is the real constraint. The cluster alone took the workstation
  from 25 GB free to 11 GB; a UE5 runtime-host image is tens of GB and will not
  fit in a kind node's image store without reclaiming space first.

## 7. A staged path, if this is pursued

Each stage proves one thing and is the prerequisite of the next. Stages 0 and 1
are the machine; 2 onward are this repository.

| Stage | Proves | Owned by |
| --- | --- | --- |
| 0 | CLIs and a kind cluster | done, §6 |
| 1 | OSMO control plane, `verify-hello.yaml` completes | blocked upstream, §6 |
| 2 | The bridge as a cluster task against a host sim | TEVV-Airsim-ROS2-Bridge, its `osmo/bridge-certify-hostsim.workflow.yaml` |
| 3 | The runtime host in-cluster, headless, on GPU | **here** — generator headless mode (§3.2), digest pins (§5), pack injection (§4) |
| 4 | A whole generated stack as one group, with a verdict | **here** plus the platform — lead task and exit-code contract (§3.4) |
| 5 | A campaign as N labelled workflows | the campaign runner; one materialized ScenarioSpec per workflow, already how it works |

Stage 3 is the first that needs code in this repository, and the smallest
useful piece of it is the one that is independently valuable: make the
generator emit digests instead of moving tags, and a headless profile that
drops the X11 mounts instead of only adding `-RenderOffScreen`. Both improve
the compose path on their own merits, whether or not OSMO is adopted.
