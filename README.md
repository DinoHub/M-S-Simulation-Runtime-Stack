# MnS Product

This repository is the customer distribution of the MnS product. Its historical repository name is `M-S-Simulation-Runtime-Stack`, but it contains the whole product shell, not a single runtime stack.

The first screen is the browser product shell. It launches ScenarioLab in a separate Unreal window for authoring, runs the stack generator image for validation and generation, and owns generated-stack run, status, logs, and stop actions.

## Quick Start

Requirements: Docker Engine with Compose, an NVIDIA-capable runtime for Unreal images, and X11 when opening ScenarioLab.

```bash
./product.sh setup
./product.sh doctor
./product.sh start
```

Open <http://127.0.0.1:8765>.

1. The Runtime form is prefilled with `scenarios/blocks-quickstart`. Click **Generate and Run** for the shortest end-to-end check.
2. Use **Show Status**, **Show Logs**, and **Stop Runtime** for `generated/blocks-quickstart`.
3. Click **Open Editor** to load the same scenario in ScenarioLab. The authoring image seeds the curated Blocks level, vehicle pack, and built-in placeable primitive pack into the persistent Pack Library on first launch.
4. Edit and export into `scenarios/`, then run the exported ScenarioSpec from the Runtime form.

Stop the browser shell with:

```bash
./product.sh stop
```

`product-images.env` pins the four-image review channel: product shell, ScenarioLab authoring, stack generator, and Blocks runtime. Customer setup only pulls images; it never builds source. MnSPackaging is upstream content-production tooling and is not part of this E2E image set.

## Full Acceptance Test

Reviewers can run the deterministic packaged-authoring-to-live-runtime acceptance path with:

```bash
./tests/full-product-e2e/run.sh
```

It checks authored drones, static and random-spawned objects, ROS FLU to AirSim NED conversion, rain/time conditions, generated sensor settings, live RGB/lidar topics for both drones, and test-container cleanup. See [Full Product E2E](tests/full-product-e2e/README.md).

## Headless CLI

The same product shell image exposes equivalent CLI actions:

```bash
./product.sh cli check
./product.sh cli runtime --scenario /workspace/scenarios/blocks-quickstart --out /workspace/generated/blocks-quickstart --no-run
./product.sh cli run-stack --stack /workspace/generated/my_scenario --detach
./product.sh cli status --stack /workspace/generated/my_scenario
./product.sh cli logs --stack /workspace/generated/my_scenario
./product.sh cli stop --stack /workspace/generated/my_scenario
```

Paths passed to the container must be under this repository, mounted as `/workspace`.

The previous named Compose stacks remain documented in [Legacy static stacks](docs/legacy-static-stacks.md). They are compatibility workflows, not the product architecture.
