# Origin whole-product integration candidate

This is an image/source integration candidate, not an accepted pack release.
No existing pack lock has been changed or relabeled. The current Electric Dreams
and vehicle artifacts lack an exact variant for the new frozen renderer/base
contract; full browser-to-flight acceptance remains incomplete.

The catalog adds `origin_integration` and generates
`images/origin-integration.generated.env`. Its `origin-integration` image set
selects the coordinated runtime, ROS bridge, PX4 and ArduPilot images. Supply
`images/image-set.generated.yaml` to generation with `MNS_IMAGE_SET_FILE` and
select `origin-integration`; this external catalog is required because the
published generator's baked earlier candidate autopilot defaults predate the
final autopilot publications. Existing default channels are preserved.

Use `tools/images.sh sync` and `tools/images.sh verify` to reproduce and check
these selections. There is deliberately no new pack release lock until new
compatible immutable variants pass both host validators.

Frozen actual image contracts are in
`packs/runtime-host-compatibility.origin-integration.json` and
`packs/authoring-host-compatibility.origin-integration.json`.

[Dependency decisions, image/source versions, live numerical evidence, and remaining E2E gates](https://github.com/DinoHub/MnS-Integration-Platform/blob/integration/scenario-origin-product-20260910/docs/scenario-platform/scenario-origin-integration.md).

| Role | Exact published reference |
| --- | --- |
| runtime_host | `dhdevspace/auto_mns:tevv-runtime-host-v0.4.0-origin.1@sha256:604400d8d9f9ec0d0a5f8df6eb090acbc8dda3fd6f1d66a666ec46d9ddba5a21` |
| authoring | `dhdevspace/auto_mns:mns-authoring-v0.4.0-origin.1@sha256:77018f623f87ec985bda0a534e0a24a35a5760e0a5b37f57bf5cb1095b7a59b3` |
| stack_generator | `dhdevspace/auto_mns:mns-stack-generator-v0.4.0-origin.1@sha256:016373d2281fa2f2cd6a835ea3416546f1bf046d33191aa47fd86ed99c499e80` |
| product_shell | `dhdevspace/auto_mns:mns-product-shell-v0.4.0-origin.1@sha256:3fc42441b6442a9ae6f5427b43ceae5c5450360337490d0ee871417e21e89e33` |
| ros2_bridge | `dhdevspace/auto_mns:tevv-airsim-ros2-bridge-humble-v0.4.0-origin.1@sha256:24be9ec9c70412f09de251aae201f6d1a70d5d7569205a3268739982f729b4f6` |
| px4 | `dhdevspace/auto_mns:px4-airsim-px4-v0.4.0-origin.1@sha256:59aaa3a3e992317ed92de69ad7fc2b5221faaf97a79d6b426be07b2e1af605cc` |
| ardupilot | `dhdevspace/auto_mns:ardupilot-slim-v0.4.0-origin.1@sha256:aa77cd67258cb5f11c17d327cc78833eb959145e4c54778313117c2b0c645920` |
| dashboard_backend | `dhdevspace/auto_mns:tevv-web-dashboard-backend-v0.2.0-g1b74c70@sha256:d9d13b56343b99c025d53c2504a8cb46d04ed1bb824cffa3191d0353fa7931c2` |
| dashboard_frontend | `dhdevspace/auto_mns:tevv-web-dashboard-frontend-v0.2.0-g1b74c70@sha256:026eac57ea8c737d3abaebeb3412d5a0b37bc50f7e34c09dfaa14e9f5d51bc77` |
