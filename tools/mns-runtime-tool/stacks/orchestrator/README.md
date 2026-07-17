# Stack Orchestrator MVP

The orchestrator turns a human-editable Scenario Definition folder (`ScenarioSpec.yaml`) into a self-contained runtime stack.

```text
Scenario Definition (`ScenarioSpec.yaml`) + optional ScenarioBundle support
        |
        v
ResolvedScenario
        |
        v
generated stack folder
        |
        v
stacks/scripts/run_stack.sh
```

This keeps generation inspectable. A developer can validate the scenario,
generate `settings.json` and `docker-compose.yml`, inspect the generated stack,
and only then run it.

## Customer Input

The supported input is a Scenario Definition file or folder. For larger
scenarios, the folder form is preferred:

```text
MyScenario/
  ScenarioSpec.yaml               # root manifest; may include section files
  Environment.yaml
  Runtime.yaml
  ObjectClutter.yaml
  SensorProfiles.yaml
  Objects.yaml
  Extensions.yaml
  Vehicles/
    DroneA.yaml
    DroneB.yaml
  ScenarioBundle/                 # optional generated/supporting local bundle
    catalogs/
      environments.yaml
      asset_packs.yaml
    artifacts/
      level_packs/
      asset_packs/
```

`ScenarioSpec.yaml` can also remain a single self-contained file for compact
tests and CI fixtures.

For local scenarios, files under `ScenarioBundle/catalogs/` should refer to
local level/asset artifacts using paths relative to `ScenarioBundle/`:

```text
artifacts/level_packs/<level_id>/...
artifacts/asset_packs/<pack_id>/...
```

`ScenarioSpec.yaml` and its included section files are the human-editable source of truth. They capture user intent: environment IDs, runtime profile,
vehicles, sensors, authored object placements, and extension blocks for fields that are
still evolving. Engine-specific map, mesh, class, image, executable, and pak
bindings are resolved through built-in catalogs or bundle-local catalog overlays.

For field-level configuration, see:

```text
docs/simrunner/scenariospec-reference.md
```

## Generator

Primary entrypoint:

```bash
stacks/orchestrator/generate_stack.py <command> <scenario-input> [options]
```

`<scenario-input>` must be a `ScenarioSpec.yaml` file or a folder containing one.

Autopilot-specific behavior lives in profile modules:

```text
stacks/orchestrator/stackgen/autopilots/
  base.py
  registry.py
  ardupilot.py
  px4.py
```

Profiles own vehicle naming, hostname/index conventions, AirSim vehicle blocks,
managed SITL Compose services, bridge suffixes, MAVROS config/URLs, and support
config folders. The ScenarioSpec resolver and common generators call the active
profile instead of hard-coding autopilot branches.

Generated files are produced by artifact generators:

```text
stacks/orchestrator/stackgen/generators/
  artifact_pipeline.py
  stack_artifacts.py
```

Commands:

```text
validate         Validate the Scenario Definition.
explain          Print resolved vehicles, endpoints, and generated choices.
render-scenario Generate only ScenarioRuntime/ScenarioPlugin artifacts.
generate         Generate the full stack under stacks/generated/ or --out.
ports            Generate, then print the customer connection contract.
```

Options:

```text
--profile docker|editor  docker starts AirSim in Compose; editor leaves AirSim
                         for Unreal Editor on the host.
--out DIR                Override the generated stack output directory.
```

Validate and inspect the bundled demos:

```bash
stacks/orchestrator/generate_stack.py validate stacks/orchestrator/examples/ScenarioSpec
stacks/orchestrator/generate_stack.py validate stacks/orchestrator/examples/ScenarioSpecFolder
stacks/orchestrator/generate_stack.py explain stacks/orchestrator/examples/ScenarioSpecFolder
```

Generate a self-contained stack folder:

```bash
stacks/orchestrator/generate_stack.py generate stacks/orchestrator/examples/ScenarioSpec
```

Inspect the generated port and hostname contract:

```bash
stacks/orchestrator/generate_stack.py ports stacks/orchestrator/examples/ScenarioSpec
```

Generate only Unreal-facing scenario artifacts:

```bash
stacks/orchestrator/generate_stack.py render-scenario stacks/orchestrator/examples/ScenarioSpec
```

For Unreal Editor/AirSim on the host:

```bash
stacks/orchestrator/generate_stack.py generate \
  stacks/orchestrator/examples/ScenarioSpec \
  --profile editor
```

The editor profile writes `editor-launch-args.txt` and leaves AirSim out of the
generated Compose stack.

## Runner

The runner operates on an already generated stack folder. It does not parse or
resolve scenario input.

Start:

```bash
stacks/scripts/run_stack.sh stacks/generated/<stack-name> -d
```

Logs:

```bash
stacks/scripts/log_stack.sh stacks/generated/<stack-name> -f
```

Stop:

```bash
stacks/scripts/stop_stack.sh stacks/generated/<stack-name>
```

External MAVROS validation:

```bash
stacks/tests/test_external_mavros.sh stacks/generated/<stack-name> 1
```

## Typical Development Flow

```bash
# 1. Validate customer YAML.
stacks/orchestrator/generate_stack.py validate stacks/orchestrator/examples/ScenarioSpec

# 2. Generate a normal stack folder.
stacks/orchestrator/generate_stack.py generate stacks/orchestrator/examples/ScenarioSpec

# 3. Inspect generated settings, compose, manifests, and port contracts.
stacks/orchestrator/generate_stack.py ports stacks/orchestrator/examples/ScenarioSpec

# 4. Run the generated stack explicitly.
stacks/scripts/run_stack.sh stacks/generated/ardupilot-xfs-docker-dev-scenariospec-multi -d

# 5. Inspect logs and stop.
stacks/scripts/log_stack.sh stacks/generated/ardupilot-xfs-docker-dev-scenariospec-multi -f
stacks/scripts/stop_stack.sh stacks/generated/ardupilot-xfs-docker-dev-scenariospec-multi
```

## Generated Stack Layout

```text
stacks/generated/<stack-name>/
  docker-compose.yml
  .env
  generated-manifest.json
  execution-context.json
  scenario-artifacts-manifest.json
  scenario-docker-args.txt
  editor-launch-args.txt        # editor profile only
  config/
    unreal-airsim/settings.json
    scenario/scenario_runtime.json
    scenario/object_clutter.yaml
    scenario/object_clutter.json
    scenario-plugin/scenario_plugin.json
    asset-packs/                # staged runtime asset bundles when used
    ardupilot/...
    qgroundcontrol/...
  source/
    ScenarioSpec.yaml
    ScenarioBundle/             # only files needed to reproduce generation
```

The generated `.env` uses `CONFIG_ROOT=./config`, so the generated folder can be
inspected and run like the existing hand-authored stack folders.

## Network Profiles

`runtime.autopilot.endpoint` selects the autopilot network contract:

```text
docker       AirSim connects to per-drone Docker hostnames.
host         AirSim uses host-visible ports and `host.docker.internal`.
hostname/IP  Advanced direct routing to an external FCU host.
```

`runtime.autopilot.managed` controls ownership. When true, stackgen emits
managed ArduPilot or PX4 services. When false, stackgen only renders the AirSim,
settings, bridge, and manifest contract; the user must provide the autopilot
processes at the resolved hosts and ports. In that mode, MnS assigns the ports in
ScenarioSpec and the external autopilot must expose, bind, or connect those exact
ports. Port changes require regenerating the stack so `settings.json`, bridge
commands, and `generated-manifest.json` stay aligned.

`runtime.features` selects optional generated services:

```text
qgroundcontrol  Include generated QGroundControl.
ros2_bridge     Include one generated AirSim ROS2 bridge per vehicle.
mavros          Start MAVROS inside each generated ROS2 bridge container.
```

Use `generate_stack.py ports <ScenarioSpec> --out <stack-dir>` or inspect
`generated-manifest.json` after generation for the exact AirSim RPC endpoint,
per-vehicle autopilot host, data/control ports, MAVROS FCU URLs, ROS domain ids,
and generated container names. External MAVROS/ROS2/autopilot deployments should
use that manifest as the connection contract instead of depending on profile-name
conventions. If generated ROS2 bridge services stay enabled, external ROS2 client
containers should join the generated `agent_internal-N` network or provide their
own DDS discovery configuration.

## Source Layout

```text
stacks/orchestrator/
  generate_stack.py           # primary generator CLI
  catalogs/                   # built-in environment, runtime, asset catalogs
  examples/ScenarioSpec/      # runnable demo input
  stackgen/
    scenariospec.py           # ScenarioSpec resolver
    models.py                 # normalized generator model
    autopilots/               # ardupilot/px4 port and container contracts
    generators/               # artifact writers
```
