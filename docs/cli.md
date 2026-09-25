# The product shell from a terminal (`product.sh`)

`./product.sh` runs the product-shell image with this repository mounted, so
everything the dashboard does to `scenarios/` and `generated/` is also reachable
from a terminal, and a stack generated in one shows up in the other. The browser
product shell it starts is the visual ScenarioLab authoring surface; for the
full loop in a browser use `make dashboard` and the [User Guide](USER_GUIDE.md).

## Requirements

Requirements: Docker Engine with Compose, Python 3 with `pip install -r tools/requirements.txt` (`./product.sh setup`, `doctor`, and `pull-images` resolve their image list through `tools/images.sh`, which needs PyYAML), an NVIDIA-capable runtime for Unreal images, X11 when opening ScenarioLab, and a Docker login that can pull the private `dhdevspace/auto_mns` images. The product wrapper mounts the active Docker config read-only so generated stacks can pull their pinned runtime dependencies.

## Start and stop

```bash
./product.sh setup
./product.sh doctor
./product.sh start
```

Open <http://127.0.0.1:8760> (`MNS_SCENARIO_LAUNCHER_PORT`; it was 8765 until the Foxglove websocket claimed that port).

Stop the browser shell with:

```bash
./product.sh stop
```

ScenarioLab launches mount authoring-only AirSim settings that select `ComputerVision` mode with no AirSim vehicles, preventing the vehicle-type prompt from blocking the authoring UI.

## Setup and the image cache

`setup` creates the local content-addressed PackStore, refreshes the 14 immutable production pins, and then refreshes the 14 mutable development tags used by `make dashboard`. This is the deliberate operation that replaces matching local development tags with their published versions; normal dashboard starts never do that. No product configuration uses `local/...` repository names or per-level runtime images.

If Docker Hub is temporarily unreachable, `setup` retries each exact pull three times with backoff. Once all pins are cached, `./product.sh doctor` confirms the active set without contacting the registry. Generated stacks default to `MNS_IMAGE_PULL_POLICY=missing`, so they use cached, digest-verified images and pull only when a pin is absent. Set it to `always` only for a deliberate per-run registry check; use `./product.sh pull-images` for the normal refresh workflow.

Use the pull helper directly when you only want to refresh the image cache:

```bash
./product.sh pull-images                 # active product set
./product.sh pull-images --dry-run       # print exact refs without pulling
./product.sh pull-images --development    # explicitly refresh dashboard development tags
./product.sh pull-images --all-catalog   # optional catalog entries too
./product.sh pull-images --refresh-moving
```

`--refresh-moving` runs `tools/images.sh bump --channel moving`: it advances every `channel: moving` row — the dashboard, autopilot, QGroundControl and sim-real-eval images, whose tags are republished in place — to whatever digest that tag resolves to now, regenerates the image files, and then pulls them. It deliberately does **not** touch the standalone-v2 rows: those are `channel: pinned`, and `bump` refuses pinned rows so a release pin only ever moves by hand. Because it rewrites `images/catalog.yaml`, it cannot be combined with `--dry-run`; use `tools/images.sh report` to preview instead. Commit and review those catalog changes before using them for a release. The normal command never silently changes a digest.

The standalone-v2 image set contains ScenarioLab authoring, the product shell, the stack generator, and one generic TEVVRuntimeHost. Customer setup only pulls images; it never builds source. MnSPackaging is upstream content-production tooling and is not part of this consumer image set.

Every image reference in this repository is authored in `images/catalog.yaml` and rendered by `tools/images.sh sync`. Pins use `repo:tag@sha256:...`: the digest is the release contract and the tag keeps the reference readable. `tools/images.sh report` shows staleness, `bump` rewrites both parts, and `verify` is the CI drift gate. See [Image operations](images.md), [ADR 0002](adr/0002-one-image-catalog.md), and the platform [image-versioning ADR](https://github.com/DinoHub/MnS-Integration-Platform/blob/main/docs/adr/0001-image-versioning-and-digest-pinning.md).

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

Nothing records a bag on this path; the User Guide's
[Running stacks without the dashboard](USER_GUIDE.md#running-stacks-without-the-dashboard)
shows how to record one from your own container.

Content packs for this path are installed with `./download-packs.sh` or the
installer underneath it; see [packs/README.md](../packs/README.md#installing).
