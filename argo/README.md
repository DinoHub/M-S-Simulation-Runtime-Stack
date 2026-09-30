# TEVV runs on Argo Workflows (prototype)

Phase 2 of [the OpenShift / Argo design](../docs/design/2026-09-30-campaigns-openshift-argo.md):
one run of a generated stack as one Argo Workflow, on a dev cluster, and a campaign as one
workflow per run through the campaign executor's `--backend argo`.

```
fly (one pod) -> vio-eval | spawn-eval | validate -> verdict      (on exit: export)
```

The fly pod's main container is the recorder, and its exit ends the pod. The discovery
server, sim, bridge, autopilot, estimator, relay and pilot are sidecars, which Argo stops
when the recorder exits. All of them share the pod's IPC namespace, so the bridge can take
the simulator's images over iceoryx2 shared memory (`enable_vio=true`), which separate OSMO
tasks cannot.

The per-task scripts are `osmo/files/*` (plus `files/qos_relay.py`), delivered as the
ConfigMap `tevv-run-files`. Every `*_HOST` is `127.0.0.1`.

## A campaign

```bash
argo/dev/install.sh                      # once: Argo v4.1.4 in namespace tevv-argo, runner account
osmo/campaign.py run vio-reference --backend argo --only calm-r1
```

`osmo/campaign.py` is the same executor as for OSMO. It materialises and generates each run
through the product shell and checks the images against the catalog. It then submits one
workflow per run, keeps the run registry live while the runs fly, and writes the manifest.
Only the cluster calls differ. They are in `osmo/backends.py`: submit, status, timings,
evidence and logs.

- **Parameters come from the generated stack.** The backend reads whether the fisheye images
  go over iceoryx2 (`ENABLE_VIO` in its `.env`), whether an estimator runs, which of its
  image topics need the QoS relay (`<topic>_reliable`), which `camera_info` topics to record,
  and stereo or not. None of it is restated in the CampaignSpec.
- **Evidence:** the workflow's exit handler copies it into
  `generated/campaigns/<id>/runs/<key>/` through the node's `/workspace`. The layout is the
  one the OSMO executor downloads into: `bag/`, `mission.json`, `eval/<step>/`. The
  scorecard, `reindex` and `registry sync` then read either backend's runs alike.
- **Status:** OSMO's words. Every fly-pod container is a task, with a sidecar that Argo
  stopped counted as completed, and so is every evaluate step. The registry's lifecycle and
  failure reasons are unchanged.
- **Not on this backend yet:** the live Foxglove view (`--viz` is ignored, with a note).
- **Environment:** `TEVV_CAMPAIGN_BACKEND=argo` makes `argo` the default backend.
  `TEVV_ARGO_NAMESPACE` sets the namespace (default `tevv-argo`) and `ARGO` the CLI path.

## One run by hand

```bash
LOCAL_IMAGES=1 argo/submit.sh <generated stack dir> fly=true estimator=true WAIT=1
```

- **Parameters:** `argo/submit.sh` takes any `name=value` of the WorkflowTemplate's parameters.
  See the top of `workflows/tevv-campaign-run.yaml`.
- **Images:** default to the v1 image set. Override any with `RUNTIME_HOST_IMAGE`,
  `BRIDGE_IMAGE`, `ARDUPILOT_IMAGE`, `PX4_IMAGE`, `VIO_ESTIMATOR_IMAGE` or
  `SIM_REAL_EVAL_IMAGE`. `autopilot=px4` flies the PX4 image.
- **`LOCAL_IMAGES=1`:** for images side-loaded with `kind load`, which keep their tag but not
  their registry digest.

A fisheye VIO flight with OpenVINS live, on the gap-test XFS stack and route from PR #107
(`tools/gap-tests/gap.sh stack` generates the stack):

```bash
LOCAL_IMAGES=1 RUNTIME_HOST_IMAGE=dhdevspace/auto_mns:tevv-runtime-host-v1.0.0-bloomfix.16 \
BRIDGE_IMAGE=tevv-airsim-ros2-bridge:v1.0.0-shmfix.1 \
argo/submit.sh <gap-fisheye-xfs stack> fly=true estimator=true vio_use_stereo=false \
  relay_topics=/fisheye_front/image_raw,/fisheye_back/image_raw \
  camera_info_topics=/fisheye_front/camera_info,/fisheye_back/camera_info \
  "route_b64=$(cat scenarios/gap-fisheye-xfs/routes/xfs_yard_box.b64)" \
  'gates_json={"ate_trans_m.rmse": {"max": 5.0}}'
```

The verdict is the `verdict` step's output parameter, so it outlives the run's pods
(kept 30 minutes after a success) and its scratch volume:

```bash
kubectl -n tevv-argo get wf <name> -o json \
  | jq -r '.status.nodes[] | select(.displayName=="verdict") | .outputs.parameters[0].value'
```

## Measured (kind, one RTX 5080 node, 30 Sep 2026)

Parked, gap-fisheye-xfs, bloomfix.16 host, shmfix bridge:

- **Transport:** iceoryx2 shared memory between the sim and bridge containers, with one
  recipient on each of the four streams.
- **Fisheye rates:** 10.6–29 Hz, against 12–29 Hz on compose.
- **IMU:** 200 Hz.
- **Recording:** passes validation.

Flying, same stack, ArduPilot, OpenVINS live (monocular, through the QoS relay), route
`xfs_yard_box_v1` (13 waypoints, 49 m path):

- **Run time:** 3 min 10 s from submit to verdict. The fly pod took 2 min 43 s; the
  evaluation and verdict steps took 20 s.
- **Recording:** 117,292 messages. They include 15,203 estimates, 3,613 frames of
  `camera_info` per fisheye and 26,740 IMU samples.
- **Estimator:** ATE RMSE 0.63 m over the 66 s airborne window (0.72 m over the whole
  recording). The gate at 5 m passes. This is one run. On compose, replays of this stack
  scored 0.90–1.24 m, and live runs sometimes diverged.

Campaigns through `osmo/campaign.py --backend argo`, PX4, pinhole stereo OpenVINS (cameras
over RPC: the iceoryx2 path is the fisheye rig's), XFS yard, v1 image set:

| Campaign, run | Workflow | Registry | ATE RMSE |
| --- | --- | --- | --- |
| vio-reference (#106 spawn and route), calm-r1 | `tevv-campaign-run-x2947` | passed (no ATE gate declared) | 4.55 m |
| vio-reference, calm-r2 | `tevv-campaign-run-6k8b2` | passed (no ATE gate declared) | 9.22 m |
| vio-osmo-xfs, calm-r1 | `tevv-campaign-run-m9745` | failed, gate_failed (1.0 m gate) | 3.33 m |
| vio-osmo-xfs, calm-r1, Fast DDS on UDP only | `tevv-campaign-run-b8nr4` | failed, gate_failed | 9.85 m |

- **The flow works end to end.** Each run was materialised and generated through the
  product shell and flown in one pod: MAVROS connected, OFFBOARD, the full 51 m route,
  landed. The evidence lands in `runs/<key>/`, and the registry gets the run's status,
  Argo's timings, the gate rows, six headline metrics and the artifact paths.
- **The estimator does not do as well as on OSMO.** The images match by digest, and the
  stack config and the route are identical to `vio-osmo-xfs` on OSMO (25 Sep). There, 10 of
  12 scored runs came in at 0.45–1.5 m. All four stereo runs here diverged 30–40 s into the flight, at
  the point where the other 2 OSMO runs diverged (6.6 m, 15 m). Same failure mode,
  much more often. Not the cause, as measured:
  - camera and IMU rates in the bag: cameras 23.3 Hz here against 23.6 Hz, IMU 189 Hz
    against 192 Hz;
  - estimator CPU: 4.4 ms mean and 15 ms max per update, against a 43 ms frame period;
  - Fast DDS shared memory against UDP only, over one run.
  Still open: timing between the simulator, bridge and estimator when they share one node.
  The mono fisheye flight above, 0.63 m, did not diverge.

Three things had to match the compose stack before the parked rates did:

- **One user across the pod.** iceoryx2 connected nothing while the bridge ran as root next to
  the uid-1000 simulator.
- **The simulator's camera names for the bridge.**
- **The stack's render settings on the simulator's command line** (`r.Fisheye.SyncCaptureFPS`).
  Without them the fisheye rig captured at 3–5 Hz.

Two more things had to match before the flight did:

- **`/dev/shm` shared by every container, not only the sim and bridge.** Fast DDS uses
  shared memory between processes on one host. A pilot without the mount saw
  `/mavros/state` advertised but received none of its messages.
- **Sidecars that exit 0 or 143 on SIGTERM.** Argo fails the pod for any other exit code
  from a sidecar it stops. The relay's rclpy shutdown exception exited 1.

## Prototype limits (on the way to the design)

| Here | Design |
| --- | --- |
| Checkout from the node's `/workspace` (hostPath) | Pack store on a `ReadOnlyMany` PVC; stack config from a `generate` step |
| Steps share a per-workflow volume (`/work`) | Argo artifact repository (S3) |
| Stack generated on the host before submitting | `generate` step in the workflow |
| The live view is OSMO's only | Foxglove through a Route |
| Runs as the images' own users | Arbitrary UID under `restricted-v2` |
