# Offline setup

How to take this repository onto a machine with no network: build a bundle on a
connected host, carry it over, and bring the dashboard up on the target.

Proven end to end on 2026-09-18: Ubuntu 24.04.5, Docker CE 29.8.1 with the
containerd image store, RTX 5080 — `make dashboard` up with no registry access.

Three scripts do the work; everything below is the manual procedure around them.

| script | runs on | what it does |
| --- | --- | --- |
| `tools/offline-export.sh` | online host | pulls every catalog image by digest, saves them, fetches and checksums the content packs, downloads python wheels |
| `tools/offline-verify.sh` | either | checks a bundle without loading anything — catalog coverage, tar contents, pack checksums |
| `tools/offline-import.sh` | offline host | loads and tags the images, writes `./.env`, stages the pack cache, installs the python deps |

## Why the bundle is not just `docker save`

Every image reference in this repository is `repo:tag@sha256:...` and the digest
is the contract (see [ADR 0002](adr/0002-one-image-catalog.md)). Two properties of
Docker make a naive save/load bundle wrong, both verified on a real host:

1. **`docker load` does not restore RepoDigests.** A loaded image carries its
   tag and nothing else, so `docker image inspect repo:tag@sha256:...` fails and
   every compose service tries to pull. `offline-import.sh` therefore writes the
   same references into `./.env` with the digest stripped. Integrity is
   established on the online host, which pulled each image *by digest*;
   `./.env` outranks the generated env files (`tools/load-images-env.sh`:
   shell > `./.env` > generated), so the offline host resolves them by tag.

2. **`docker pull repo:tag@sha256:...` creates no local tag.** The image is
   stored with only a RepoDigest, so `docker save repo:tag` fails with
   `reference does not exist`. Worse, where a pinned reference and a development
   reference share one tag string (the mutable `-latest` dashboard images), an
   unpinned pull moves that tag onto a newer build — saving by tag would ship an
   image the catalog never approved.

The bundle therefore stores images **by image ID**, one tar per distinct image,
with a `manifest.tsv` of `tar → id → tag → original ref`. Where two references
contend for one tag, the digest-pinned one wins.

3. **An image id is not portable between daemons.** The classic overlay2 store
   reports an image's *config* digest as its id; the containerd image store
   reports its *manifest* digest. The bytes are identical, but a manifest built
   on an overlay2 host records ids that `docker image inspect` cannot find on a
   containerd-store host (`docker info` → `Driver: overlayfs`). The import
   therefore tags whatever `docker load` **reports** it loaded, and verifies by
   tag, never by the recorded id.

## Prerequisites

**Online host**

- `docker login` for the private `dhdevspace/auto_mns` images
- `GH_TOKEN` (or `GITHUB_TOKEN`, or `gh auth login`) with read access to
  `DinoHub/TEVV-Airsim` — the pack releases are private and answer 404, not 401,
  to an anonymous client
- `python3` with PyYAML, and `pip`
- ~35 GB free

**Offline host**

- Docker Engine with Compose
- NVIDIA Container Toolkit — the Unreal images will not start without it, and it
  is **not** part of the bundle
- X11, for ScenarioLab and QGroundControl
- ~85 GB free: ~30 GB for the copied bundle, ~25 GB of Docker images once
  loaded, and ~28 GB under `.mns/`, before generated stacks and `~/tevv-runs`.
  Around 30 GB of that is reclaimable once the dashboard is up (see step 3), and
  importing straight off the drive instead needs only ~55 GB

## 1. Build the bundle (online host)

```bash
cd /path/to/M-S-Simulation-Runtime-Stack
export GH_TOKEN=$(gh auth token)          # or set it directly

tools/offline-export.sh ~/ECW/deployment/mns-offline-bundle
```

Roughly 30 GB and a long download. Options:

```bash
tools/offline-export.sh ~/ECW/deployment/mns-offline-bundle --all-catalog   # + monitoring, metrics, logs, legacy sims
MNS_DEMO_PACKS="--safti --office-props" tools/offline-export.sh ~/ECW/deployment/mns-offline-bundle
MNS_TARGET_PY=3.12 tools/offline-export.sh ~/ECW/deployment/mns-offline-bundle
```

`MNS_TARGET_PY` defaults to `3.12`, the Ubuntu 24.04 LTS interpreter. It matters:
`pip download` otherwise resolves wheels for whatever Python the *online* host
runs, and a 3.13/3.14 host would produce `cp313`/`cp314` binary wheels (pyyaml,
markupsafe) that cannot install on the target.

The default set is the 15 references the product path needs. `--all-catalog`
adds 42 more for the monitoring, metrics, logs and legacy `compose/<scenario>/`
stacks.

Bundle layout:

```
mns-offline-bundle/
  images/           one tar per distinct image, plus manifest.tsv
  pack-cache/       the content pack archives named by the channel lock
  wheels/           python deps for the target interpreter
```

## 2. Transfer

An exFAT drive is the safe default: no 4 GiB file-size limit, readable
everywhere. The drive mirrors the `ECW/deployment/` staging layout used on both
hosts, so the MnS bundle sits beside the OS-layer `offline-pc/` kit rather than
in a folder of its own:

```text
<drive>/ECW/deployment/
  offline-pc/                    apt, firmware and NVIDIA bundles for noble
  mns-offline-bundle/            this repository's images, packs and wheels
  M-S-Simulation-Runtime-Stack/  the checkout itself
```

Do not copy `.mns/` (regenerated on the target from `pack-cache/`) or `.venv/`
(host-specific, and exFAT cannot store its symlinks).

```bash
DEST=/run/media/$USER/CBD4-3014/ECW/deployment      # confirm the label: lsblk -f
mkdir -p "$DEST"

RS="rsync -rt --modify-window=1 --info=progress2 --no-perms --no-owner --no-group"

$RS ~/ECW/deployment/mns-offline-bundle "$DEST"/
$RS --exclude='.mns' --exclude='.venv' --exclude='.env' --exclude='.env.bak.*' \
    /path/to/M-S-Simulation-Runtime-Stack "$DEST"/

sync    # exFAT buffers aggressively; do not unplug before this returns
```

The three rsync flags are for exFAT specifically: `--modify-window=1` for its
two-second timestamp granularity — without it, a repeated or resumed copy
re-transfers all 30 GB — and `--no-perms --no-owner --no-group` because exFAT
stores neither, so `-a` errors on every file.

`cp -r` works too. rsync earns its place here for one reason: an interrupted
`cp` leaves a truncated file that *looks* complete, and a later `cp -rn` skips
it, producing a bundle that fails midway through `docker load`. rsync writes to
a temp name and renames on completion. Either way, step 3's verify is what
actually guarantees integrity.

## 3. Import (offline host)

```bash
USB=/run/media/$USER/CBD4-3014/ECW/deployment      # confirm with: lsblk -f

mkdir -p ~/ECW/deployment
cp -r "$USB/mns-offline-bundle" ~/ECW/deployment/          # 30 GB
cp -r "$USB/M-S-Simulation-Runtime-Stack" ~/               # 56 MB
sync

cd ~/M-S-Simulation-Runtime-Stack
chmod +x tools/*.sh launch.sh product.sh setup.sh stop.sh logs.sh tools.sh

tools/offline-verify.sh ~/ECW/deployment/mns-offline-bundle    # verify the LOCAL copy
tools/offline-import.sh ~/ECW/deployment/mns-offline-bundle
```

Everything lands on local disk before anything is deployed: the drive is a
courier, not a runtime dependency. Once the copy is verified you can unplug it,
and a failure midway through the import is never confused with a flaky USB
connection.

Budget **~85 GB** on the target for this: ~30 GB bundle, ~25 GB of Docker images
once loaded, and ~28 GB under `.mns/`. Once `make dashboard` has come up
successfully you can delete `~/ECW/deployment/mns-offline-bundle` and recover its
30 GB — the images are in Docker's store and the packs are installed in
`.mns/ue582/pack-store` by then.

If the target is too tight for that, the scripts take any path, so you can point
them straight at the drive instead and keep it plugged in for the whole import:

```bash
tools/offline-verify.sh "$USB/mns-offline-bundle"
tools/offline-import.sh "$USB/mns-offline-bundle"
```

That needs only ~55 GB, at the cost of a slower `docker load` and a drive you
must not disturb. Either way the import copies the 7.2 GB pack cache into
`.mns/downloads/pack-cache` unless you set `MNS_DEMO_PACK_CACHE_DIR`.

`cp`, not rsync: rsync is priority `optional` on Ubuntu, so a minimal install may
not have it and you cannot `apt-get` it on an air-gapped box. `chmod +x` is not
optional either — exFAT stores no executable bit, so whether the scripts arrive
runnable depends on the target's mount options.

Run the verify **before** the import. It re-hashes every pack and checks every
tar's config blob, so a bad transfer surfaces in minutes instead of halfway
through a 30 GB load.

`offline-import.sh` is idempotent: re-running it rewrites its own delimited block
in `./.env` rather than appending a second copy. Anything you hand-added to
`.env` outside that block is preserved, and the file is backed up first.

Useful knobs:

```bash
MNS_SKIP_WHEELS=1 tools/offline-import.sh "$USB/mns-offline-bundle"
MNS_DEMO_PACK_CACHE_DIR="$USB/mns-offline-bundle/pack-cache" tools/offline-import.sh ...
```

The second leaves the pack archives on the drive instead of copying 7.2 GB into
`.mns/downloads/pack-cache` — slower install, 7 GB saved.

## 4. Verify and run

**Source `./.env` first, in every shell that runs `make`:**

```bash
set -a; . ./.env; set +a
```

`./.env` carries the tag-only image references, and docker compose reads it
automatically — but `tools/load-images-env.sh` deliberately skips exporting any
key `./.env` already defines. Python helpers such as `install_demo_packs.py`
read the environment, find nothing, and fall back to the lock's digest-pinned
reference, which after `docker load` only resolves against a registry:

```text
failed to resolve reference "docker.io/dhdevspace/auto_mns@sha256:..."
  ... dial tcp: lookup registry-1.docker.io ... connection refused
```

Sourcing puts the tag-only refs in the environment, where they outrank
everything else.

All of these work with no network:

```bash
tools/ensure-images.sh --development --channel standalone_v2_ue582 --dry-run   # expect: 0 would pull
tools/install-demo-packs.sh --check --all                                      # expect: all installed
tools/images.sh verify                                                         # catalog vs generated files

docker run --rm --gpus all --entrypoint nvidia-smi \
  dhdevspace/auto_mns:tevv-runtime-host-20260910.1                             # NVIDIA runtime

make dashboard          # http://127.0.0.1:3001
```

The NVIDIA check uses an image the import just loaded, because you cannot pull
`nvidia/cuda` to test with — the toolkit injects `nvidia-smi` into any container.

`tools/images.sh status --offline` also runs without the network, but it exits 1
on the catalog's pre-existing follow-up notes, so it is not a pass/fail signal
here. Use `tools/images.sh verify` for that.

The first `make dashboard` installs the packs from the staged cache. That needs
no credentials and no network: the installer reuses any archive whose size and
SHA-256 match the lock.

## Troubleshooting

**`no matching distribution` during the python step.** The wheels are built for
`MNS_TARGET_PY` (default 3.12). Check the target's `python3 -V` and re-export
with a matching value.

**`pip: command not found`.** Not fatal. The only required dep is PyYAML, which
Ubuntu ships as `python3-yaml`; `jinja2` and `python-dotenv` are used solely by
`tools/generate_scenario.py` on the legacy `./launch.sh` path, not by
`make dashboard`. Install `python3-pip` from an apt bundle if you want them.

**`error: externally-managed-environment`.** Ubuntu 24.04 marks the system
interpreter as PEP 668. `offline-import.sh` retries with
`--break-system-packages` automatically; if you are installing by hand, add it.

**A compose service tries to pull.** Its image variable is not in `./.env`.
Images outside the exported set are written there commented out — re-export with
`--all-catalog` if you need the monitoring, metrics, logs or legacy stacks.

**`container ...-unreal-airsim is unhealthy`.** Usually X11, not the container.
`tools/check_docker.sh` warns about a broken cookie or unset `DISPLAY` before it
surfaces this way.

## Updating an offline machine

Re-run the export on the online host — it skips images already saved and packs
already cached — then rsync the bundle again and re-run the import. Both are
incremental, so a catalog bump costs only the images that actually changed.

Tars are named by image ID, so a bumped image is written as a *new* tar and the
superseded one is left behind. Nothing references it (the import reads
`manifest.tsv`, not the directory listing), but it still occupies the drive.
After a few bumps, prune what the manifest no longer mentions:

```bash
cd ~/ECW/deployment/mns-offline-bundle/images
comm -23 <(ls -1 *.tar | sort) <(cut -f1 manifest.tsv | sort -u) | xargs -r rm -v
```

Switching `CHANNEL` away from the default `ue582` means re-running the import so
`./.env` is regenerated for that channel's image set.
