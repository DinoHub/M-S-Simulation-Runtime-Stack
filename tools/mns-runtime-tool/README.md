# MnS Runtime Tool

The runtime stack uses the published MnS runtime-tool image as the ScenarioSpec
runtime entrypoint. It sits after authoring in the end-to-end product flow:
MnSPackaging builds `MnSLevelPack` (`*.mnslevelpack`) and `MnSAssetPack`
(`*.mnsassetpack`) bundles, ScenarioLab exports a ScenarioSpec folder plus
ScenarioBundle support data, then the runtime-tool image validates that
source-of-truth ScenarioSpec, generates an image-only Docker Compose stack, and
runs/stops/logs that stack without requiring Integration Platform service source
checkouts. The top-level wrappers call `run_image.sh`, which runs
`MNS_RUNTIME_TOOL_IMAGE` with path-preserving mounts and the host Docker socket.

Default image:

```bash
dhdevspace/auto_mns:mns-runtime-tool-latest
```

Typical user commands stay at the repo root:

```bash
./launch.sh --scenario-spec /path/to/scenario --stack-output generated/my_scenario
./logs.sh stack generated/my_scenario -f
./stop.sh --stack generated/my_scenario --remove-orphans
```

Direct helper use is mainly for debugging:

```bash
tools/mns-runtime-tool/run_image.sh version
tools/mns-runtime-tool/run_image.sh validate --scenario /path/to/scenario
tools/mns-runtime-tool/run_image.sh generate --scenario /path/to/scenario --out generated/my_scenario
```

The legacy vendored files under `stacks/` remain as reference/source snapshots,
but the supported runtime user path is image-based and pull-only. The runtime
stack forces the published image set; use the Integration Platform dev flow when
testing `local/*` runtime images.
