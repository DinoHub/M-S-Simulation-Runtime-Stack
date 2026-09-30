# Decision log: moving campaign runs from OSMO to Argo Workflows

The decisions taken while building phase 2 of
[the OpenShift / Argo design](2026-09-30-campaigns-openshift-argo.md), in the order they
were taken. Each entry says what was decided and why, what else was considered, and what
showed it. Entries marked **Deviation** depart from the design doc; the doc has been
updated to match unless the entry says otherwise. Status is **Accepted** (keep),
**Prototype** (right for a dev cluster, replaced by a later phase) or **Open**.

Code: `argo/`, `osmo/campaign.py`, `osmo/backends.py` (PR #113). Measurements:
`argo/README.md`.

| # | Decision | Status |
| --- | --- | --- |
| 1 | Argo Workflows as the cluster engine | Accepted |
| 2 | Namespace install on the existing kind cluster, pinned by checksum | Prototype |
| 3 | One run is one Workflow; the fly step is one pod | Accepted |
| 4 | The recorder is the main container, the rest sidecars (not `containerSet`) | Accepted, **Deviation** |
| 5 | Reuse the OSMO task scripts, with default-preserving overrides | Accepted |
| 6 | Everything on localhost; keep the DDS discovery server | Accepted, **Deviation** |
| 7 | One user for the whole pod; the autopilot as root | Prototype |
| 8 | One `/dev/shm` for every container | Accepted |
| 9 | `HOME=/home/ros` in bridge-image containers | Accepted |
| 10 | Sidecars end 0 or 143; a failed flight is a completed pilot | Accepted |
| 11 | Evidence: a per-workflow volume, then an exit handler to `/workspace` | Prototype |
| 12 | The verdict survives as an output parameter; pods kept 30 minutes | Accepted |
| 13 | Side-loaded images by tag, checked against the catalog digests | Prototype |
| 14 | The backend lives in the runtime stack's executor, beside OSMO | Accepted, **Deviation** |
| 15 | The Argo backend answers in OSMO's vocabulary | Accepted |
| 16 | Template parameters are read from the generated stack | Accepted |
| 17 | One autopilot container for PX4 and ArduPilot | Accepted |
| 18 | A failed fly or evaluator still reaches a verdict | Accepted |
| 19 | The recorder's cap is at least 15 minutes when a route is flown | Accepted |
| 20 | Pinhole cameras stay on RPC; iceoryx2 is proven on the fisheye rig | Accepted, **Deviation** |
| 21 | Fast DDS keeps its default transports | Accepted |
| 22 | No Kueue and no live view on this backend yet | Prototype, **Deviation** |
| 23 | Registry schema unchanged | Accepted |
| 24 | Acceptance and benchmark runs under their own campaign ids | Prototype |
| 25 | Finish phase 2 before phase 3 | Accepted |
| 26 | Task files must survive OSMO's Jinja | Accepted |
| 27 | Benchmark the backends interleaved, not concurrently | Accepted |
| 28 | Estimator scores are not compared across sittings | Accepted |

---

## 1. Argo Workflows as the cluster engine

**Context.** The target is OpenShift, including classified clouds, and OSMO is not
something those platforms carry.

**Decision.** Argo Workflows. It runs on stock Kubernetes and OpenShift, puts several
containers in one pod, runs a DAG with artifacts and retries, and retries on an exit-code
expression. That last one is the OSMO exit-code contract: 42 and 137 are retried, 1 is a
verdict.

**Alternatives.**
- **Keep OSMO.** It is not available on the target platforms.
- **Tekton.** It is CI-shaped, and needs more glue for a long GPU pod with sidecars.
- **Plain Jobs with Kueue.** No DAG, no artifact passing and no retry policy; the
  executor would reimplement all three.

## 2. Namespace install on the existing kind cluster, pinned by checksum

**Decision.** Argo v4.1.4 from `namespace-install.yaml` into namespace `tevv-argo`, with
the manifest's sha256 checked in `argo/dev/install.sh`. Only the CRDs are cluster-wide.
The runner service account `tevv-runner` gets create/patch on `workflowtaskresults`
only.

**Why.** It reuses the GPU node, the registry and Loki without touching OSMO. Namespaced
RBAC is also the shape OpenShift asks for.

**Replaced by.** An operator-managed install, with an image mirror for the air gap
(phase 6).

## 3. One run is one Workflow; the fly step is one pod

**Decision.** Fly, then evaluate, then verdict, as on OSMO. Every real-time process is in
the fly pod: simulator, bridge, autopilot, estimator, relay, pilot, recorder and the
discovery server.

**Why.** A shared IPC and network namespace makes iceoryx2 zero-copy possible and puts
DDS on localhost. Every container also starts and stops together, which OSMO's gang
scheduling only approximated.

**Cost.** The pod needs one node with all of its requests free. It cannot be split.

## 4. The recorder is the main container, the rest sidecars (Deviation)

**Context.** The design said `containerSet`.

**Evidence.** A `containerSet` ends only when every container has exited. The simulator
never exits on its own, so the recorder could not end the run.

**Decision.** The recorder is the template's main container, and every other process is
a sidecar. Argo stops the sidecars once the main container exits. The design doc and its
diagram now say so.

## 5. Reuse the OSMO task scripts, with default-preserving overrides

**Decision.** `osmo/files/*` go into the pod unchanged in behaviour, as the ConfigMap
`tevv-run-files`. Where Argo needs something different, the script gained an environment
override whose default is the old behaviour:

- `STACK_DIR`;
- `ENABLE_VIO`, which also passes the camera names from the stack's `settings.json`;
- `CAMERA_INFO_TOPICS`, `USE_STEREO` and `MAX_CAMERAS`;
- the render flags from the compose file when a stack has no `host-launch-args.json`.

**Why.** There is one copy of the task logic for both backends. An OSMO run is unchanged
by any of it.

## 6. Everything on localhost; keep the DDS discovery server (Deviation)

**Decision.** Every `*_HOST` is `127.0.0.1`. The discovery server stays as a sidecar,
where the design had it dropped.

**Why.** Every script already dials it, and keeping it made the pod a drop-in for the
OSMO task group. Dropping it would mean new DDS configuration in every script for no
measured gain.

**Revisit.** When the scripts are next reworked.

## 7. One user for the whole pod; the autopilot as root

**Evidence.** With the bridge running as root next to the simulator's uid 1000,
iceoryx2 connected no subscriber: zero recipients on every stream.

**Decision.** The pod runs as uid/gid 1000, as the compose stack does (`HOST_UID:GID`).
The autopilot container runs as root, because the ArduPilot entrypoint drops privileges
with gosu and PX4's service runs as root.

**Replaced by.** Phase 3: arbitrary UID under `restricted-v2`, and no root at all.

## 8. One `/dev/shm` for every container

**Evidence.** The pilot saw `/mavros/state` advertised but received nothing, until the
pilot, the discovery server and the autopilot mounted the same memory-backed `/dev/shm`
as the rest. Fast DDS uses shared memory between processes it believes share a host, and
a container with its own `/dev/shm` breaks that.

**Decision.** An `emptyDir` with `medium: Memory` at `/dev/shm` in every fly container,
plus a shared `/tmp/iceoryx2`.

## 9. `HOME=/home/ros` in bridge-image containers

**Evidence.** With `HOME=/tmp`, the bridge's DDS clean-up removed `/tmp/.ros/log`, and
`ros2 launch` then failed every process it started after MAVROS.

**Decision.** Bridge-image containers keep the image's own home, as under compose.

## 10. Sidecars end 0 or 143; a failed flight is a completed pilot

**Evidence.** A flight that went well failed its workflow, because the QoS relay exited
1 on SIGTERM: rclpy raises `ExternalShutdownException`. Argo fails the pod for a stopped
sidecar with any exit code but 0 or 143 (137 past the grace period).

**Decision.**
- The relay exits 0 on shutdown.
- A pilot that exits 1 (the flight failed) is turned into 0. On OSMO a failed flight also
  ends as a completed task. The recorder's `mission.json` carries the flight's own
  result, which the executor judges.
- A pilot exit of 42 still fails the pod.

## 11. Evidence: a per-workflow volume, then an exit handler to `/workspace`

**Decision.** The steps share a `volumeClaimTemplate` at `/work`. An `onExit` step copies
the run's files into `export_dir` under the node's `/workspace`, which is the host
checkout. That covers `bag/`, `mission.json` and `eval/<step>/`, and it runs whatever the
run reached. It writes as uid 1000, removes the previous attempt's files first (never
merges), and drops group and other write permission.

**Why.** The layout is the one the OSMO executor downloads into, so the scorecard,
`reindex` and `registry sync` need no change. It also needs no artifact repository.

**Evidence.** Pod GC on success also deleted the volume, and with it one flight's
scores.

**Replaced by.** Phase 4: an Argo artifact repository (S3) and no host path.

## 12. The verdict survives as an output parameter; pods kept 30 minutes

**Decision.** `verdict.json` is the verdict step's output parameter, so it lives in the
workflow's status. `podGC` is `OnWorkflowSuccess` with `deleteDelayDuration: 30m`, so a
success's logs stay readable for a while. A failure's pods stay until the workflow is
deleted, and the executor copies their logs.

## 13. Side-loaded images by tag, checked against the catalog digests

**Context.** `kind load` keeps an image's tag but not its registry digest, so a
`repo:tag@sha256:` reference never matches it.

**Decision.** Submit by tag. The executor first checks each tag on the GPU node against
the image id of the digest `images/catalog.yaml` pins. This is OSMO's existing check,
reused. `argo/submit.sh` does the same with `LOCAL_IMAGES=1`.

**Replaced by.** A registry the cluster pulls from, which keeps digests.

## 14. The backend lives in the runtime stack's executor, beside OSMO (Deviation)

**Context.** The design put the executor interface in the platform's campaign runner
(MnS-Integration-Platform).

**Evidence.** That runner runs inside the product-shell container, which has no kubectl,
Argo client, kubeconfig or OSMO session. The OSMO executor was written outside it for the
same reason.

**Decision.** `osmo/campaign.py run --backend osmo|argo`. The OSMO calls moved behind a
backend object; `osmo/backends.py` adds Argo. The backend's operations are:

- `prepare`
- `submit_run`
- `query`
- `times`
- `fetch`
- `logs`

Materialise, generate, the image check, verdict, registry, manifest and the evaluator
stay shared.

**Consequences.**
- `execution.backend` in the CampaignSpec waits until the interface moves into the
  platform's runner. Until then the choice is the `--backend` flag or
  `TEVV_CAMPAIGN_BACKEND`.
- The manifest's `target` and each `run.json` record which backend ran.

**Revisit.** When the product shell carries a cluster client.

## 15. The Argo backend answers in OSMO's vocabulary

**Decision.** `query()` translates an Argo Workflow into OSMO's workflow status
(`COMPLETED`, `FAILED`, `FAILED_SERVER_ERROR`, `FAILED_IMAGE_PULL`, `CANCELLED`) and one
task per fly-pod container and evaluate step. The rules:

- Only the last retry of the fly step is read.
- A sidecar Argo stopped (exit 137 or 143) counts as completed.
- A recorder killed by a signal counts as failed.

**Why.** `judge_flight`, `registry_outcome`, the registry's lifecycle and the scorecard
then read both backends unchanged. `osmo/test_backends.py` covers the mapping.

## 16. Template parameters are read from the generated stack

**Decision.** The backend reads what the pod needs from the generated stack:

- whether the fisheye images go over iceoryx2 (`ENABLE_VIO` in `.env`);
- whether an estimator runs (`config/vio/estimator_config.yaml`);
- the estimator's camera topics, from `kalibr_imucam_chain.yaml`;
- which of those need the QoS relay (`<topic>_reliable`);
- the `camera_info` topics to record;
- stereo or mono.

**Why.** The generator already decided all of it for this ScenarioSpec, and restating it
in the CampaignSpec is how the two drift.

**Evidence.** The chain file is OpenCV YAML (a `%YAML:1.0` header), which a YAML parser
refuses; the first acceptance run crashed on it. The topics are read line by line
instead.

## 17. One autopilot container for PX4 and ArduPilot

**Decision.** One `autopilot` container. Its image is the `autopilot_image` parameter,
chosen by the submitter from the campaign's `mission.autopilot`, and it runs
`autopilot_<autopilot>.sh`. The environment is the union of what the OSMO workflow sets
for each; each image reads its own.

**Evidence.** PX4 connects to the simulator on localhost TCP 4560 and serves MAVROS on
its router's 14555. The pilot reached OFFBOARD, armed and flew the full route in the pod.

## 18. A failed fly or evaluator still reaches a verdict

**Decision.** `continueOn: {failed: true}` on fly, vio-eval, spawn-eval and validate, so
the verdict judges whatever was recorded and says what was missing. This is OSMO's
behaviour.

**Evidence.** Before this, the relay's exit code (entry 10) skipped every evaluator, and
the run had no verdict at all.

## 19. The recorder's cap is at least 15 minutes when a route is flown

**Context.** `RECORD_SEC` only caps a pilot that never announces the end of the flight;
the bag normally closes a few seconds after it does. The OSMO executor passes
`mission.timeout_s` there. For vio-reference that is 10 s, the runner's tail after a
flight, which would close the bag before takeoff.

**Decision.** On Argo the cap is `max(timeout_s, 900)` when a route is declared.

**Not changed.** The OSMO path is left as it was.

## 20. Pinhole cameras stay on RPC; iceoryx2 is proven on the fisheye rig (Deviation)

**Context.** Phase 2's acceptance named "vio-reference on iceoryx2".

**Evidence.** The bridge's `enable_vio` is the iceoryx2 *fisheye* path, and
vio-reference's cameras are pinhole.

**Decision.**
- iceoryx2 in one pod is shown on the fisheye gap stack: one recipient per stream, and
  10.6–29 Hz against 12–29 Hz on compose.
- vio-reference shows the backend, PX4 and the registry, with its cameras over RPC.

## 21. Fast DDS keeps its default transports

**Context.** Compose pins `FASTDDS_BUILTIN_TRANSPORTS=UDPv4`. Stereo OpenVINS in the pod
looked to diverge far more often than on OSMO.

**Tried.** UDPv4 in every ROS container. The estimator received 27.9 Hz of images, and
the flight scored 9.85 m, no better.

**Decision.** Reverted to the default transports.

**Later evidence.** The benchmark (entry 27) showed the divergence on OSMO too, so there
was nothing here for the transport to fix.

## 22. No Kueue and no live view on this backend yet (Deviation)

**Decision.**
- Kueue is not installed. There is one GPU, and the executor submits one run at a time
  per campaign.
- `--viz` is ignored on Argo, with a note. The Foxglove view is OSMO-only until an
  OpenShift Route with websockets is tested.

**Replaced by.** Kueue quotas when there is more than one GPU node or more than one
team; Foxglove through a Route.

## 23. Registry schema unchanged

**Decision.** No new column. The workflow reference identifies the run, and the
manifest and `run.json` say which backend ran it.

**Evidence.** The schema's `failure_reason` check rejects new values, so a submit that
failed after generation is recorded as `infra`, the platform-unusable reason.

**Also changed.** The executor used to catch only `RuntimeError` at submission; any
exception now closes the registry attempt instead of leaving it `pending`.

## 24. Acceptance and benchmark runs under their own campaign ids

**Context.** `generated/campaigns/vio-reference` is root-owned: a compose run went
through the product shell as root, and the executor cannot write there. Reusing a
campaign's run directory would also overwrite that campaign's own evidence.

**Decision.** The runs used copies of the CampaignSpec with new ids, pointing back at
the real ScenarioSpec and route. The copies sit under the git-ignored `generated/`:

- vio-reference-argo and vio-osmo-xfs-argo (acceptance);
- vio-bench-osmo and vio-bench-argo (benchmark).

## 25. Finish phase 2 before phase 3

**Decision.** Phase 2 is done before phase 3 (image hardening), the user's choice. Phase
3's acceptance is "the same run passes under `restricted-v2`", and that needs phase 2's
run to exist first.

## 26. Task files must survive OSMO's Jinja

**Evidence.** The first benchmark submit to OSMO failed with "Jinja substitution failure:
Missing end of comment tag". OSMO renders every `files:` entry as a Jinja template, and
the `sim.sh` change from entry 5 counted an array with `${#COMPOSE_ARGS[@]}`, where `{#`
opens a Jinja comment. No Argo run could have shown this.

**Decision.**
- Count the array without the length expansion.
- `osmo/test_backends.py` fails if any shell or Python file under `osmo/files/` contains
  `{#`, `{{` or `{%`.
- The shared scripts are exercised on both backends before a change to them is called
  default-preserving.

## 27. Benchmark the backends interleaved, not concurrently

**Context.** The user asked for an Argo flow alongside OSMO to compare performance.

**Decision.** Same CampaignSpec, same images (digest-checked on the node), same cluster,
three runs each under separate campaign ids. The runs were interleaved (OSMO, Argo, Argo,
OSMO, OSMO, Argo, then the OSMO run whose first submit failed), with host GPU and CPU
sampled every 5 s.

**Why not concurrently.** The GPU node advertises one `nvidia.com/gpu`, so a second
simulator would only queue, and two simulators on one GPU would measure each other.
Interleaving spreads host-load drift across both backends.

**Result.**
- Argo takes 170 s per run against OSMO's 198 s, at the same GPU load. The gain is in
  scheduling and the evaluate hand-off.
- The Argo pod's cameras run about 8 % slower.
- Both backends' estimators diverged in every run (`argo/README.md`).

## 28. Estimator scores are not compared across sittings

**Evidence.** The same CampaignSpec, with the same image digests and a byte-identical
generated stack, scored 0.45–0.9 m on OSMO on 25 Sep and 2.8–8.6 m on OSMO and Argo alike
on 30 Sep. That is the same host boot and the same GPU driver. Comparing Argo's 30 Sep
runs with OSMO's 25 Sep runs had made the Argo pod look like the cause.

**Decision.** A backend comparison is only valid inside one interleaved sitting (entry
27). The drift since 25 Sep is its own investigation, not a migration blocker.
