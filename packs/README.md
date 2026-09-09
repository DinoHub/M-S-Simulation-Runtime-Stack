# packs/

The standalone-v2 content the dashboard and `./product.sh` install, one lock per
engine line, and the host contract every pack in that lock is checked against.

A **channel** is one engine line: a runtime host and ScenarioLab built on the same
Unreal build, the packs cooked for that build, and the generator/shell that
understand those packs' contract. `make dashboard CHANNEL=<name>` and
`MNS_CHANNEL=<name> ./product.sh` select one; the catalog
(`images/catalog.yaml` `consumers.release_channels`) renders its image env file.
Packs cooked for one host id never mount on another, so each channel keeps its
own pack store and authoring data root under `.mns/`.

| Channel | Host capability | Lock | Contract | Store / data root |
|---|---|---|---|---|
| `ue582` (default) | `ue-5.8.2-cl56702186-linux-development-vulkan-sm6-iostore-v2` | `standalone-v2-ue582.lock.json` | `runtime-host-compatibility.ue582.json` | `.mns/ue582/pack-store`, `.mns/ue582/authoring-data` |
| `v2` | `ue-5.5.4-cl40574608-linux-development-vulkan-sm6-iostore-v2` | `standalone-v2-review.1.lock.json` | `runtime-host-compatibility.json` | `.mns/pack-store`, `.mns/authoring-data` |

## Files

| File | What it is |
|---|---|
| `standalone-v2-review.1.lock.json` | UE 5.5.4 review set: seven packs (four `.mnslevelpack`, three `.mnsassetpack`) published as one GitHub release of this repository. |
| `standalone-v2-ue582.lock.json` | UE 5.8.2: six level packs (XFS, SAFTI, Condo, Office Environment, Warehouse, Fisherman's Cabin) and four object packs (Office Props, Office Pack Vol 1, Warehouse Props, Fisherman's Cabin Props), each published as its own release on `DinoHub/TEVV-Airsim` (`pack-<kind>-<id>-<version>`; a pack-level `release` overrides the lock-level one). Built by `make pack-lock` / `tools/build_pack_lock.py --discover`. |
| `runtime-host-compatibility*.json` | The frozen host capability contract baked into the channel's runtime host image at `/app/TEVVRuntimeHost/TEVVRuntimeHost/Content/TEVVHost/host-compatibility.json`. Its `id` is the lock's `capability_id`; the generator validates every resolved pack against it. |
| `authoring-host-compatibility.ue582.json` | ScenarioLab's own contract, from the authoring image at `/opt/mns/authoring/ScenarioLab/Content/TEVVHost/host-compatibility.json`. Same id, but its plugin set is ScenarioLab's: staging validates packs against it (`MNS_AUTHORING_HOST_CONTRACT`), and the dashboard's Content phase compares each pack's required plugins to it. A pack the runtime host provides plugins for but ScenarioLab does not is **runtime only**: generated stacks fly it, the editor cannot open it, staging skips it (MnS-Integration-Platform `fix/stage-authoring-skips-unmountable-packs`). The 5.5.4 authoring image embeds no contract. |

A lock (`mns.pack_release_lock.v1`) carries, per pack: sizes, SHA-256 of the
archive, the content-addressed `artifact_digest` the store indexes on and a
ScenarioSpec names, the payload digest, the entry map and required plugins. Its
`required_images` restate the channel's image pins because the installer has to
run the product shell before anything has resolved an image set;
`images/catalog.yaml` `consumers.pack_locks` declares which catalog rows those
mirror and `tools/images.sh verify` fails when they drift.

## Why the contract is checked in

The stack generator resolves a ScenarioSpec's `environment` (id, version,
artifact_digest) from the pack store, selects the variant cooked for the runtime
host's capability id, and validates it against the host contract document. The
published generator images read that document from
`MNS_RUNTIME_HOST_COMPATIBILITY_CONTRACT` and ship no default copy, so without
this file every v2 generate stopped with `host compatibility document does not
exist`. The runtime host image is the authority; this file is a copy so the
contract can be handed to a container that never sees that image.

`tools/install-demo-packs.sh` refuses a lock whose `capability_id` differs from
the selected contract's `id`.

## Installing

`make dashboard` installs whatever the selected channel's lock lists and the
store lacks, then stages it for ScenarioLab. By hand:

```bash
tools/install-demo-packs.sh --all                          # 5.8.2 set (default lock)
tools/install-demo-packs.sh --safti --office-props         # a subset
tools/install-demo-packs.sh --check --all                  # offline: what is installed
MNS_DEMO_PACK_LOCK=packs/standalone-v2-review.1.lock.json \
MNS_PACK_STORE_ROOT=.mns/pack-store \
MNS_AUTHORING_DATA_ROOT=.mns/authoring-data \
MNS_RUNTIME_HOST_COMPATIBILITY_CONTRACT=packs/runtime-host-compatibility.json \
  tools/install-demo-packs.sh --all                        # the 5.5.4 set into its own store
```

Selections are read from the lock (`--condo`, `--xfs`, `--office-props`, ...;
`--help` lists the current lock's). Archives already under
`.mns/downloads/pack-cache/` with the right checksum are reused, never
re-downloaded.

## The 5.8.2 channel today (2026-09-09)

Published and pinned by digest: `tevv-runtime-host-20260909.2` (the slimmed
host, 2.55 GB; same capability id as 20260908, contract adds the CableComponent
and Niagara engine plugins) and `mns-authoring-20260909.1` (TEVV-Authoring #15
at 2b3d67fd: in-process level-pack switching via `mns.Authoring.SelectLevelPack`
with a restart-request fallback, and the same 25-plugin contract as the runtime
host). Both carry the 5.8.2 host id. `mns-authoring-20260908` predates both the
switching work and the two plugins, so it needed a restart to change packs and
rejected the Fisherman's Cabin level; `mns-authoring-20260909` (no suffix) is a
superseded early push of the same day and can be deleted from the registry. Not published:
a generator or product shell that accepts the 5.8.2 packs. The 20260826 shell
rejects them with `level pack must declare a supported strict, whole-level, or
actor-selection contract`; the parser that accepts the new `mns.whole-level.v1`
contract is on TEVV-Authoring #15. Both are therefore `channel: local` catalog
rows built on this machine:

```bash
# MnS-Integration-Platform at fix/stage-authoring-skips-unmountable-packs (main + the staging
# fix) with services/tevv-authoring at TEVV-Authoring #15 (7fe22c7). The generator gets the
# RUNTIME contract, the shell the AUTHORING contract: the shell stages for ScenarioLab.
cd ~/Coding-Projects/MnS-Integration-Platform
git -C services/tevv-authoring checkout 7fe22c7f9cabe2bb46b6eeca982d1a97bde26b67
C=$(mktemp -d) && cp ~/M-S-Simulation-Runtime-Stack/packs/runtime-host-compatibility.ue582.json $C/host-compatibility.json
SHA=$(git rev-parse --short=12 HEAD)
docker build -f docker/stack-generator.Dockerfile --build-context "mns_authoring_contract=$C" \
  -t local/mns-stack-generator:ue582-local.$SHA .
A=$(mktemp -d) && cp ~/M-S-Simulation-Runtime-Stack/packs/authoring-host-compatibility.ue582.json $A/host-compatibility.json
docker build -f docker/product-shell.Dockerfile --build-context "mns_authoring_contract=$A" \
  --build-arg DEFAULT_AUTHORING_IMAGE=dhdevspace/auto_mns:mns-authoring-20260909.1@sha256:cd7e54e70079564210b33ebe6db85f364bb40b378a3163df5e6034ed8e8a893c \
  --build-arg DEFAULT_STACK_GENERATOR_IMAGE=local/mns-stack-generator:ue582-local.$SHA \
  -t local/mns-product-shell:ue582-local.$SHA .
```

Then update the two `local/` rows in `images/catalog.yaml` to the new tag,
`tools/images.sh sync`, and rebuild the lock (below) so its `required_images`
follow. When MnS-Integration-Platform publishes a generator and shell built on
the #15 SDK, replace the local rows with `channel: pinned` rows and the
`--shell` argument below with the published pin.

The ROS 2 bridge on this channel is still the 5.5.4-era build
(TEVV-Airsim-ROS2-Bridge #45 is open). ScenarioLab's `mns_vehicle_models`
default pack is seeded from the v1 authoring image on this channel too: the
5.8.2 image ships none, and the 5.5.4-cooked pak mounts in the 5.8.2 editor.

Known content defect: `condo-level@1.0.0` has no `PlayerStart` tagged
`MnSScenarioOrigin` (9 selected actors), so the generic runtime host aborts on
it with `Generic pack host requires exactly one PlayerStart tagged
MnSScenarioOrigin; found 0`. It authors fine; use `safti-level@1.0.1` or
another level for a runtime run until a condo-level 1.0.1 is published.

## Building or refreshing a lock

A lock is a snapshot of what packaging had published when it was built. New
pack releases on TEVV-Airsim do not appear anywhere (not in `make dashboard`,
not in the dashboard's Content phase) until the lock is rebuilt:

```bash
make pack-lock            # CHANNEL=ue582: every pack-* release cooked for the channel's host id, newest per pack
git diff packs/           # review, commit
make dashboard            # installs what is new
```

`make pack-lock` runs `tools/build_pack_lock.py --discover`; pass
`--release-tag pack-level-condo-level-1.0.0 ...` instead to pin an explicit set.
Each release's `artifact.json`, manifest and `.sha256` sidecar supply the
receipts; the bundle is fetched into `.mns/downloads/pack-cache/` (reused by the
installer) and its `artifact_digest` comes from the channel's product shell
(`packs verify --host <id>`), so a bundle the shell rejects never enters a lock.
`gh` must be logged in with access to the release repository. A release without
a variant for the channel's host id is skipped and named on stderr.

## Refreshing a contract after a runtime-host bump

```bash
. images/standalone-v2-ue582.generated.env
docker run --rm --entrypoint cat "$MNS_RUNTIME_HOST_IMAGE" \
  /app/TEVVRuntimeHost/TEVVRuntimeHost/Content/TEVVHost/host-compatibility.json \
  > packs/runtime-host-compatibility.ue582.json
```

A pack version is immutable: packs built for the old id need new versions and a
new lock (MnS-Integration-Platform `docs/scenario-platform/level-pack-v2-workflow.md`).
