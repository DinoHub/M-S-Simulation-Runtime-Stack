# packs/

The standalone-v2 content the dashboard and `./product.sh` install, one lock per
engine line, and the host contract every pack in that lock is checked against.

A **channel** is one engine line: a runtime host and ScenarioLab built on the same
Unreal build, the packs cooked for that build, and the generator/shell that
understand those packs' contract. `make dashboard CHANNEL=<name>` and
`MNS_CHANNEL=<name> ./product.sh` select one; the catalog
(`images/catalog.yaml` `consumers.release_channels`) renders its image env file.
Capability mismatches remain rejected. Different base-cook names or registry digests
warn during v1 runtime generation and authoring staging; matching them is no
longer a prerequisite to attempting a load. Each channel keeps its
own pack store and authoring data root under `.mns/`.

| Channel | Host capability | Lock | Contract | Store / data root |
|---|---|---|---|---|
| `v1` (default) | `ue-5.8.2-cl56702186-linux-development-vulkan-sm6-iostore-v2` | `v1.0.0.lock.json` | `runtime-host-compatibility.v1.json` | `.mns/v1/pack-store`, `.mns/v1/authoring-data`, pack mount directory `.mns/v1/packs` |
| `ue582` | `ue-5.8.2-cl56702186-linux-development-vulkan-sm6-iostore-v2` | `standalone-v2-ue582.lock.json` | `runtime-host-compatibility.ue582.json` | `.mns/ue582/pack-store`, `.mns/ue582/authoring-data` |
| `v2` | `ue-5.5.4-cl40574608-linux-development-vulkan-sm6-iostore-v2` | `standalone-v2-review.1.lock.json` | `runtime-host-compatibility.json` | `.mns/pack-store`, `.mns/authoring-data` |

## Selecting a channel

`CHANNEL` picks which Unreal line the whole dashboard runs, from the images to
the packs. Packs cooked for one engine never mount on another, so each channel
owns its own pack store and authoring data under `.mns/` and its own lock and
host contract under `packs/`:

```bash
make dashboard                 # CHANNEL=v1, MnS 1.0 on UE 5.8.2 (default): 6 level + 5 object packs, ~7.7 GB
make dashboard CHANNEL=ue582   # the earlier UE 5.8.2 review set
make dashboard CHANNEL=v2      # the previous UE 5.5.4 set: 4 level + 3 object packs, ~2.6 GB
MNS_CHANNEL=v2 ./product.sh start
```

Every image on the default `v1` channel is a published digest pin of the
non-Substrate MnS 1.0 baseline: runtime host, ScenarioLab, generator and shell
`*-v1.0.0` ([docs/releases/v1.0.0.md](../docs/releases/v1.0.0.md) says what they
were built from). A channel may also carry `channel: local` rows for images built on this
machine; `tools/ensure-images.sh` and `./product.sh doctor` refuse to start a
channel whose local images are missing. The dashboard's Content phase shows the
active engine line, the packs published for it, and installs the missing ones.

## The pack mount directory (`MNS_PACKS_DIR`)

Every channel has one operator-facing directory for packs: `.mns/<channel>/packs`
by default, or any host path in `MNS_PACKS_DIR`. It is mounted read-only into the
product shell (`/mnt/mns/packs`) and, at its identical host path, into the
dashboard backend. Anything that lands there as a `.mnslevelpack` /
`.mnsassetpack` archive is verified by the product shell and installed into the
channel's content-addressed PackStore:

```bash
tools/pull-packs.sh --list-remote                             # releases cooked for this channel's host
tools/pull-packs.sh --release-tag pack-level-warehouse-1.0.1     # pull one published pack (parts are reassembled)
tools/pull-packs.sh --import                                  # install archives already in the mount directory
tools/install-demo-packs.sh --all                             # the channel's locked set
tools/pull-packs.sh --status                                  # locked vs installed vs published
tools/pull-packs.sh --remove blocks --unstage                 # uninstall one, then re-stage
```

Staging never copies a payload twice any more: ScenarioLab's `ResolvedPacks/`
tree and each generated stack's `config/content-packs/` hard-link the store's
blob (falling back to a copy across filesystems, or with
`MNS_PACKS_LINK_MODE=copy`). The store is the mount; consumers only reference it.
No level pack is baked into any image. The v1 authoring image bakes exactly one
pack, `mns_vehicle_models` (drone placement is core authoring); the same pack is
also in the lock so generated stacks resolve it from the store.

## Knowing when a pack is out of date

`--check` compares the lock against the store and never looks remotely.
`--list-remote` looks remotely and never reads the lock or the store. Neither
answers "is there anything newer than what I am running", so `--status` joins
all three into one prioritised list, the way `tools/images.sh status` does for
images:

```bash
tools/pull-packs.sh --status             # or: make pack-status [CHANNEL=v1|ue582|v2]
tools/pull-packs.sh --status --offline   # skip the published-version check
```

It exits nonzero while its NEEDS YOU list is non-empty, so CI can gate on it.
`--json` additionally writes `.mns/<channel>/pack-status.json`, which the
dashboard's Content phase reads: the backend ships no `gh` and gets no GitHub
credentials, so the browser cannot check for itself and shows this file's
`generated_at` rather than implying it is current.

A newer version still reaches everyone the same way — `make pack-lock`, review
the diff, commit. `--status` only removes the guesswork about when to run it.

## Removing a pack

```bash
tools/pull-packs.sh --remove xfs               # refuses if anything still needs it
tools/pull-packs.sh --remove xfs --unstage     # ... and re-stage what is left
tools/pull-packs.sh --remove-orphans           # blob directories the index no longer lists
```

Removal refuses while a generated stack under `generated/` references the
digest, because that stack is still launchable, and while the pack is staged
for ScenarioLab unless `--unstage` is given. It warns, but does not refuse, when
an exported `ScenarioSpec.yaml` or `AssetPacks.yaml` names the digest: a spec is
a record of what was authored, and it becomes generatable again as soon as the
pack is reinstalled. A pack in the lock can always be removed -- the lock is how
you get it back.

Liveness comes from those index files, never from inode link counts: the v1
product shell hard-links payloads out of the store, so a link count says how the
bytes are shared, not whether anything still needs the pack. For the same reason
`--remove` reports the space actually freed rather than the pack's size.

## Files

| File | What it is |
|---|---|
| `standalone-v2-review.1.lock.json` | UE 5.5.4 review set: seven packs (four `.mnslevelpack`, three `.mnsassetpack`) published as one GitHub release of this repository. |
| `v1.0.0.lock.json` | MnS 1.0 non-Substrate baseline: every pack published on `DinoHub/TEVV-Airsim` for its host id. Six level packs: Warehouse 1.0.1 (Aortz, unchanged), Office Environment, Condo, XFS, Safti and Fisherman's Cabin. Four object packs, and the baseline `mns_vehicle_models` 1.0.3 (capability release `pack-asset-mns_vehicle_models-1.0.3-iostore-v2`). The non-Warehouse entries are the `standalone-v2-ue582.lock.json` rows: same releases and host id, byte-identical sha256/digests. ScenarioLab opens Warehouse and Office Environment. The other four levels are runtime-only on this authoring contract (AirSim / CableComponent plugins); the multi-pack E2E flew all six. |
| `standalone-v2-ue582.lock.json` | UE 5.8.2: six level packs (XFS, SAFTI, Condo, Office Environment, Warehouse, Fisherman's Cabin) and four object packs (Office Props, Office Pack Vol 1, Warehouse Props, Fisherman's Cabin Props), each published as its own release on `DinoHub/TEVV-Airsim` (`pack-<kind>-<id>-<version>`; a pack-level `release` overrides the lock-level one). Built by `make pack-lock` / `tools/build_pack_lock.py --discover`. |
| `runtime-host-compatibility.v1.json`, `authoring-host-compatibility.v1.json` | The v1 channel's frozen runtime and ScenarioLab contracts (same id; ScenarioLab's plugin set is smaller). |
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

### From the dashboard and `./download-packs.sh`

Content packs are downloaded by `./download-packs.sh` (`--list` shows the
channel's checksum-locked demo packs; MnS 1.0: six levels, four object packs
and the vehicle models, about 7.7 GB), which installs them into the channel's
pack store through the product-shell image. `make dashboard` itself does not
download packs unless `MNS_DEMO_PACKS` is set; it stages what the store holds
for ScenarioLab (warning when that is nothing), and seeds ScenarioLab's
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
./download-packs.sh --warehouse --office                 # a subset (./download-packs.sh --list shows them)
make dashboard MNS_DEMO_PACKS=--all                      # install missing packs, then start
make dashboard MNS_SKIP_PACK_INSTALL=1                   # skip the pack store check too
```

The Author tab's preflight reports `pack_store` (what is installed) and
`packs_staged` (whether ScenarioLab can see it); the wizard's Environment list
is the staged level packs, and the spec it builds carries the pack's version
and artifact digest.

### The installer underneath

`./download-packs.sh` installs content packs (see above). The underlying installer, for scripting or the product shell:

```bash
tools/install-demo-packs.sh --all             # the default channel's (MnS 1.0) eleven packs
tools/install-demo-packs.sh --safti --office-props
tools/install-demo-packs.sh --objects         # every object pack in the lock
tools/install-demo-packs.sh --missing --all   # only what the store lacks (what make dashboard runs)
tools/install-demo-packs.sh --check --all     # offline: list installed/missing, exit 1 if any is missing
tools/install-demo-packs.sh --lock packs/standalone-v2-review.1.lock.json --help   # the 5.5.4 set's selections
```

The installer downloads the assets declared in the selected lock (`--lock` or `MNS_DEMO_PACK_LOCK`; default `packs/v1.0.0.lock.json`), verifies their full SHA-256 checksums, installs them into the channel's content-addressed pack store, and refreshes ScenarioLab's resolved pack index. Selections are the lock's: `--warehouse`, `--office`, `--condo`, `--xfs`, `--safti`, `--fishermans-cabin`, `--office-props`, `--office-pack-vol-1`, `--warehouse-props`, `--fishermans-cabin-props`, `--mns_vehicle_models` on MnS 1.0. Run with `--dry-run` to inspect the selected immutable assets without downloading them. It refuses to start a download that cannot fit (archive plus store copy) and says how much room it needs; `MNS_DEMO_PACK_DOWNLOAD_DIR` moves the staging area. The product-shell image that performs the install is the lock's digest pin, or `MNS_PRODUCT_SHELL_IMAGE` when set, which is how `make dashboard` keeps install and staging on the selected `IMAGE_MODE`'s shell.

Each generated ScenarioSpec selects an environment with `environment.id`, `environment.version`, and `environment.artifact_digest`. ScenarioLab and the generic TEVVRuntimeHost load the exact same artifact. The only spec committed under `scenarios/` is `vio-reference`, the reference campaign; your own exports land beside it. The catalog includes six authoring vehicle models independently of the three placeable object-vehicle models.

### Another channel's lock, into its own store

```bash
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

All five images are published and pinned by digest:

| role | image | notes |
|---|---|---|
| runtime host | `tevv-runtime-host-20260909.2` | slim host, 2.55 GB; 25-plugin contract |
| ScenarioLab | `mns-authoring-20260909.1` | TEVV-Authoring #15 at 2b3d67fd: in-process level-pack switching, same 25-plugin contract |
| generator | `mns-stack-generator-20260909` | MnS-Integration-Platform main + PR #93 (30657d1) with the TEVV-Authoring #15 (7fe22c7) content-pack SDK; runtime contract baked |
| product shell | `mns-product-shell-20260909` | same sources; ScenarioLab's contract baked, so staging validates against what the editor can mount and skips what it cannot (#93) |
| ROS 2 bridge | `tevv-airsim-ros2-bridge-humble-20260826` | 5.5.4-era build until TEVV-Airsim-ROS2-Bridge #45 publishes a 5.8.2 one |

The generator and shell were built on this machine and pushed on 2026-09-09
(the published 20260826 ones reject the 5.8.2 packs' `mns.whole-level.v1`
contract, and MnS-Integration-Platform had published nothing newer). Once #93
merges, an integration-side publish supersedes them; repin then. To rebuild
them locally (a newer integration commit, a new authoring contract):

```bash
# MnS-Integration-Platform at the commit to publish, with services/tevv-authoring at TEVV-Authoring #15 (7fe22c7).
# The generator gets the RUNTIME contract, the shell the AUTHORING contract: the shell stages for ScenarioLab.
cd ~/Coding-Projects/MnS-Integration-Platform
git -C services/tevv-authoring checkout 7fe22c7f9cabe2bb46b6eeca982d1a97bde26b67
SHA=$(git rev-parse --short=12 HEAD); DATE=$(date +%Y%m%d)
C=$(mktemp -d) && cp ~/M-S-Simulation-Runtime-Stack/packs/runtime-host-compatibility.ue582.json $C/host-compatibility.json
docker build -f docker/stack-generator.Dockerfile --build-context "mns_authoring_contract=$C" \
  -t dhdevspace/auto_mns:mns-stack-generator-$DATE .
A=$(mktemp -d) && cp ~/M-S-Simulation-Runtime-Stack/packs/authoring-host-compatibility.ue582.json $A/host-compatibility.json
docker build -f docker/product-shell.Dockerfile --build-context "mns_authoring_contract=$A" \
  --build-arg DEFAULT_AUTHORING_IMAGE=<the mns_authoring_ue582 pin> \
  --build-arg DEFAULT_STACK_GENERATOR_IMAGE=dhdevspace/auto_mns:mns-stack-generator-$DATE \
  -t dhdevspace/auto_mns:mns-product-shell-$DATE .
docker push dhdevspace/auto_mns:mns-stack-generator-$DATE && docker push dhdevspace/auto_mns:mns-product-shell-$DATE
```

Then set the two rows' `tag`/`digest` in `images/catalog.yaml` (`docker buildx
imagetools inspect <ref>` gives the digest), `tools/images.sh sync`, and
`make pack-lock` so the lock's `required_images` follow.

ScenarioLab's `mns_vehicle_models` default pack is still seeded from the v1
authoring image on this channel: no authoring image ships it yet (TEVV-Authoring
`tooling/asset_packs/provision_default_packs.sh` builds it, and the #15
Dockerfile copies `ScenarioLab/AssetPacks` when present, so the fix is to run
the provisioning before the image build).

Known content defect: `condo-level@1.0.0` has no `PlayerStart` tagged
`MnSScenarioOrigin` (the Condo `L_Main.umap` on TEVV-Airsim `feat/ue5.8-migration`
carries four PlayerStarts, none tagged), so the generic runtime host aborts on
it with `Generic pack host requires exactly one PlayerStart tagged
MnSScenarioOrigin; found 0`. It authors fine; use another level for a runtime
run until a condo-level 1.0.1 with the tagged PlayerStart is published.

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
