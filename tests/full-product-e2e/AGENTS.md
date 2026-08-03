# Full Product E2E Guidance

## Scope

This directory owns the source-free customer acceptance path for the four-image product: product shell, ScenarioLab authoring, stack generator, and Blocks runtime. MnSPackaging is an upstream content producer and must not be added to this test or to `product-images.env`.

## Required Coverage

Changes to this test must preserve all three gates:

1. Packaged ScenarioLab loads the fixture, restores two drones, an explicit static object, a random spawn volume, world conditions, and edited sensor settings, then exports a concrete ScenarioSpec.
2. The generator validates that exported ScenarioSpec and emits a stack whose AirSim coordinates, sensors, runtime plugin payload, weather/time payload, services, and immutable image tags match it.
3. The live Blocks stack reports applied conditions and four spawned objects, and both drones publish RGB camera and lidar samples through their isolated ROS domains.

Do not replace semantic checks with file-existence checks. Do not weaken exact review-image assertions when advancing a review tag; update the fixture verifier and `product-images.env` together.

## Runtime Hygiene

`run.sh` must retain trap-based cleanup. Every authoring, product-shell, and generated Compose container started by the test must be stopped and removed on success, failure, or interruption. Cleanup must stay scoped to test-owned names and Compose labels; never stop unrelated developer containers.

Generated exports, stacks, and evidence belong under ignored `scenarios/` and `generated/` paths. Only the deterministic fixture, verifier, runner, and reviewer documentation are committed.
