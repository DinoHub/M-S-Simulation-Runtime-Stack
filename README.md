# MnS Product

This repository is the customer distribution of the MnS product. Its historical repository name is `M-S-Simulation-Runtime-Stack`, but it contains the whole product shell, not a single runtime stack.

The first screen is the browser product shell. It launches ScenarioLab in a separate Unreal window for authoring, runs the stack generator image for validation and generation, and owns generated-stack run, status, logs, and stop actions.

## Dashboard Entry Point (full loop in the browser)

For teams that want configuration → run → evaluation in one UI instead of
the CLI wrappers:

```bash
make dashboard          # pulls published images; UI at http://localhost:3001
make dashboard-down
```

The dashboard's **Scenario Configuration** tab authors a ScenarioSpec and
generates + launches stacks through the pinned `MNS_STACK_GENERATOR_IMAGE`
(no source checkouts — same distribution contract as `./launch.sh`).
**Runtime Config** edits the evaluation files in the shared runs directory
(`TEVV_RUNS_DIR`, default `~/tevv-runs`; hot-reloaded). **Calibration**
shows the sim-to-real verdicts the `sim-real-eval` worker writes there
automatically after each recorded run (enable with
`runtime.features: { sim_real_eval: true }` in the scenario).

The browser product shell (`./product.sh start`, port 8760) remains the
visual ScenarioLab authoring surface; the dashboard links to it. Grafana
monitoring stays on :3000 — the dashboard uses :3001.

---

## Quick Start

Requirements: Docker Engine with Compose, an NVIDIA-capable runtime for Unreal images, X11 when opening ScenarioLab, and a Docker login that can pull the private `dhdevspace/auto_mns` images. The product wrapper mounts the active Docker config read-only so generated stacks can pull their pinned runtime dependencies.

```bash
./product.sh setup
./product.sh doctor
./product.sh start
```

Open <http://127.0.0.1:8760> (`MNS_SCENARIO_LAUNCHER_PORT`; it was 8765 until
the Foxglove websocket claimed that port).

1. The Runtime form is prefilled with `scenarios/blocks-quickstart`. Click **Generate and Run** for the shortest end-to-end check.
2. Use **Show Status**, **Show Logs**, and **Stop Runtime** for `generated/blocks-quickstart`.
3. Click **Open Editor** to load the same scenario in ScenarioLab. The authoring image seeds the curated Blocks level, vehicle pack, and built-in placeable primitive pack into the persistent Pack Library on first launch.
4. Edit and export into `scenarios/`, then run the exported ScenarioSpec from the Runtime form.

Stop the browser shell with:

```bash
./product.sh stop
```

`product-images.env` pins the four-image review channel: product shell, ScenarioLab authoring, stack generator, and Blocks runtime. Customer setup only pulls images; it never builds source. MnSPackaging is upstream content-production tooling and is not part of this E2E image set.

Pins are `repo:tag@sha256:…` — the digest is the contract, the tag is there so the file is readable. `tools/check-image-pins.sh` reports staleness and `--bump` rewrites both parts. Why it works this way, and what it costs: [ADR 0001](https://github.com/DinoHub/MnS-Integration-Platform/blob/main/docs/adr/0001-image-versioning-and-digest-pinning.md).

### When the registry is unreachable

Services on mutable tags pull on every start, so a Docker Hub outage aborts the
whole `up` — even when the image is already cached locally:

```
failed to resolve reference "docker.io/dhdevspace/auto_mns:tevv-airsim-xfs-latest":
failed to do request: Head "https://registry-1.docker.io/v2/...": net/http: TLS handshake timeout
```

These blips are usually brief, so retry first (`./product.sh setup` now retries
each pull three times with backoff). To start from the local cache instead, set
the pull policy for the stack you are launching:

| Stack | Variable |
|-------|----------|
| Generated stacks (`generated/<name>/`), scenario stacks, metrics | `MNS_IMAGE_PULL_POLICY=missing` |
| TEVV dashboard (`make dashboard`) | `DASHBOARD_PULL_POLICY=missing` |

```bash
MNS_IMAGE_PULL_POLICY=missing ./launch.sh ardupilot-xfs
DASHBOARD_PULL_POLICY=missing make dashboard
```

Generated stacks also carry the variable in their own `generated/<name>/.env`,
which the launcher reads — edit it there to make the setting stick. Confirm what
is cached with `docker images | grep auto_mns`; `./product.sh doctor` reports any
of the four pinned product images that are missing. `launch.sh` and
`make dashboard` warn up front when the registry does not answer.

## What will this stack publish?

The bridges' topic names are the product of four inputs that only meet at
runtime — `settings.json` sensors/cameras, `topic_names.yaml` renames,
`topic_prefix`/`TOPIC_PREFIX`, and the bridge's fixed topic list. Resolve them
before starting anything:

```bash
make topics                                # default scenario
make topics SCENARIO=ardupilot-xfs
make topics STACK=generated/xfs-fisheye
./tools/preview_topics.py ardupilot-xfs --json
TOPIC_PREFIX=/ make topics SCENARIO=ardupilot-xfs   # preview the flattened names
```

`./launch.sh` prints a short version of this before every `up`; set
`PREVIEW_TOPICS=false` to skip it.

The names come from the bridge image's own launch code (`_final_topic`,
`_canonical_vehicle_topics`, `load_topic_renames`) and each entry launch file's
declared argument defaults — not a second copy of the rules here — so a new
bridge image changes this output with it. That matters because the two bridge
images in use disagree: `airsim-ros2-bridge` defaults `topic_prefix` to
`{vehicle}/` while `tevv-airsim-ros2-bridge-humble` defaults it to `/` and adds a
canonical `lidar/points` alias.

The output separates published topics from services and command inputs, and
flags `topic_names.yaml` keys matching no topic on a vehicle — harmless for a
sensor the scenario does not run, and identical to what a typo'd key looks like.

Scope: the vehicle node's own namespace. Checked against a live
`generated/xfs-fisheye` stack, 14 of 16 names matched `ros2 topic list` exactly.
It does not model topics from nodes outside the vehicle node (gimbal commands,
`target_detection`), nor camera images carried over iceoryx SHM rather than ROS.

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
