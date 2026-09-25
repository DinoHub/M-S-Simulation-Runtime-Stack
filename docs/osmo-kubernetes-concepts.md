# OSMO and Kubernetes for this repository

This page covers what a new developer needs to understand before the
[OSMO runbook](osmo-runbook.md) makes sense. It is not a general Kubernetes
course. Each concept is here because a run in this repository depends on it,
and most of them come with the way they have already broken a run.

Read it top to bottom once. After that, the runbook is the sequence to follow
and [osmo-mapping.md](osmo-mapping.md) explains why the stack is shaped the
way it is.

## 1. The layers

A run passes through six layers, and every problem sits in exactly one of
them. Knowing which one saves most of the debugging.

```
your shell         osmo/campaign.py, osmo CLI, kubectl
   |
docker (host)      the "kind" docker network, 172.27.0.0/16
   |
kind nodes         osmo-control-plane 172.27.0.3
                   osmo-worker        172.27.0.2   OSMO's own services
                   osmo-worker2       172.27.0.4   the GPU node: every task pod
   |
Kubernetes         pods, services, labels, the nvidia.com/gpu resource
   |
KAI scheduler      gang scheduling: all of a group's pods, or none of them
   |
OSMO               workflows, groups, tasks, pools, object storage
   |
your workflow      osmo/sim-bridge-vio.workflow.yaml + osmo/files/*
```

The IPs are the ones on this cluster. They change when the cluster is rebuilt,
so the tools read them live and never hardcode them.

## 2. Kubernetes, the parts this repo uses

### Cluster and nodes, as kind builds them

A **node** is a machine that runs pods. **kind** (Kubernetes IN Docker) makes
each node a docker container on the host, so "a node" here is a container
named `osmo-worker2`, and its "network" is the `kind` docker network. Two
consequences come from that:

- The host can reach every node's IP directly, which is why a NodePort URL
  like `ws://172.27.0.4:30765` works from this machine and not from any other.
- Anything a node needs from the host must be declared when the cluster is
  created (`osmo/kind-osmo-cluster-config.gpu.yaml`), and adding it later
  means rebuilding the cluster. That covers `extraMounts` (the checkout at
  `/workspace`, the GPU) and `extraPortMappings` (host port 80 to
  OSMO's gateway).

### Pods and containers

A **pod** is the unit Kubernetes schedules: one or more containers sharing a
network namespace, and therefore sharing `localhost`. Every OSMO task is one
pod holding two containers: your task's container and OSMO's `osmo-ctrl`
**sidecar**. The sidecar downloads inputs, uploads outputs, streams logs and
holds your command until the whole group is ready. That last job is why a pod
shows Running while your script has not started yet.

Pods are disposable. Nothing a pod writes to its own filesystem survives it,
so evidence has to leave through OSMO's outputs to object storage before the
pod ends.

### Namespaces

A **namespace** scopes names. The ones on this cluster:

| namespace | holds |
| --- | --- |
| `default` | every workflow task pod, and the Foxglove Services `watch` creates |
| `osmo` | OSMO's control plane (section 4) |
| `kai-scheduler` | the gang scheduler |
| `gpu-operator` | the NVIDIA device plugin, validator and exporters that make the GPU schedulable |
| `kube-system`, `local-path-storage`, `cnpg-system` | Kubernetes itself, volume provisioning, the Postgres operator |

Most `kubectl` commands take `-n <namespace>`. A pod that "doesn't exist"
usually exists in another namespace.

### Labels and selectors

**Labels** are key/value tags on any object, and a **selector** finds objects
by them. OSMO labels every task pod with `osmo.workflow_id` and
`osmo.task_name`, which is how the tools find a run's pods without asking
OSMO:

```bash
kubectl get pods -n default -l osmo.workflow_id=sim-bridge-vio-57
kubectl get pods -n default -l osmo.workflow_id=sim-bridge-vio-57,osmo.task_name=foxglove
```

### Networking: pod IPs, DNS, Services

- Every pod gets its own IP from the node's pod range (10.244.1.0/24 on the
  GPU node). Pods reach each other by pod IP. **The host cannot** reach a pod
  IP, so something has to bridge from the host into the pod network.
- Pods get DNS names. OSMO gives each task one and hands it to the other tasks
  through the `{{host:<task>}}` token. These names are long (around 100
  characters), and some software refuses a name where it expects an address:
  Fast DDS's discovery-server locator, and SITL's sim hostname. So the task
  scripts resolve the name to an IP with `getent hosts` first.
- A **Service** gives a stable way in to the pods its selector matches:
  - `ClusterIP` (the default): reachable only inside the cluster.
  - `NodePort`: also listens on a port in **30000-32767** on every node's IP.
    This is how the host reaches a pod: node IP plus node port. OSMO's own
    gateway (30080), object storage (`localstack-s3`, 30035) and the Foxglove
    view (30765) all come in this way.
- `kubectl port-forward` tunnels a local port to a pod's port through the API
  server. There is nothing to create, but the process must stay running, and
  one refused connection ends the tunnel for good.
- There is **no multicast** between pods, and DDS discovers peers by multicast
  by default. That is why every workflow runs a DDS discovery server and every
  ROS task is configured as its client.

### Ownership and garbage collection

An object can name an **owner** in `metadata.ownerReferences`. When the owner
is deleted, Kubernetes deletes what it owns. The Foxglove Service is owned by
the foxglove pod, so it disappears when the run ends, with no cleanup step and
no process to keep alive.

### Resources and the GPU

Each container **requests** CPU, memory and storage. The scheduler places a
pod only on a node with that much still unrequested. A GPU is an **extended
resource**, `nvidia.com/gpu`, which the GPU operator's device plugin
advertises. `osmo-worker2` offers `cpu: 24`, `nvidia.com/gpu: 1`, and about
62 GiB of memory.

Requests add up across a whole group, sidecars included (250m CPU each). The
`run` group requests about 21 of the node's 24 cores. One more task, or one
bigger request, and the group can no longer be placed (section 3).

### Images

A node runs images from its own containerd store, not the host's docker.
`kind load docker-image --nodes osmo-worker2 <image>` copies one in. The pod
template's `imagePullPolicy: IfNotPresent` makes the node use that copy
instead of trying a registry it may have no credentials for. Without it,
every task fails with `FAILED_IMAGE_PULL` despite the image being present.

### Volumes

`/workspace` in a sim or bridge pod is the host checkout. It travels
host, then kind node (`extraMounts`), then pod (a mount the OSMO pool must
allow, `allowed_mounts: ["/workspace"]`). The pod sees it read-only and runs
as uid 1000, which is why `sim.sh` copies what it needs into `/tmp` before
starting the simulator.

## 3. Gang scheduling: KAI

Kubernetes' default scheduler places pods one at a time. That fails for a
run: a bridge placed without its simulator waits forever, and it keeps the
CPUs the simulator needed. **KAI** (NVIDIA's scheduler, in `kai-scheduler`)
places a **PodGroup** as a whole or not at all. OSMO makes every workflow
group a PodGroup.

What it means in practice:

- **A group that does not fit looks exactly like a hang.** Every pod stays
  Pending, and the workflow shows as queued. The reason is on the PodGroup,
  not the pods: `Resources were found for 6 pods while 7 are required for gang
  scheduling`. When a run "never starts", check requests before anything else.
- **Queues and priority.** Workflows queue in KAI's `default-queue`. A NORMAL
  priority workflow preempts a LOW one: the LOW run is killed mid-flight, not
  paused.
- There is one GPU, so one run at a time. A second submission waits in the
  queue, which is expected and not a hang.

## 4. OSMO

### What it is

OSMO is NVIDIA's workflow orchestrator for robotics and simulation. You
describe a run as groups of tasks in one YAML file, and it turns that into
pods on a Kubernetes cluster. It handles gang scheduling, moves data between
tasks, retries, logs and final status. Here it is the local **quick-start**
deployment from the 6.3.1 chart (`osmo/setup-local-osmo.sh`).

### The control plane, in namespace `osmo`

| component | role |
| --- | --- |
| `osmo-service` | the API the CLI talks to |
| `quick-start-envoy` | the gateway: NodePort 30080, mapped to host port 80, so `osmo login http://localhost` |
| `osmo-worker`, `osmo-osmo-backend-listener` / `-worker`, `osmo-agent` | turn workflows into pods and track them |
| `osmo-delayed-job-monitor` | timeouts and delayed transitions |
| `osmo-logger`, `osmo-router` | task log streaming and routing: what `osmo workflow logs` reads. It drops lines under load; the complete copy is Loki's, see [osmo-logs.md](osmo-logs.md) |
| `osmo-ui` | the web UI behind the workflow "Overview" URL |
| `postgres`, `redis` | state |
| `localstack-s3` | object storage (a local S3) for task inputs and outputs |

The gateway is the weak point on this deployment. `osmo workflow
port-forward` and `osmo workflow query` returned `504 upstream request
timeout` while a group was loading, so `watch` goes straight to `kubectl`
instead.

### Pools, platforms and configs

A **pool** is a set of compute a workflow runs on (`default` here, 1 GPU). A
**platform** inside it sets what a task may do there, such as which host
paths it may mount. Cluster-wide behaviour lives in named configs, edited with
`osmo config show|update`: `POOL`, `WORKFLOW`, `POD_TEMPLATE`. Three settings
the chart does not apply are listed in the runbook ("Three settings the chart
does not apply"). None of their failures says what is actually wrong.

### A workflow file

```yaml
workflow:
  name: sim-bridge-vio
  resources:                     # named resource shapes tasks refer to
    sim: {cpu: 6, memory: 16Gi, gpu: 1}
  groups:
  - name: run                    # a group = a gang: scheduled together
    barrier: true                # no task's command starts until all are ready
    ignoreNonleadStatus: false   # any task failing ends the group
    tasks:
    - name: recorder
      lead: true                 # the lead's exit ends the group
      image: "{{ bridge_image }}"
      resource: light
      command: ["/bin/bash", "/tmp/record.sh"]
      environment: {SIM_HOST: "{{host:sim}}"}
      files: [{localpath: files/record.sh, path: /tmp/record.sh}]
      exitActions: {COMPLETE: "0", FAIL: "1", RESCHEDULE: "42,137"}
  - name: evaluate               # runs after `run`, on its outputs
    tasks:
    - name: vio-eval
      inputs: [{task: recorder}] # the recorder's {{output}} appears as {{input:0}}
default-values:                  # template variables, overridable at submit
  bridge_image: ""               # images are passed at submit, from images/catalog.yaml
```

The concepts in it:

- **Group.** Tasks that must be alive together. Groups run in order, and a
  later group reads an earlier one's outputs. This workflow has three groups:
  `run` flies, `evaluate` scores, `aggregate` gives the verdict.
- **Lead.** The task whose exit ends its group. In `run` it is the recorder,
  so the run lasts as long as the recording, not as long as the simulator.
- **Barrier.** The sidecar holds every task's command until the whole group
  is up, so a task that starts has all its peers. It does not mean a peer is
  *ready*: the sim can still be booting, so the scripts also dial-wait for the
  ports they need.
- **`exitActions`.** How a task's exit code is read: `COMPLETE`, `FAIL`, or
  `RESCHEDULE` (run again). The convention here: 0 passed, 1 a real result
  (never retried), 42 the platform was not ready (retry), 137 out of memory
  (retry). The pilot's 1 is mapped to COMPLETE so a failed flight still
  produces evidence. The foxglove task maps 0-255 to COMPLETE so a viewer can
  never fail a run.
- **Data.** A task writes to `{{output}}`. The sidecar uploads it to object
  storage, and a later task that declares `inputs: [{task: X}]` gets it at
  `{{input:0}}`. `osmo/campaign.py` then pulls the evidence out of storage
  through the localstack NodePort.
- **`files` with `localpath`.** Copies a file from beside the workflow into
  the task at submit time. This is why the task scripts are plain files in
  `osmo/files/` and not inline YAML.
- **Templating.** The file is **Jinja** rendered at submit.
  `default-values` are the variables, and `--set k=v` / `--set-string k=v`
  override them. Tokens OSMO fills in itself look like Jinja but aren't:
  `{{host:task}}`, `{{output}}`, `{{input:0}}`. The traps:
  - `--set` and `--set-string` each accept many values, but only the last
    occurrence of a repeated flag counts. Pass one of each, carrying every
    value.
  - `--set viz=false` arrives as the string `"false"`, which Jinja treats as
    true. Compare as a string.
  - Comments are rendered too. A Jinja tag in a comment is parsed.
  - A tag on its own line inside a `\`-continued command renders a blank line
    and ends the command.

### Lifecycle and statuses

A workflow goes from submitted to queued (waiting for KAI to place the group)
to running, then one group at a time to a final status: `COMPLETED`, or one of
the `FAILED_*` statuses (`FAILED_IMAGE_PULL`, `FAILED_UPSTREAM`,
`FAILED_CANCELED`, and so on). Each task has its own status. The workflow's
status is a platform verdict: `COMPLETED` means the tasks exited as their
`exitActions` allow. It does not mean the flight was any good. The verdict
task and the evidence say that (see the runbook, "Two traps that produce a
green run with no meaning").

### The CLI you will use

```bash
osmo login http://localhost --method=dev --username=testuser
osmo pool list                                   # is the GPU there and free?
osmo workflow validate <file> --set ...          # render and check, schedules nothing
osmo workflow submit   <file> --set ... --set-string ...
osmo workflow list
osmo workflow query  <id>                        # per-task status
osmo workflow logs   <id> [--task <name>]
osmo workflow cancel <id>
osmo config show POOL | WORKFLOW | POD_TEMPLATE
```

## 5. How one run uses all of it

`osmo/campaign.py run vio-osmo-condo-ardupilot --only calm-r1 --viz`:

1. **Spec to stack, on the host.** The product shell's `campaign plan` merges
   the variant into a per-run ScenarioSpec, and `runtime --no-run` generates a
   stack from it under `generated/campaigns/<id>/stacks/calm-r1/`. That
   directory is visible inside pods as `/workspace/...`.
2. **Submit.** The executor submits the workflow with one `--set` (stack,
   autopilot, viz, ...) and one `--set-string` (route, gates, record cap).
   OSMO renders the Jinja and creates a PodGroup of 8 pods.
3. **Gang placement.** KAI places all 8 on `osmo-worker2` once CPU, memory and
   the GPU are free. The sidecars release every command together.
4. **Discovery.** Each ROS task resolves the discovery server's DNS name to an
   IP and registers with it. No multicast is involved.
5. **Flight.** The sim renders off-screen on the GPU. The autopilot and bridge
   dial-wait for its RPC port. The pilot flies the route and announces
   `/mission/done`. The recorder stops 4 s later and writes the bag to
   `{{output}}`.
6. **Watching (with `--viz`).** `watch` finds the foxglove pod by label and
   creates a pod-owned NodePort Service on port 30765. Foxglove on the host
   connects to `ws://172.27.0.4:30765`. While someone is connected, the
   recorder holds the group open.
7. **Evaluate, aggregate.** The next groups read the recorder's output as
   `{{input:0}}`, score the flight window, and apply the CampaignSpec's gates.
8. **Collect.** The executor copies each task's output out of localstack
   through its NodePort into `runs/calm-r1/`, runs vio-stress into `reports/`,
   and updates `campaign_manifest.json`.

## 6. Debugging, by layer

| symptom | layer | look at |
| --- | --- | --- |
| every pod Pending, workflow queued | KAI | `kubectl get podgroups -A` then `kubectl describe podgroup`, and the group's total requests |
| `FAILED_IMAGE_PULL` | node images | `kind load` onto `osmo-worker2`; `POD_TEMPLATE` pull policy |
| pod Running, script silent | OSMO sidecar | the barrier: some other task in the group is not up yet |
| task exits 42 over and over | your script | a dial-wait timed out; the peer it waits for is the problem |
| topics listed but no data | DDS | discovery-server client vs super-client, and named types (runbook, "The empty-graph family") |
| `504 upstream request timeout` | OSMO gateway | use `kubectl` for that step instead |
| COMPLETED, but nothing flew | your workflow | `runs/<key>/mission.json`, and the verdict |

The commands that answer most questions:

```bash
kubectl get pods -n default -l osmo.workflow_id=<id> -o wide
kubectl describe pod -n default <pod>              # events: scheduling, pulls, OOM
osmo workflow logs <id> --task <name>              # your script's output: the sidecar captures it
kubectl logs -n default <pod> --all-containers     # the sidecar's own lines: inputs, barrier, uploads
kubectl exec -n default <pod> -c <task name> -- bash
kubectl get svc -n default -l tevv.viewer=foxglove
```

## 7. Glossary

| term | meaning here |
| --- | --- |
| kind | Kubernetes in Docker: every node is a container on this host |
| node | a machine pods run on; `osmo-worker2` is the GPU one |
| pod | the unit that is scheduled; one OSMO task, plus its sidecar |
| sidecar | a helper container in the same pod (`osmo-ctrl`) |
| namespace | a name scope; task pods live in `default`, OSMO in `osmo` |
| label / selector | tags on objects, and the query that finds them |
| Service, NodePort | a stable way in to pods; NodePort opens 30000-32767 on every node |
| ownerReference | "delete me with that object" |
| request | the CPU, memory or GPU a container reserves; placement is decided on these |
| PodGroup, gang | pods scheduled together or not at all (KAI) |
| pool, platform | OSMO's compute set, and the rules for tasks in it |
| workflow, group, task, lead | the run; a gang inside it; one pod; the task whose exit ends its group |
| barrier | the sidecar holds every command until the group is up |
| exitActions | the map from exit code to COMPLETE, FAIL or RESCHEDULE |
| `{{host:x}}`, `{{output}}`, `{{input:0}}` | OSMO's tokens for a task's DNS name, its upload dir, and an upstream output |
| default-values, `--set` | the template's variables, and the override at submit |
| discovery server | the DDS rendezvous that replaces multicast between pods |
