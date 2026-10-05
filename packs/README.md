# packs/

The content packs the dashboard, `./download-packs.sh` and the headless targets
install: the v1 channel's lock, and the host contracts every pack in it is
checked against. Every pack operation goes through the content-pack SDK's CLI,
`mns-packs` (TEVV-Content-Pack-SDK), from the pinned `MNS_PACKS_IMAGE`: it
defines what a pack is, and the product only pins versions and runs it.

A **channel** is one engine build: a runtime host and ScenarioLab built on the
same Unreal build, the packs cooked for that build, and the `mns-stacks` and
`mns-packs` images that understand those packs' contract. **v1** (MnS 1.0, UE 5.8.2) is the stable
channel and the only one; `packs/channels.json` lists it for the dashboard's
Content phase, and `images/catalog.yaml` `consumers.release_channels` renders
its image env file (`images/v1.0.0.generated.env`). Packs cooked for one engine
build never mount on another, so a channel keeps its own pack store and
authoring data root under `.mns/<channel>/`.

| Channel | Host capability | Lock | Contracts | Store / data root |
|---|---|---|---|---|
| `v1` | `ue-5.8.2-cl56702186-linux-development-vulkan-sm6-iostore-v2` | `v1.0.0.lock.json` | `runtime-host-compatibility.v1.json`, `authoring-host-compatibility.v1.json` | `.mns/v1/pack-store`, `.mns/v1/authoring-data`, pack mount directory `.mns/v1/packs` |

Every v1 image is a pin in `images/catalog.yaml`: runtime host and its kit,
ScenarioLab, `mns-stacks`, `mns-packs`, bridge and dashboard
([docs/releases/v1.0.0.md](../docs/releases/v1.0.0.md) says what they were
built from). Capability mismatches are rejected; different base-cook names or
registry digests only warn during generation and staging. The dashboard's
Content phase shows the engine build, the packs published for it, and installs
the missing ones.

## The pack mount directory (`MNS_PACKS_DIR`)

Every channel has one operator-facing directory for packs: `.mns/<channel>/packs`
by default, or any host path in `MNS_PACKS_DIR`. It is mounted read-only, at its
identical host path, into the dashboard backend and into `mns-packs` when it
installs from there. Anything that lands there as a `.mnslevelpack` /
`.mnsassetpack` archive is verified by `mns-packs install` and installed into the
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
also in the lock so generated stacks resolve it from the store. TEVV-Authoring
owns its version (`tooling/asset_packs/default_vehicle_pack.json`, now 1.0.4);
the lock's entry and the `v1_authoring` pin in `images/catalog.yaml` move with
it, never on their own.

**The lock decides each pack's version.** `make dashboard` (and
`./download-packs.sh`) run `tools/install-demo-packs.sh --missing --sync`, which
fetches the lock's version of the vehicle models and of every pack already
installed in any version, so a lock bump arrives on the next start; packs never
chosen are never fetched, and a pack `--sync` cannot fetch (no credentials, a
failed download) is a warning, not a failure. `tools/stage-authoring-packs.sh` then
runs `mns-packs stage-authoring --lock packs/v1.0.0.lock.json`, which stages
exactly the lock's version of each pack it pins (a store holding
`mns_vehicle_models` 1.0.3 and 1.0.4 stages only 1.0.4 and reports 1.0.3 as
superseded; a pinned version whose installed bytes differ from the lock is never
staged), and sets ScenarioLab's PackLibrary copy to it, creating the vehicle
models there before the authoring image's entrypoint can copy its baked copy in.
Older versions stay in the store for generated stacks that reference them.

## Knowing when a pack is out of date

`--check` compares the lock against the store and never looks remotely.
`--list-remote` looks remotely and never reads the lock or the store. Neither
answers "is there anything newer than what I am running", so `--status` joins
all three into one prioritised list, the way `tools/images.sh status` does for
images:

```bash
tools/pull-packs.sh --status             # or: make pack-status
tools/pull-packs.sh --status --offline   # skip the published-version check (no GitHub)
```

It exits nonzero while its NEEDS YOU list is non-empty, so CI can gate on it.
`--json` additionally writes `.mns/<channel>/pack-status.json`, which the
dashboard's Content phase reads: the backend ships no `gh` and gets no GitHub
credentials, so the browser cannot check for itself and shows this file's
`generated_at` rather than implying it is current.

A newer version still reaches everyone the same way — `make pack-lock`, review
the diff, commit; each checkout's next `make dashboard` then fetches it
(`--sync`). `--status` only removes the guesswork about when to run it.

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

Liveness comes from those index files, never from inode link counts: staging
hard-links payloads out of the store, so a link count says how the bytes are
shared, not whether anything still needs the pack. For the same reason
`--remove` reports the space actually freed rather than the pack's size.

## Files

| File | What it is |
|---|---|
| `v1.0.0.lock.json` | Every pack published on `DinoHub/TEVV-Airsim` for the v1 host id. Six level packs (Warehouse, Office Environment, Condo, XFS, Safti, Fisherman's Cabin), four object packs, and `mns_vehicle_models` 1.0.4 (release `pack-asset-mns_vehicle_models-1.0.4-iostore-v2`), the version the authoring image bakes. ScenarioLab opens Warehouse, Office Environment, XFS and Safti; Condo 1.0.1 (GeoReferencing) and Fisherman's Cabin (CableComponent) are runtime only; the release's multi-pack E2E flew all six. |
| `runtime-host-compatibility.v1.json` | The frozen host capability contract baked into the runtime host image at `/app/TEVVRuntimeHost/TEVVRuntimeHost/Content/TEVVHost/host-compatibility.json`. Its `id` is the lock's `capability_id`; `mns-stacks generate` validates every resolved pack against it. |
| `authoring-host-compatibility.v1.json` | ScenarioLab's own contract. Same id, but its plugin set is ScenarioLab's: `mns-packs stage-authoring --host` validates packs against it (`MNS_AUTHORING_HOST_CONTRACT`), and the Content phase compares each pack's required plugins to it. Its `runtime_only_plugins` names the runtime host's plugins ScenarioLab leaves out on purpose (AirSim, AirSimFCTeleporter, MetricsEmitter, MetricsVehicleTelemetry, ScenarioDegradation: the simulator and the metrics emitter): a pack that needs only those is staged with a warning and opens in the editor, what it places from them without their runtime behaviour (needs mns-packs with the SDK's runtime-only support). A pack that needs any other plugin ScenarioLab lacks (an engine plugin such as CableComponent) is **runtime only**: generated stacks fly it, the editor cannot open it, staging skips it. |
| `channels.json` | The channel list for the dashboard; `tools/test_channels.py` asserts it matches the Makefile. |

A lock (`mns.pack_release_lock.v1`) carries, per pack: sizes, SHA-256 of the
archive, the content-addressed `artifact_digest` the store indexes on and a
ScenarioSpec names, the payload digest, the entry map and required plugins. Its
`required_images` (`packs`, `stacks`, `authoring`, `runtime_host`,
`ros2_bridge`) restate the channel's image pins because the installer has to
run `mns-packs` before anything has resolved an image set;
`images/catalog.yaml` `consumers.pack_locks` declares which catalog rows those
mirror and `tools/images.sh verify` fails when they drift.

## Why the contract is checked in

`mns-stacks generate` resolves a ScenarioSpec's `environment` (id, version,
artifact_digest) from the pack store, selects the variant cooked for the runtime
host's capability id, and validates it against the host contract document. It
reads that document from `MNS_RUNTIME_HOST_COMPATIBILITY_CONTRACT` (the headless
targets and the dashboard both pass this file), so without it every generate
stops with `host compatibility document does not exist`. The runtime host image
is the authority; this file is a copy so the contract can be handed to a
container that never sees that image. The same contract also travels in the
host's kit image (`MNS_RUNTIME_HOST_KIT_IMAGE`, `/kit`), which
`tools/images.sh verify` checks the host pin against.

`tools/install-demo-packs.sh` refuses a lock whose `capability_id` differs from
the selected contract's `id`.

## Installing

`./download-packs.sh` is the entry point (`--list` shows every pack, its size,
whether it is installed, and whether ScenarioLab can open it). With no options
it installs the starter set (Warehouse and the vehicle models). It installs
into the channel's pack store with `mns-packs install` and stages what
ScenarioLab can open. `make dashboard` downloads only
the lock's version of packs you already have (and the vehicle models), or
`MNS_DEMO_PACKS` when set; it stages what the store holds and warns when that
is nothing. The dashboard, `make fly` and `mns-stacks` all read that one
store, and the generated stack's TEVVRuntimeHost loads the same immutable
artifact ScenarioLab authored against.

```bash
./download-packs.sh --warehouse --office      # a subset (vehicle models always added)
./download-packs.sh --all                     # everything, ~7.7 GB
make dashboard MNS_DEMO_PACKS=--all           # install missing packs, then start
```

The Author tab's preflight reports `pack_store` (what is installed) and
`packs_staged` (whether ScenarioLab can see it).

The installer underneath, for scripting:

```bash
tools/install-demo-packs.sh --all             # the lock's eleven packs
tools/install-demo-packs.sh --objects         # every object pack in the lock
tools/install-demo-packs.sh --missing --all   # only what the store lacks
tools/install-demo-packs.sh --missing --sync  # the lock's version of what is installed (what make dashboard runs)
tools/install-demo-packs.sh --check --all     # no network, no docker: list installed/missing, exit 1 if any is missing
```

It downloads the assets declared in the lock (`--lock` or `MNS_DEMO_PACK_LOCK`;
default `packs/v1.0.0.lock.json`), verifies their full SHA-256 checksums,
installs them into the content-addressed pack store (`mns-packs install
--json`, run as you with no network and every path at its host path), and
refreshes ScenarioLab's resolved pack index. A dropped connection resumes. It refuses to
start a download that cannot fit (archive plus store copy);
`MNS_DEMO_PACK_DOWNLOAD_DIR` moves the staging area. Archives already under
`.mns/downloads/pack-cache/` with the right checksum are reused.

## Building or refreshing a lock

A lock is a snapshot of what packaging had published when it was built. New
pack releases on TEVV-Airsim do not appear anywhere (not in `make dashboard`,
not in the dashboard's Content phase) until the lock is rebuilt:

```bash
make pack-lock            # every pack-* release cooked for the channel's host id, newest per pack
git diff packs/           # review, commit
make dashboard            # installs what is new
```

`make pack-lock` runs `tools/build_pack_lock.py --discover`; pass
`--release-tag pack-level-condo-level-1.0.0 ...` instead to pin an explicit set.
Each release's `artifact.json`, manifest and `.sha256` sidecar supply the
receipts; the bundle is fetched into `.mns/downloads/pack-cache/` (reused by the
installer) and its `artifact_digest` comes from the channel's `mns-packs`
(`mns-packs verify --host <id> --json`), so a bundle it rejects never enters a
lock.
`gh` must be logged in with access to the release repository. A release without
a variant for the channel's host id is skipped and named on stderr.

## Refreshing a contract after a runtime-host bump

```bash
. images/v1.0.0.generated.env
docker run --rm --entrypoint cat "$MNS_RUNTIME_HOST_IMAGE" \
  /app/TEVVRuntimeHost/TEVVRuntimeHost/Content/TEVVHost/host-compatibility.json \
  > packs/runtime-host-compatibility.v1.json
```

A pack version is immutable: packs built for a new host id need new versions
and a new lock (MnS-Integration-Platform
`docs/scenario-platform/level-pack-v2-workflow.md`). A new engine build is a new
channel: add it to the catalog, the Makefile and `channels.json`.
