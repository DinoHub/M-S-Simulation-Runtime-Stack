# Origin whole-product integration candidate

This candidate includes freshly cooked Electric Dreams 1.0.4 and vehicle/object
pack 1.0.2 variants validated against both frozen host contracts. Observed live on
2026-09-10 from this candidate: browser pack selection and staging, packaged
ScenarioLab launch, native origin/vehicle/object edit with exact export -> reload
-> re-export preservation, browser generation, the generic UE 5.8.2 runtime applying
the explicit origin before AirSim start (`MNS_SCENARIO_ORIGIN_READY`,
`MNS_VEHICLE_SPAWN` at the expected world pose, authored cube at the expected world
pose), ROS 2 `map->odom`, and one bounded takeoff/forward/back/descent flight each
for PX4 (`px4-airsim-px4-v0.4.0-origin.3`) and ArduPilot
(`ardupilot-slim-v0.4.0-origin.1`) driven through the dashboard teleop path, with
dashboard telemetry and metrics JSONL observed. Preflight is still not E2E
acceptance: see the platform document `scenario-origin-integration.md` for the
numerical comparisons and the open findings (bridge `map->odom` settle offset,
ArduPilot C2 stream/disarm gaps, UE PSO hitch at takeoff, zero/multiple PlayerStart
cooked packs not yet run live).

The catalog adds `origin_integration` and generates
`images/origin-integration.generated.env`. Its `origin-integration` image set
selects the coordinated runtime, ROS bridge, PX4 and ArduPilot images. Supply
`images/image-set.generated.yaml` to generation with `MNS_IMAGE_SET_FILE` and
select `origin-integration`; this external catalog is required because the
published generator's baked earlier candidate autopilot defaults predate the
final autopilot publications. Existing default channels are preserved.

Use `tools/images.sh sync` and `tools/images.sh verify` to reproduce and check
these selections. `packs/origin-integration.lock.json` records the locally assembled candidate.
Its archives have not been uploaded to a release; install those exact archives
into the selected store before launching the candidate.

Frozen actual image contracts are in
`packs/runtime-host-compatibility.origin-integration.json` and
`packs/authoring-host-compatibility.origin-integration.json`.

[Dependency decisions, image/source versions, live numerical evidence, and remaining E2E gates](https://github.com/DinoHub/MnS-Integration-Platform/blob/integration/scenario-origin-product-20260910/docs/scenario-platform/scenario-origin-integration.md).

| Role | Exact published reference |
| --- | --- |
| runtime_host | `dhdevspace/auto_mns:tevv-runtime-host-v0.4.0-origin.1@sha256:604400d8d9f9ec0d0a5f8df6eb090acbc8dda3fd6f1d66a666ec46d9ddba5a21` |
| authoring | `dhdevspace/auto_mns:mns-authoring-v0.4.0-origin.5@sha256:3fe4f5b80e510b8c2f88f9a8e4ead6dbcb7c048889d2681e27c504665cb559a0` |
| stack_generator | `dhdevspace/auto_mns:mns-stack-generator-v0.4.0-origin.1@sha256:016373d2281fa2f2cd6a835ea3416546f1bf046d33191aa47fd86ed99c499e80` |
| product_shell | `dhdevspace/auto_mns:mns-product-shell-v0.4.0-origin.1@sha256:3fc42441b6442a9ae6f5427b43ceae5c5450360337490d0ee871417e21e89e33` |
| ros2_bridge | `dhdevspace/auto_mns:tevv-airsim-ros2-bridge-humble-v0.4.0-origin.1@sha256:24be9ec9c70412f09de251aae201f6d1a70d5d7569205a3268739982f729b4f6` |
| px4 | `dhdevspace/auto_mns:px4-airsim-px4-v0.4.0-origin.3@sha256:70d3fdf1f44f973f4ad36f11eb1560cada2b1aefccadcf5ed1b0dcd198be23bc` |
| ardupilot | `dhdevspace/auto_mns:ardupilot-slim-v0.4.0-origin.1@sha256:aa77cd67258cb5f11c17d327cc78833eb959145e4c54778313117c2b0c645920` |
| dashboard_backend | `dhdevspace/auto_mns:tevv-web-dashboard-backend-v0.2.0-gfeb7600@sha256:094533c3195e83a0b7d6f77f5cab81935980933f174e7ba8395539806c8c3f1e` |
| dashboard_frontend | `dhdevspace/auto_mns:tevv-web-dashboard-frontend-v0.2.0-g39de41e@sha256:0f82cfd54fe546999ccc7ae14239f7b8a326e648cba7a201bd2041011c412e36` |

## Local candidate run

From an isolated checkout of this product integration branch, install the two
locally built archives with the pinned product shell, then run:

```bash
python3 tools/check_ue_candidate.py \
  --lock packs/origin-integration.lock.json \
  --pack-store "$PWD/.mns/pack-store" --workspace "$PWD" \
  --authoring-data-root "$PWD/.mns/authoring-data" \
  --compose-project mns-origin-e2e --start-dashboard \
  --dashboard-container mns-origin-e2e-airsim-dashboard-api
```

Export `COMPOSE_PROFILES=db` to include the isolated telemetry database. The
browser is at `http://localhost:3001`. The candidate owns distinct dashboard,
editor and ROS2 tool container names; browser ports must be available. Original
containers can be stopped and retained. Archive checksum, content digest, host
capability and exact required images are recorded in the lock.
