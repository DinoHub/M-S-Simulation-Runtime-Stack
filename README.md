# MnS Product

This repository is the customer distribution of the MnS product. Its historical repository name is `M-S-Simulation-Runtime-Stack`, but it contains the whole product shell, not a single runtime stack.

The first screen is the browser product shell. It launches ScenarioLab in a separate Unreal window for authoring, runs the stack generator image for validation and generation, and owns generated-stack run, status, logs, and stop actions.

## Dashboard Entry Point (full loop in the browser)

For teams that want configuration → run → evaluation in one UI instead of
the CLI wrappers:

```bash
make dashboard                         # local-first; pulls only missing tags
make dashboard IMAGE_MODE=production   # exact release pins
make dashboard-down
```

By default, `make dashboard` runs the transitional development workflow: it keeps any locally built matching image tags, pulls only tags absent from the Docker image store, and uses the tag-only development image-set overlay for generated stacks. It does not refresh an existing tag. Run `./product.sh setup` when you deliberately want the approved remote images refreshed; use `IMAGE_MODE=production` to test the immutable release pins.

`make dashboard` also onboards the standalone-v2 content: on the first run it
downloads the channel's checksum-locked demo packs (UE 5.8.2: five levels and
two object packs, about 5.7 GB; Office Environment and XFS are the big ones)
into the channel's pack store through the product-shell image, then stages them
for ScenarioLab, and seeds ScenarioLab's
PackLibrary with the `mns_vehicle_models` and `scenario_runtime_basic` asset
packs from the pinned v1 authoring image (the standalone-v2 authoring image
ships no default packs, and the editor cannot add a drone without the vehicle
models). Later runs only compare
the lock against the store (`tools/install-demo-packs.sh --missing`) and
re-stage when `.mns/pack-store/index.json` changed, so they cost nothing. The
dashboard, the product shell and the generator all read that one store, and
the generated stack's generic TEVVRuntimeHost loads the same immutable artifact
ScenarioLab authored against.

```bash
make dashboard MNS_DEMO_PACKS="--safti --office-props"   # a subset (selections are the lock's; --help lists them)
make dashboard MNS_SKIP_PACK_INSTALL=1                   # offline, or v1-only work
```

The Author tab's preflight reports `pack_store` (what is installed) and
`packs_staged` (whether ScenarioLab can see it); the wizard's Environment list
is the staged level packs, and the spec it builds carries the pack's version
and artifact digest.

To run a dashboard backend or frontend you built locally from a
TEVV-Web-Dashboard branch, put `DASHBOARD_BACKEND_IMAGE=` /
`DASHBOARD_FRONTEND_IMAGE=` in `./.env`: a key set there (or in the shell) is
never overridden by the generated image env files, and `pull_policy: missing`
keeps a local tag.

### Engine lines

`CHANNEL` picks which Unreal line the whole dashboard runs, from the images to
the packs. Packs cooked for one engine never mount on another, so each channel
owns its own pack store and authoring data under `.mns/` and its own lock and
host contract under `packs/` (see [packs/README.md](packs/README.md)):

```bash
make dashboard                 # CHANNEL=ue582, UE 5.8.2 (default): 5 level + 2 object packs, ~5.7 GB
make dashboard CHANNEL=v2      # the previous UE 5.5.4 set: 4 level + 3 object packs, ~2.6 GB
MNS_CHANNEL=v2 ./product.sh start
```

The 5.8.2 channel's runtime host (`tevv-runtime-host-20260909.2`, 2.55 GB) and
ScenarioLab (`mns-authoring-20260909.1`, switches level packs in-process) are
published pins; its generator and product shell are `channel:
local` rows built on this machine until published (build steps in
`packs/README.md`). `tools/ensure-images.sh` and `./product.sh doctor` refuse
to start a channel whose local images are missing. The dashboard's Content
phase shows the active engine line, the packs published for it, and installs
the missing ones.

The dashboard’s **Scenario Configuration** tab authors a ScenarioSpec and
generates + launches stacks through the selected `MNS_STACK_GENERATOR_IMAGE`
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

Requirements: Docker Engine with Compose, Python 3 with `pip install -r tools/requirements.txt` (`./product.sh setup`, `doctor`, and `pull-images` resolve their image list through `tools/images.sh`, which needs PyYAML), an NVIDIA-capable runtime for Unreal images, X11 when opening ScenarioLab, and a Docker login that can pull the private `dhdevspace/auto_mns` images. The product wrapper mounts the active Docker config read-only so generated stacks can pull their pinned runtime dependencies.

```bash
./product.sh setup
./product.sh doctor
./product.sh start
```

Open <http://127.0.0.1:8760> (`MNS_SCENARIO_LAUNCHER_PORT`; it was 8765 until the Foxglove websocket claimed that port).

`setup` creates the local content-addressed PackStore, refreshes the 14 immutable production pins, and then refreshes the 14 mutable development tags used by `make dashboard`. This is the deliberate operation that replaces matching local development tags with their published versions; normal dashboard starts never do that. No product configuration uses `local/...` repository names or per-level runtime images.

Use the pull helper directly when you only want to refresh the image cache:

```bash
./product.sh pull-images                 # active product set
./product.sh pull-images --dry-run       # print exact refs without pulling
./product.sh pull-images --development    # explicitly refresh dashboard development tags
./product.sh pull-images --all-catalog   # legacy/optional catalog entries too
./product.sh pull-images --refresh-moving
```

`--refresh-moving` runs `tools/images.sh bump --channel moving`: it advances every `channel: moving` row — the legacy simulator, observability, and dashboard images, whose tags are republished in place — to whatever digest that tag resolves to now, regenerates the image files, and then pulls them. It deliberately does **not** touch the standalone-v2 rows: those are `channel: pinned`, and `bump` refuses pinned rows so a release pin only ever moves by hand. Because it rewrites `images/catalog.yaml`, it cannot be combined with `--dry-run`; use `tools/images.sh report` to preview instead. Commit and review those catalog changes before using them for a release. The normal command never silently changes a digest.

`make dashboard` installs the checksum-verified standalone-v2 demo catalog itself. For the product shell, or to install a subset by hand:

```bash
tools/install-demo-packs.sh --all             # the default channel's (UE 5.8.2) seven packs
tools/install-demo-packs.sh --safti --office-props
tools/install-demo-packs.sh --objects         # every object pack in the lock
tools/install-demo-packs.sh --missing --all   # only what the store lacks (what make dashboard runs)
tools/install-demo-packs.sh --check --all     # offline: list installed/missing, exit 1 if any is missing
tools/install-demo-packs.sh --lock packs/standalone-v2-review.1.lock.json --help   # the 5.5.4 set's selections
```

The installer downloads the assets declared in the selected lock (`--lock` or `MNS_DEMO_PACK_LOCK`; default `packs/standalone-v2-ue582.lock.json`), verifies their full SHA-256 checksums, installs them into the channel's content-addressed pack store, and refreshes ScenarioLab's resolved pack index. Selections are the lock's: `--condo`, `--safti`, `--xfs`, `--office`, `--warehouse`, `--office-props`, `--warehouse-props` on 5.8.2. Run with `--dry-run` to inspect the selected immutable assets without downloading them. It refuses to start a download that cannot fit (archive plus store copy) and says how much room it needs; `MNS_DEMO_PACK_DOWNLOAD_DIR` moves the staging area. The product-shell image that performs the install is the lock's digest pin, or `MNS_PRODUCT_SHELL_IMAGE` when set, which is how `make dashboard` keeps install and staging on the selected `IMAGE_MODE`'s shell.

Each generated ScenarioSpec selects an environment with `environment.id`, `environment.version`, and `environment.artifact_digest`. ScenarioLab and the generic TEVVRuntimeHost load the exact same artifact. The specs committed under `scenarios/` predate this and carry only `environment.id`, so they cannot select a v2 level pack — each one now says so in its header, and `tools/images.sh drift` skips them by name rather than reporting a generation failure. They remain as a reference for the legacy static stacks under `compose/`; re-author them in ScenarioLab to migrate. The catalog includes six authoring vehicle models independently of the three placeable object-vehicle models.

Stop the browser shell with:

```bash
./product.sh stop
```

The standalone-v2 image set contains ScenarioLab authoring, the product shell, the stack generator, and one generic TEVVRuntimeHost. Customer setup only pulls images; it never builds source. MnSPackaging is upstream content-production tooling and is not part of this consumer image set.

Every image reference in this repository is authored in `images/catalog.yaml` and rendered by `tools/images.sh sync`. Pins use `repo:tag@sha256:...`: the digest is the release contract and the tag keeps the reference readable. `tools/images.sh report` shows staleness, `bump` rewrites both parts, and `verify` is the CI drift gate. See [Image operations](docs/images.md), [ADR 0002](docs/adr/0002-one-image-catalog.md), and the platform [image-versioning ADR](https://github.com/DinoHub/MnS-Integration-Platform/blob/main/docs/adr/0001-image-versioning-and-digest-pinning.md).

ScenarioLab launches mount authoring-only AirSim settings that select `ComputerVision` mode with no AirSim vehicles, preventing the vehicle-type prompt from blocking the authoring UI.

If Docker Hub is temporarily unreachable, `setup` retries each exact pull three times with backoff. Once all pins are cached, `./product.sh doctor` confirms the active set without contacting the registry. Generated stacks default to `MNS_IMAGE_PULL_POLICY=missing`, so they use cached, digest-verified images and pull only when a pin is absent. Set it to `always` only for a deliberate per-run registry check; use `./product.sh pull-images` for the normal refresh workflow.

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

See [Full Product E2E](tests/full-product-e2e/README.md). Do not treat that test as standalone-v2 acceptance. The v2 acceptance run requires a published pack whose digest loads in both ScenarioLab and TEVVRuntimeHost; it must also verify runtime cleanup.

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
