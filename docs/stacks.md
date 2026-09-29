# Headless: fly, author and campaign from a terminal

Everything the dashboard does has a `make` target that runs the same images the
same way, so a stack generated in one shows up in the other and a bag recorded
by either has one layout. For the full loop in a browser, use `make dashboard`
and the [User Guide](USER_GUIDE.md).

| Task | Dashboard | Headless |
| --- | --- | --- |
| Check packs | preflight | `make pack-status` |
| Author | editor window | `make author [SCENARIO=<name>]` |
| Fly one scenario | Fly | `make fly SCENARIO=<name> [RECORD=1]` |
| Stop a stack | stack down | `make stop [STACK=generated/<name>]` |
| Record | Record button (`mns-stacks record`) | `RECORD=1` |
| Campaign | campaign page | `make campaign CAMPAIGN=<name>` |
| Anything else | | `make stacks ARGS="<mns-stacks command> ..."` |
| Is the machine ready | | `make doctor` |

## Requirements

Docker Engine with Compose, Python 3 with PyYAML (`./setup.sh` checks it), an
NVIDIA-capable runtime for the Unreal images, a desktop session (X11) for
ScenarioLab and the simulator window, and a Docker login that can pull the
private `dhdevspace/auto_mns` images. Run `./setup.sh` and `./download-packs.sh`
first, as for the dashboard. `make doctor` checks Docker, Compose and that every
image the channel pins is on this machine; it changes nothing and pulls nothing.

## Fly one scenario

```bash
make fly SCENARIO=vio-reference                  # generate, fly until the mission is done, stop
make fly SCENARIO=vio-reference RECORD=1         # ... and record a bag
make fly SCENARIO=my-scene ARGS="--timeout 900"  # extra `mns-stacks run` flags
```

`SCENARIO` is a folder under `scenarios/` (what ScenarioLab exports), another
folder, or a `ScenarioSpec.yaml`. `make fly`:

1. brings the packs to the lock's versions, the same check `make dashboard` runs
   (`ensure-demo-packs`);
2. runs `mns-stacks generate <scenario folder> --out generated/<name>` (no Docker
   socket, `--network=none`, as you);
3. runs `mns-stacks run --stack generated/<name> [--record] --until-done`, which
   verifies the stack's resolved packs, brings the stack up, waits for the run
   director's mission-done signal, and stops it (with `finalize_metrics`).

With `RECORD=1` the bag goes to `<runs dir>/<run id>/bag` (`runs/` in this
checkout unless `TEVV_RUNS_DIR` says otherwise), recorded by the bridge
container exactly as the dashboard's Record button records it, so
`sim_real_eval` and the dashboard's replay read it the same way.

If a run is interrupted, `make stop` stops the last stack `make fly` started
(`make stop STACK=generated/<name>` names another). It always runs
`finalize_metrics` first; `docker compose down` still runs if that fails.

## Author

```bash
make author                        # ScenarioLab, empty
make author SCENARIO=my-scene      # open scenarios/my-scene
make author-stop                   # close it
```

`make author` stages the packs for ScenarioLab (as `make dashboard` does) and
starts the authoring image with exactly the `docker run ... editor` command the
dashboard's editor window uses: the same container name, so only one editor
runs whichever started it, exports to `scenarios/<name>/`, the staged packs
from `.mns/v1/authoring-data`, and the desktop's X11 cookie. Run it from a
terminal on the desktop. `MNS_AUTHORING_DOCKER_GPU_ARGS` (default `--gpus all`)
and `MNS_AUTHORING_DOCKER_ARGS` add `docker run` flags.

## Campaigns

```bash
make campaign                               # the reference campaign (scenarios/vio-reference)
make campaign CAMPAIGN=my-test              # yours
make campaign ARGS="status vio-reference"   # any `mns-stacks campaign` subcommand
make campaign-status CAMPAIGN=my-test       # one row per flight
```

See [Campaigns](campaigns.md).

## Any mns-stacks command

`make stacks ARGS="..."` passes its arguments straight to `mns-stacks`:

```bash
make stacks ARGS=--help
make stacks ARGS="status --stack generated/my-scene --json"
make stacks ARGS="logs --stack generated/my-scene --tail 200"
make stacks ARGS="record start --stack generated/my-scene"
make stacks ARGS="check"
```

Every command takes `--json`; the result then comes in one envelope,
`{"schema": "mns.stacks_cli.v1", "command", "ok", "exit_code", "result",
"warnings", "error"}`, with exit code 0 (ok), 1 (a blocking finding) or 2 (the
command could not run).

## How the targets run mns-stacks

`tools/mns-stacks.sh` is the one wrapper all of them use (and
`tools/images.sh drift` and `osmo/campaign.py`). It runs `$MNS_STACKS_IMAGE` as
a sibling container, the way the dashboard does:

- this checkout, the runs directory, and any pack store, image-set file or
  contract outside the checkout are mounted **at their host paths**, and
  mns-stacks is told them (`MNS_WORKSPACE_ROOT`, `SIM2REAL_RUNS_DIR`), so every
  bind mount it hands Compose resolves on the host;
- the Docker socket (and your Docker login, read-only) is mounted only for the
  commands that drive containers: `run`, `stop`, `status`, `logs`, `record`,
  `check`, and `campaign run|status|watch|cancel`. Everything else runs with
  `--network=none` as you, so generated files are yours.

`make` exports the selected channel's images and roots (`CHANNEL=`,
`IMAGE_MODE=`), as it does for the dashboard. To run a local build instead of a
pin, put `MNS_STACKS_IMAGE=mns-stacks:local-test` (or `MNS_PACKS_IMAGE`,
`MNS_AUTHORING_IMAGE`) in `./.env`; the wrapper prints a note that it is using
the override.

## Setup and the image cache

`./setup.sh` pulls every pinned image the channel needs; `make doctor` confirms
the set without contacting the registry. Generated stacks default to
`MNS_IMAGE_PULL_POLICY=missing`, so they use cached, digest-verified images and
pull only when a pin is absent.

```bash
tools/pull-all-images.sh                  # refresh the active product set (make pull-images)
tools/pull-all-images.sh --dry-run        # print exact refs without pulling
tools/pull-all-images.sh --development    # refresh the tags make dashboard's development mode uses
tools/pull-all-images.sh --all-catalog    # optional catalog entries too
tools/pull-all-images.sh --refresh-moving
```

`--refresh-moving` runs `tools/images.sh bump --channel moving`: it advances
every `channel: moving` row (QGroundControl and sim-real-eval until their
immutable v1.0.0 tags are pinned) to whatever digest that tag resolves to now,
regenerates the image files, and then pulls them. It does **not** touch the v1
release rows: those are `channel: pinned`, and `bump` refuses pinned rows so a
release pin only moves by hand. Because it rewrites `images/catalog.yaml`, it
cannot be combined with `--dry-run`; use `tools/images.sh report` to preview.

Every image reference is authored in `images/catalog.yaml` and rendered by
`tools/images.sh sync`. See [Changing a container image](images.md) and
[ADR 0002](adr/0002-one-image-catalog.md).

Content packs are installed with `./download-packs.sh` or the installer
underneath it; see [packs/README.md](../packs/README.md#installing).
