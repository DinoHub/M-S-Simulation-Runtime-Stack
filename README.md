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

Open <http://127.0.0.1:8760> (`MNS_SCENARIO_LAUNCHER_PORT`; it was 8765 until the Foxglove websocket claimed that port).

`setup` creates the local content-addressed PackStore and pulls the current published product image set. It resolves 14 active image references, including the four standalone-v2 product images and the runtime prerequisites used by generated stacks. Every reference is remote and digest-pinned; no product run depends on a locally built or per-level image.

Use the pull helper directly when you only want to refresh the image cache:

```bash
./product.sh pull-images                 # active product set
./product.sh pull-images --dry-run       # print exact refs without pulling
./product.sh pull-images --all-catalog   # legacy/optional catalog entries too
./product.sh pull-images --refresh-moving
```

`--refresh-moving` resolves the catalog entries that explicitly declare a `-latest` alias, records their new immutable digests, regenerates the image files, and then pulls them. Commit and review those catalog changes before using them for a release. The normal command never silently changes a digest.

Before generating a v2 runtime, install a checksum-verified `.mnslevelpack` into `.mns/pack-store` using the release installer delivered with that pack. The ScenarioSpec must select it with `environment.id`, `environment.version`, and `environment.artifact_digest`. ScenarioLab and the generic TEVVRuntimeHost load the exact same artifact.

XFS, SAFTI, Condo, and Pendleton v2 packs have not yet been published, so the image flow and product shell are ready but a full environment run remains blocked until one of those packs is installed.

Stop the browser shell with:

```bash
./product.sh stop
```

The standalone-v2 image set contains ScenarioLab authoring, the product shell, the stack generator, and one generic TEVVRuntimeHost. Customer setup only pulls images; it never builds source. MnSPackaging is upstream content-production tooling and is not part of this consumer image set.

Every image reference in this repository is authored in `images/catalog.yaml` and rendered by `tools/images.sh sync`. Pins use `repo:tag@sha256:...`: the digest is the release contract and the tag keeps the reference readable. `tools/images.sh report` shows staleness, `bump` rewrites both parts, and `verify` is the CI drift gate. See [Image operations](docs/images.md), [ADR 0002](docs/adr/0002-one-image-catalog.md), and the platform [image-versioning ADR](https://github.com/DinoHub/MnS-Integration-Platform/blob/main/docs/adr/0001-image-versioning-and-digest-pinning.md).

ScenarioLab launches mount authoring-only AirSim settings that select `ComputerVision` mode with no AirSim vehicles, preventing the vehicle-type prompt from blocking the authoring UI.

If Docker Hub is temporarily unreachable, `setup` retries each exact pull three times with backoff. Once all pins are cached, `./product.sh doctor` confirms the active set without contacting the registry. Generated stacks can use `MNS_IMAGE_PULL_POLICY=missing` to prefer those cached, digest-verified images.

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

Scope: the vehicle node's own graph. Checked against a live
`generated/xfs-fisheye` stack, every name matched `ros2 topic list` — which also
lists ROS's own `/clock`, `/rosout`, `/parameter_events`, `/tf`, `/tf_static`.
Two cases the listing calls out rather than hides: with `enable_vio` or
`enable_shm_fisheye` the camera rides iceoryx shared memory and the vehicle node
publishes no camera topic at all, and the settings.json-named lidar is
superseded by the canonical `lidar/points` alias. Nodes outside the bridge
launch are not visible here.

## Full Acceptance Test

The existing acceptance harness below covers the previous v1/Blocks product release and is retained for rollback verification:

```bash
./tests/full-product-e2e/run.sh
```

See [Full Product E2E](tests/full-product-e2e/README.md). Do not treat that test as standalone-v2 acceptance. The v2 acceptance run requires a published pack whose digest loads in both ScenarioLab and TEVVRuntimeHost; it must also verify runtime cleanup. Until the four packs are published, the available smoke path is `setup` -> `doctor` -> `start` -> HTTP check -> `stop`.

## Headless CLI

The same product shell image exposes equivalent CLI actions:

```bash
./product.sh cli check
./product.sh cli runtime --scenario /workspace/scenarios/<scenario> --out /workspace/generated/<scenario> --no-run
./product.sh cli run-stack --stack /workspace/generated/my_scenario --detach
./product.sh cli status --stack /workspace/generated/my_scenario
./product.sh cli logs --stack /workspace/generated/my_scenario
./product.sh cli stop --stack /workspace/generated/my_scenario
```

Paths passed to the container must be under this repository, mounted as `/workspace`.

The previous named Compose stacks remain documented in [Legacy static stacks](docs/legacy-static-stacks.md). They are compatibility workflows, not the product architecture.
