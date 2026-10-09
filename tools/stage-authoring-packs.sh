#!/usr/bin/env bash
# Refresh ScenarioLab's resolved view of the packs installed in the local
# content-addressed store: `mns-packs stage-authoring --lock <the channel's
# lock>` (TEVV-Content-Pack-SDK), which stages exactly the version the lock
# names for each pack, so a store holding two versions of one pack never
# stages both (ScenarioLab refuses a pack set with a duplicated id).
#
# This sits on `make dashboard`'s dependency chain under `set -euo pipefail`,
# so every early exit below is a deliberate exit 0: a fresh clone with no packs
# installed must not abort the dashboard. Set MNS_SKIP_PACK_STAGING=1 to skip
# it outright.
#
# Roots come from the environment, the same variables the Makefile hands the
# dashboard backend, so one channel's packs are staged into that channel's
# authoring data root:
#   MNS_PACK_STORE_ROOT       (default .mns/v1/pack-store)
#   MNS_AUTHORING_DATA_ROOT   (default .mns/v1/authoring-data)
#   MNS_PACKS_IMAGE           the mns-packs image (required once packs exist;
#                             ./.env overrides it, with a note)
#   MNS_IMAGE_PULL_POLICY     always|missing|never for that image (default missing)
#   MNS_AUTHORING_HOST_CONTRACT  ScenarioLab's host contract (default
#                             packs/authoring-host-compatibility.v1.json)
#   MNS_DEMO_PACK_LOCK        the lock whose versions are staged (default
#                             packs/v1.0.0.lock.json)
# Every path is mounted into mns-packs at its identical host path, so the
# roots may live anywhere, and the same command works from inside the
# dashboard backend (which mounts this checkout at its host path).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STORE_ROOT="${MNS_PACK_STORE_ROOT:-$ROOT/.mns/v1/pack-store}"
DATA_ROOT="${MNS_AUTHORING_DATA_ROOT:-$ROOT/.mns/v1/authoring-data}"
STORE_ROOT="$(mkdir -p "$STORE_ROOT" && cd "$STORE_ROOT" && pwd)"
DATA_ROOT="$(mkdir -p "$DATA_ROOT" && cd "$DATA_ROOT" && pwd)"
STORE_INDEX="$STORE_ROOT/index.json"
STAGED_DIR="$DATA_ROOT/ResolvedPacks"
STAGED_INDEX="$STAGED_DIR/index.json"

# The containers below run as the HOST user (MNS_HOST_UID/GID when set, else
# the caller). When the caller is root -- the dashboard backend runs this
# script inside its container -- every directory this script mkdirs would be
# root-owned and the host-uid container could not write into it ("cp: cannot
# create directory '/out/...': Permission denied"), nor could the operator's
# next `make dashboard`. Hand them to the host user right after creating them.
HOST_UID="${MNS_HOST_UID:-$(id -u)}"
HOST_GID="${MNS_HOST_GID:-$(id -g)}"
own_by_host() {
  [[ "$(id -u)" == 0 ]] || return 0
  chown "$HOST_UID:$HOST_GID" "$@" 2>/dev/null || true
}
own_by_host "$STORE_ROOT" "$DATA_ROOT"
# Records WHICH mns-packs image and lock produced the staged index. Without
# it, switching IMAGE_MODE between development and production (or moving the
# lock) left the previous staged index in place and looking current.
STAGED_STAMP="$STAGED_DIR/.staged-with"

if [[ "${MNS_SKIP_PACK_STAGING:-0}" == "1" ]]; then
  echo "MNS_SKIP_PACK_STAGING=1: leaving $STAGED_INDEX as it is."
  exit 0
fi

LOCK="${MNS_DEMO_PACK_LOCK:-$ROOT/packs/v1.0.0.lock.json}"
LOCK="$(cd "$(dirname "$LOCK")" && pwd)/$(basename "$LOCK")"
AUTHORING_CONTRACT="${MNS_AUTHORING_HOST_CONTRACT:-$ROOT/packs/authoring-host-compatibility.v1.json}"
AUTHORING_CONTRACT="$(cd "$(dirname "$AUTHORING_CONTRACT")" && pwd)/$(basename "$AUTHORING_CONTRACT")"

PACK_LIBRARY="$DATA_ROOT/PackLibrary"

# A PackLibrary asset pack is placeable as soon as it has a manifest, but
# ScenarioLab takes its artifact digest ONLY from ResolvedPacks/index.json
# (MnSAuthoringSessionSubsystem ReloadPackLibrary merges the two by pack id).
# A pack present here but absent there therefore lets an operator build a whole
# scene and then refuses the export with "standalone v2 export requires
# immutable asset pack id/version/artifact_digest". Say so now rather than an
# hour of authoring later. Not fatal: an asset pack can still be placed while
# the operator fixes the store.
report_unexportable_library_packs() {
  [[ -d "$PACK_LIBRARY/asset_packs" && -f "$STAGED_INDEX" ]] || return 0
  python3 - "$PACK_LIBRARY/asset_packs" "$STAGED_INDEX" <<'PY'
import json, sys
from pathlib import Path

library, staged_index = Path(sys.argv[1]), Path(sys.argv[2])
try:
    staged = json.loads(staged_index.read_text(encoding="utf-8"))
except (OSError, ValueError):
    sys.exit(0)
digested = {str(entry.get("id")) for entry in (staged.get("asset_packs") or [])
            if isinstance(entry, dict) and str(entry.get("artifact_digest", "")).startswith("sha256:")}

unexportable = []
for manifest in sorted(library.glob("*.mnsassetpack/mns_asset_pack.json")):
    try:
        document = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        continue
    pack = document.get("asset_pack") or document
    pack_id = str(pack.get("pack_id") or pack.get("id") or "")
    if pack_id and pack_id not in digested:
        target = (pack.get("runtime") or {}).get("runtime_target_id") or "unknown target"
        unexportable.append((pack_id, str(pack.get("version") or "?"), target))

if unexportable:
    print(f"WARNING: {len(unexportable)} asset pack(s) in {library} have no entry in",
          file=sys.stderr)
    print(f"         {staged_index}, so ScenarioLab can place them but CANNOT export a",
          file=sys.stderr)
    print("         scenario that uses them (no immutable artifact digest):", file=sys.stderr)
    for pack_id, version, target in unexportable:
        print(f"           {pack_id}@{version} (cooked for {target})", file=sys.stderr)
    print("         Install a published build of each into the pack store to fix it:",
          file=sys.stderr)
    print("         tools/install-demo-packs.sh --<selection>", file=sys.stderr)
PY
}

# No store index means no pack has ever been installed, so there is nothing to
# stage. Running `packs stage-authoring` against an empty store here used to
# run on EVERY invocation (the old guard required the staged index to exist,
# which it never did) and aborted `make dashboard` outright if it failed.
if [[ ! -f "$STORE_INDEX" ]]; then
  echo "No packs installed yet ($STORE_INDEX absent); nothing to stage."
  echo "Install some with: ./download-packs.sh   (./download-packs.sh --list to choose)"
  exit 0
fi

# The lock decides which version of a pack ScenarioLab sees: this step for its
# PackLibrary, `mns-packs stage-authoring --lock` below for ResolvedPacks.
# ScenarioLab places vehicles from its PackLibrary copy, not from
# ResolvedPacks, and the authoring image's entrypoint fills that copy from the
# vehicle pack it bakes with `cp -n`, which never replaces an existing one.
# So set each PackLibrary asset pack to the lock's pin from the store: replace
# one at another digest, and create the required packs (REQUIRED_PACK_IDS in
# install_demo_packs.py) when missing, before the entrypoint copies the baked
# one in. A pin whose blob is not installed yet leaves the library as it is.
sync_library_packs_to_lock() {
  local lock="$LOCK"
  [[ -f "$lock" ]] || return 0
  python3 - "$lock" "$PACK_LIBRARY/asset_packs" "$STORE_ROOT/blobs/sha256" "$ROOT/tools" <<'PY'
import json, shutil, sys
from pathlib import Path

lock, library, blobs = json.load(open(sys.argv[1])), Path(sys.argv[2]), Path(sys.argv[3])
try:
    sys.path.insert(0, sys.argv[4])
    from install_demo_packs import REQUIRED_PACK_IDS
except ImportError:
    REQUIRED_PACK_IDS = ("mns_vehicle_models",)
for pack in lock.get("packs", []):
    if pack.get("kind") != "asset":
        continue
    target = library / f"{pack['id']}.mnsassetpack"
    digest_file = target.with_name(target.name + ".digest")
    pinned = str(pack.get("artifact_digest", ""))
    blob = blobs / pinned.removeprefix("sha256:")
    if not pinned.startswith("sha256:") or not blob.is_dir():
        continue
    if not target.is_dir() and pack["id"] not in REQUIRED_PACK_IDS:
        continue
    current = digest_file.read_text().strip() if digest_file.is_file() else ""
    if target.is_dir() and current == pinned:
        continue
    library.mkdir(parents=True, exist_ok=True)
    staging = target.with_name(target.name + ".new")
    shutil.rmtree(staging, ignore_errors=True)
    shutil.copytree(blob, staging, symlinks=True)
    shutil.rmtree(target, ignore_errors=True)
    staging.rename(target)
    digest_file.write_text(pinned)
    print(f"PackLibrary {pack['id']}: {current or 'none'} -> {pack['version']} ({pinned})")
PY
  [[ "$(id -u)" == 0 && -d "$PACK_LIBRARY" ]] && find "$PACK_LIBRARY" -user 0 -exec chown "$HOST_UID:$HOST_GID" {} + 2>/dev/null
  return 0
}
sync_library_packs_to_lock

# shellcheck source=tools/load-images-env.sh
. "$ROOT/tools/load-images-env.sh"
IMAGE="${MNS_PACKS_IMAGE:-}"
if [[ -z "$IMAGE" ]]; then
  IMAGE="$(dotenv_value MNS_PACKS_IMAGE "$ROOT/.env")"
  [[ -n "$IMAGE" ]] && echo "NOTE: MNS_PACKS_IMAGE from ./.env overrides the catalog pin: $IMAGE" >&2
fi
if [[ -z "$IMAGE" ]]; then
  echo "ERROR: MNS_PACKS_IMAGE is not set. Run this through make (make stage-authoring-packs)," >&2
  echo "       or: set -a; . images/v1.0.0.generated.env; set +a" >&2
  exit 2
fi
PULL_POLICY="${MNS_IMAGE_PULL_POLICY:-missing}"
case "$PULL_POLICY" in
  always|missing|never) ;;
  *)
    echo "MNS_IMAGE_PULL_POLICY must be always, missing, or never; got: $PULL_POLICY" >&2
    exit 2
    ;;
esac
# The image, the lock and ScenarioLab's contract all decide what is staged,
# so a change to any of them restages.
STAMP="$IMAGE $(sha256sum "$LOCK" 2>/dev/null | cut -d' ' -f1) $(sha256sum "$AUTHORING_CONTRACT" 2>/dev/null | cut -d' ' -f1)"

if [[ -f "$STAGED_INDEX" && ! "$STORE_INDEX" -nt "$STAGED_INDEX" \
      && -f "$STAGED_STAMP" && "$(cat "$STAGED_STAMP")" == "$STAMP" ]]; then
  echo "ScenarioLab pack index is current: $STAGED_INDEX"
  report_unexportable_library_packs
  exit 0
fi

# As the host user, so the staged tree stays the operator's. Everything is
# mounted at its identical path; the index records paths relative to the data
# root, so it reads the same inside ScenarioLab's container. The store and the
# data root go in as ONE mount (their common parent, .mns/<channel>/ by
# default): staging hard-links payloads out of the store, and link(2) refuses
# to cross two bind mounts even on one filesystem (it would copy instead).
# Never a broad parent, though: $HOME, /home, / or anything shallower than
# three components would hand the container far more than the two roots, so
# those get two mounts and staging copies instead of linking.
common="$(python3 -c 'import os,sys; print(os.path.commonpath(sys.argv[1:]))' "$STORE_ROOT" "$DATA_ROOT")"
depth="$(python3 -c 'import sys; print(len([p for p in sys.argv[1].split("/") if p]))' "$common")"
home="$(cd "$HOME" 2>/dev/null && pwd || echo "$HOME")"
if [[ "$common" == / || "$common" == /home || "$common" == "$home" || "$depth" -lt 3 ]]; then
  echo "NOTE: the pack store and ScenarioLab's data root share only $common; mounting them" >&2
  echo "      separately, so staging copies payloads instead of hard-linking them." >&2
  mounts=(-v "$STORE_ROOT:$STORE_ROOT" -v "$DATA_ROOT:$DATA_ROOT")
else
  mounts=(-v "$common:$common")
fi
mounts+=(-v "$LOCK:$LOCK:ro" -v "$AUTHORING_CONTRACT:$AUTHORING_CONTRACT:ro")
status=0
out="$(docker run --pull "$PULL_POLICY" --rm --network=none \
      --user "$HOST_UID:$HOST_GID" -e HOME=/tmp "${mounts[@]}" \
      "$IMAGE" stage-authoring --store "$STORE_ROOT" --authoring-data "$DATA_ROOT" \
      --host "$AUTHORING_CONTRACT" --lock "$LOCK" --json)" || status=$?
python3 - "$out" "$status" <<'PY' || true
import json, sys
text, status = sys.argv[1], int(sys.argv[2])
try:
    envelope = json.loads(text)
except ValueError:
    sys.exit(0)  # no envelope: docker's own error is already on stderr
result = envelope.get("result") or {}
if status:
    error = envelope.get("error") or {}
    print(f"mns-packs stage-authoring: {error.get('message') or 'failed'}", file=sys.stderr)
    for pack in result.get("blocked") or []:
        print(f"  blocked: {pack.get('kind')}/{pack.get('id')}@{pack.get('version')}: "
              f"{pack.get('reason')}", file=sys.stderr)
    sys.exit(0)
staged = result.get("staged") or []
print(f"Staged {len(staged)} pack(s) for ScenarioLab: "
      + ", ".join(f"{pack['id']}@{pack['version']}" for pack in staged))
for pack in result.get("skipped") or []:
    print(f"  not staged: {pack.get('kind')}/{pack.get('id')}@{pack.get('version')}: "
          f"{pack.get('reason')}")
PY
if [[ "$status" != 0 ]]; then
  echo "ERROR: staging failed (above). A blocked pack is one whose installed bytes do not match" >&2
  echo "       the lock: reinstall it (tools/install-demo-packs.sh --<selection>). To start" >&2
  echo "       without staging: MNS_SKIP_PACK_STAGING=1 make dashboard" >&2
  exit 1
fi

mkdir -p "$STAGED_DIR"
printf '%s\n' "$STAMP" >"$STAGED_STAMP"
own_by_host "$STAGED_DIR" "$STAGED_STAMP"
report_unexportable_library_packs
