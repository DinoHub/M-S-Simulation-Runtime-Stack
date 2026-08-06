# Full Product E2E

This is the reviewer acceptance test for the customer distribution. It uses only the four images pinned in `product-images.env`; no source checkout or packaging image is part of the product path.

## Automated Run

Requirements are the same as the product quick start: Docker Compose, NVIDIA Container Toolkit/GPU access, X11, and an authenticated Docker config with access to the pinned `dhdevspace/auto_mns` images.

```bash
./product.sh setup
./tests/full-product-e2e/run.sh
```

The runner performs these gates:

1. Starts the browser product shell and checks its HTTP surface.
2. Starts packaged ScenarioLab with the deterministic fixture and uses the authoring application to materialize and re-export two drones, one static blocking box, one three-object random spawn zone, rain/time settings, ROS FLU coordinates, and a tuned RGB camera/lidar profile.
3. Validates the exported ScenarioSpec and generates a runtime stack through the pinned generator image.
4. Checks the generated AirSim NED conversion, runtime plugin payload, condition payload, services, sensors, and immutable Blocks image.
5. Starts the generated stack, verifies runtime condition/spawn logs, and samples camera and lidar topics for both drones.
6. Stops and removes every container created by the test, including on failure.

The runner uses `.mns/full-product-e2e/authoring-data/` instead of the normal persistent ScenarioLab data directory, so it cannot overwrite a developer's Pack Library. Evidence from a successful or failed live run is retained under `generated/full-product-e2e-export/e2e-evidence/`. The authored round trip is retained under `scenarios/full-product-e2e-export/`.

## Manual UI Review

For an interaction-focused review, run `./product.sh start`, open <http://127.0.0.1:8760>, and open ScenarioLab. Create or load a Blocks scenario and use the editor controls to add drones, place `scenario_runtime_basic/blocking_box`, add a random spawn volume, select rain/time conditions, and edit a sensor profile. Export under `scenarios/`, then use the browser Runtime form to generate and run it.

Use the automated run as the acceptance gate because its positions, frames, sensor settings, output rates, and cleanup are deterministic.
