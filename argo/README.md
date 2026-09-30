# TEVV runs on Argo Workflows (prototype)

Phase 2 of [the OpenShift / Argo design](../docs/design/2026-09-30-campaigns-openshift-argo.md):
one run of a generated stack as one Argo Workflow, on a dev cluster.

```
fly (one pod) -> vio-eval | spawn-eval | validate -> verdict
```

The fly pod's main container is the recorder, and its exit ends the pod. The discovery
server, sim, bridge, autopilot, estimator, relay and pilot are sidecars, which Argo stops
when the recorder exits. All of them share the pod's IPC namespace, so the bridge can take
the simulator's images over iceoryx2 shared memory (`enable_vio=true`), which separate OSMO
tasks cannot.

The per-task scripts are `osmo/files/*` (plus `files/qos_relay.py`), delivered as the
ConfigMap `tevv-run-files`. Every `*_HOST` is `127.0.0.1`.

## Use

```bash
argo/dev/install.sh                      # once: Argo v4.1.4 in namespace tevv-argo, runner account
LOCAL_IMAGES=1 argo/submit.sh <generated stack dir> fly=true estimator=true WAIT=1
```

- **Parameters:** `argo/submit.sh` takes any `name=value` of the WorkflowTemplate's parameters.
  See the top of `workflows/tevv-campaign-run.yaml`.
- **Images:** default to the v1 image set. Override any with `RUNTIME_HOST_IMAGE`,
  `BRIDGE_IMAGE`, `ARDUPILOT_IMAGE`, `VIO_ESTIMATOR_IMAGE` or `SIM_REAL_EVAL_IMAGE`.
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
| ArduPilot only | ArduPilot and PX4 |
| Runs as the images' own users | Arbitrary UID under `restricted-v2` |
