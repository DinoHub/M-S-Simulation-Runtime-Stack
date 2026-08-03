# MnS Stack Generator Image Helper

The runtime stack uses the published MnS stack-generator image as the ScenarioSpec
generation entrypoint. It sits after authoring in the end-to-end product flow:
MnSPackaging builds `MnSLevelPack` (`*.mnslevelpack`) and `MnSAssetPack`
(`*.mnsassetpack`) bundles, ScenarioLab exports a ScenarioSpec folder plus
ScenarioBundle support data, then the stack-generator image validates that
source-of-truth ScenarioSpec, generates an image-only Docker Compose stack, and
runs/stops/logs that stack without requiring Integration Platform service source
checkouts. The top-level wrappers call `run_image.sh`, which runs
`MNS_STACK_GENERATOR_IMAGE` with path-preserving mounts and the host Docker socket.

Default image:

```bash
dhdevspace/auto_mns:mns-stack-generator-latest
```

Typical user commands stay at the repo root:

```bash
./launch.sh --scenario-spec /path/to/scenario --stack-output generated/my_scenario
./logs.sh stack generated/my_scenario -f
./stop.sh --stack generated/my_scenario --remove-orphans
```

Direct helper use is mainly for debugging:

```bash
tools/stack-generator-image/run_image.sh version
tools/stack-generator-image/run_image.sh validate --scenario /path/to/scenario
tools/stack-generator-image/run_image.sh generate --scenario /path/to/scenario --out generated/my_scenario
```

This directory intentionally contains only the image wrapper. The stack generator source and ScenarioSpec contract stay in their own repos/images; the product repo only mounts user scenarios and generated stacks. The runtime stack forces the published image set; use the Integration Platform dev flow when testing `local/*` images.
