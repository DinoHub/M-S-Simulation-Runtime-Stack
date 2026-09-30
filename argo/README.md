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

## Prototype limits (on the way to the design)

| Here | Design |
| --- | --- |
| Checkout from the node's `/workspace` (hostPath) | Pack store on a `ReadOnlyMany` PVC; stack config from a `generate` step |
| Steps share a per-workflow volume (`/work`) | Argo artifact repository (S3) |
| Stack generated on the host before submitting | `generate` step in the workflow |
| ArduPilot only | ArduPilot and PX4 |
| Runs as the images' own users | Arbitrary UID under `restricted-v2` |
