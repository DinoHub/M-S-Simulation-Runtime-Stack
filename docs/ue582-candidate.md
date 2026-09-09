# UE 5.8.2 candidate: the `ue582` channel

Run from the canonical `M-S-Simulation-Runtime-Stack` checkout. The removed
`M-S-Simulation-Runtime-Stack-standalone-v2` worktree is not a product dependency.

The acceptance chain is browser dashboard → Content phase (packs) → packaged
ScenarioLab → scenario export → stack generator → generic runtime host with
cooked level/object packs. Packaging consumes declared dependencies;
project-specific plugin, streaming, and world implementation choices belong to
the source project.

## What the channel is

`make dashboard CHANNEL=ue582` selects the `standalone_v2_ue582` release channel
from `images/catalog.yaml`: the UE 5.8.2 runtime host
(`tevv-runtime-host-20260909.2`, the 2.55 GB slimmed host) and ScenarioLab
(`mns-authoring-20260909.1`, in-process level-pack switching), pinned by digest
and both labelled
`tevv.content_packs.host_compatibility_id =
ue-5.8.2-cl56702186-linux-development-vulkan-sm6-iostore-v2`, the generator
and product shell (`mns-stack-generator-20260909`, `mns-product-shell-20260909`;
`packs/README.md` says what they were built from), the `ue582` image set for
generated stacks, and `packs/standalone-v2-ue582.lock.json` installed into
`.mns/ue582/`. It is the default channel since 2026-09-09; `CHANNEL=v2`
selects the untouched 5.5.4 set.

Everything is keyed by the one host id. It comes from the runtime host image's
packaging label, which the packaging step wrote from the engine's own
`Build.version`; `tools/check_ue_candidate.py` reads it from there when no
engine is installed (every customer machine) and from `--engine-root` when one
is. ScenarioLab must carry the same label; every selected pack must have a
variant cooked for it. Never relabel old cooked payloads.

## Candidate inputs

```bash
make dashboard                                     # ue582 is the default; installs the 7 packs (~5.7 GB) on first run
make dashboard MNS_DEMO_PACKS="--safti --office-props"
```

Or verify the candidate explicitly before launching, with the gate PR #57
introduced. Without `--engine-root` the host id is read from the lock's
`runtime_host` image label:

```bash
python3 tools/check_ue_candidate.py --lock packs/standalone-v2-ue582.lock.json \
  --pack-store .mns/ue582/pack-store
```

The gate wants more roles than the channel lock carries: `required_images`
must also pin `dashboard_backend`, `dashboard_frontend` and `timescaledb`, and
`--start-dashboard` additionally `ardupilot`, `px4`, `qgroundcontrol`,
`sim_real_eval` and `lichtblick`. `tools/build_pack_lock.py` records the five
channel roles the installer needs, so a gate run needs a copy of the lock with
those roles added by hand. Every channel image is a registry digest, so the
default (strict) mode applies to them; the dashboard images are still local
builds (`local/tevv-web-dashboard-*:v2-dev`), which strict mode rejects, so
until they are published the gate can only run in `--local-images` mode with
their exact IDs in `required_image_ids`. The Makefile path (`make dashboard`)
is the check that runs today.

The check rejects engine/host mismatches, unpinned images, missing or invalid
packs, and bad bundle receipts. Pack integrity uses the Authoring-owned
verifier inside the product-shell image with read-only mounts and network
disabled. After verification, `--start-dashboard` stages packs, writes an
ignored candidate image overlay, and starts the dashboard with explicit image
selections that override stale `.env` values. No release catalog or version is
promoted. Without this flag, no dashboard is started.

### Local-only image set

Use `--local-images` when the reviewed 5.8.2 images are built on this host and
must not be published yet. Default mode is unchanged and still requires every
image to use a registry digest. Local mode permits a mix of those pins and
explicit, non-`latest` local tags, but the lock must also record the exact
Docker image ID for every role:

```json
{
  "schema": "mns.pack_release_lock.v1",
  "capability_id": "ue-5.8.2-cl56702186-linux-development-vulkan-sm6-iostore-v2",
  "required_images": {
    "product_shell": "local/mns-product-shell:ue582-local.a1936b0a5f5f",
    "authoring": "dhdevspace/auto_mns:mns-authoring-20260908@sha256:f9156845…",
    "ros2_bridge": "dhdevspace/auto_mns:tevv-airsim-ros2-bridge-humble-20260826@sha256:e661e37f…"
  },
  "required_image_ids": {
    "product_shell": "sha256:<64 lowercase hex>",
    "authoring": "sha256:<64 lowercase hex>",
    "ros2_bridge": "sha256:<64 lowercase hex>"
  }
}
```

The abbreviated example omits the other mandatory image roles and packs; in a
real lock, `required_image_ids` must have exactly the same keys as
`required_images`. Local tags may use the normal `dhdevspace/auto_mns` name or
a `local/` name, but must be unique in the lock and must not end in `latest`.
The gate compares every `docker image inspect` ID before pack verification or
launch, while retaining the exact host-label and pack checks.

Local pack verification, the candidate image-set overlay, and dashboard launch
all use pull-never behavior. The launch writes the explicit selections and
their inspected IDs to ignored files under `.mns/ue-candidate/`, plus an
executable `.mns/ue-candidate/rerun.sh` that reruns the full verification with
the same absolute paths before launch.

Once the backend is healthy, verify its actual selections:

```bash
python3 tools/check_ue_candidate.py --lock packs/standalone-v2-ue582.lock.json \
  --pack-store .mns/ue582/pack-store --dashboard-container airsim-dashboard-api
```

## Runtime gate

The preflight deliberately reports `e2e_verified: false`. Open
`http://localhost:3001`; the Content phase shows the engine line, the images
and their labels, and the seven packs with state. Launch ScenarioLab from
Author, load a level and object packs, change a scenario, export it, generate
and launch its stack. Record the generated manifest, actual running image IDs,
pack/variant digests, RGB and lidar samples, ROS2 domains, conditions and
object placement, MCP automation, and cleanup results. Repeat across the level
packs and their supported autopilot/multi-vehicle flows. Include
sky/depth/segmentation and asynchronous sensor shutdown regressions from the
owning services.

The legacy `tests/full-product-e2e` runner is not evidence of this browser
acceptance. Unit tests and a successful catalog response are prerequisites
only. Until the real 5.8.2 build, cook, and runtime gates succeed, candidate
PRs remain open with their missing evidence stated explicitly.

Focused checks (no Unreal workload):

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tools -p 'test_check_ue_candidate.py'
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tools -p 'test_install_demo_packs.py'
```

## Known gaps on this channel

- Generator and product shell were built and pushed from a developer machine
  (MnS-Integration-Platform main + PR #93, TEVV-Authoring #15 SDK); repin once
  MnS-Integration-Platform publishes its own after #93 merges.
- The ROS 2 bridge is the 5.5.4-era `tevv-airsim-ros2-bridge-humble-20260826`
  until TEVV-Airsim-ROS2-Bridge #45 publishes a 5.8.2 build.
- `ardupilot-slim-20260826.1` has no SITL binary (catalog follow-up); the drone
  container exits 127 on every channel.
