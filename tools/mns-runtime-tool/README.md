# MnS Runtime Tool

This vendored tool is the self-contained ScenarioSpec runtime entrypoint for
M-S-Simulation-Runtime-Stack. It validates a ScenarioSpec, generates an image-only
Docker Compose stack, and runs or stops that generated stack without requiring
Integration Platform service source checkouts.

The generated stack may contain a `source/` folder. That folder is a ScenarioSpec
snapshot for provenance and metrics finalization only; it is not used as a Docker
build context.

## Commands

```bash
tools/mns-runtime-tool/stacks/scripts/mns_runtime_tool.sh validate --scenario /path/to/scenario
tools/mns-runtime-tool/stacks/scripts/mns_runtime_tool.sh generate --scenario /path/to/scenario --out generated/my_scenario
tools/mns-runtime-tool/stacks/scripts/mns_runtime_tool.sh run --stack generated/my_scenario -d
tools/mns-runtime-tool/stacks/scripts/mns_runtime_tool.sh stop --stack generated/my_scenario --remove-orphans
```
