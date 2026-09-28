#!/usr/bin/env bash
# Refresh ScenarioLab's resolved view of the packs installed in the local
# content-addressed store by running the product shell's `packs
# stage-authoring`.
#
# This sits on `make dashboard`'s dependency chain under `set -euo pipefail`,
# so every early exit below is a deliberate exit 0: a fresh clone with no packs
# installed must not abort the dashboard. Set MNS_SKIP_PACK_STAGING=1 to skip
# it outright.
#
# Roots come from the environment, the same variables the Makefile hands the
# dashboard backend and product.sh, so one channel's packs are staged into that
# channel's authoring data root:
#   MNS_PACK_STORE_ROOT       (default .mns/v1/pack-store)
#   MNS_AUTHORING_DATA_ROOT   (default .mns/v1/authoring-data)
#   MNS_PRODUCT_SHELL_IMAGE   the shell whose SDK stages (required once packs exist)
#   MNS_IMAGE_PULL_POLICY     always|missing|never for that shell (default missing)
#   MNS_AUTHORING_HOST_CONTRACT  ScenarioLab's host contract (host path under the
#                             checkout); staging validates packs against it instead
#                             of the shell's baked copy. Unset = baked copy.
# Both roots must be inside this checkout: the shell mounts the checkout at
# /workspace and cannot see anything else.
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
# Records WHICH product-shell image produced the staged index. Without it,
# switching IMAGE_MODE between development and production left the previous
# image's staged index in place and looking current.
STAGED_STAMP="$STAGED_DIR/.staged-with"

if [[ "${MNS_SKIP_PACK_STAGING:-0}" == "1" ]]; then
  echo "MNS_SKIP_PACK_STAGING=1: leaving $STAGED_INDEX as it is."
  exit 0
fi

container_path() {
  # The path a checkout-relative directory has inside the product shell.
  case "$1" in
    "$ROOT"/*) printf '/workspace/%s' "${1#"$ROOT"/}" ;;
    *) echo "ERROR: $1 is outside the checkout $ROOT; the product shell cannot see it" >&2; exit 2 ;;
  esac
}
CONTAINER_STORE="$(container_path "$STORE_ROOT")"
CONTAINER_DATA="$(container_path "$DATA_ROOT")"

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

# The lock decides which version of a pack ScenarioLab sees; this step and
# drop_superseded_staged_packs below make it so. ScenarioLab places vehicles
# from its PackLibrary copy, not from ResolvedPacks, and the authoring image's
# entrypoint fills that copy from the pack it bakes (mns_vehicle_models 1.0.3,
# the untextured quadrotor) with `cp -n`, which never replaces an existing one.
# So set each PackLibrary asset pack to the lock's pin from the store: replace
# one at another digest, and create the required packs (REQUIRED_PACK_IDS in
# install_demo_packs.py) when missing, before the entrypoint copies the baked
# one in. A pin whose blob is not installed yet leaves the library as it is.
sync_library_packs_to_lock() {
  local lock="${MNS_DEMO_PACK_LOCK:-}"
  [[ -n "$lock" && -f "$lock" ]] || return 0
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

IMAGE="${MNS_PRODUCT_SHELL_IMAGE:?MNS_PRODUCT_SHELL_IMAGE is required}"
PULL_POLICY="${MNS_IMAGE_PULL_POLICY:-missing}"
case "$PULL_POLICY" in
  always|missing|never) ;;
  *)
    echo "MNS_IMAGE_PULL_POLICY must be always, missing, or never; got: $PULL_POLICY" >&2
    exit 2
    ;;
esac

# `packs stage-authoring` stages every pack in the store, so after a lock
# moves a pack to a new version the store still holds the old one and both are
# staged under one id. ScenarioLab then refuses to mount any level: "asset pack
# definition identity is invalid or duplicated: mns_vehicle_models". Keep only
# the lock's pin for each id it pins (when that pin was staged), in the index
# and in every staged environment's resolved-pack-set.json. The store keeps the
# old blob: generated stacks that reference it by digest still resolve.
drop_superseded_staged_packs() {
  local lock="${MNS_DEMO_PACK_LOCK:-}"
  [[ -n "$lock" && -f "$lock" && -f "$STAGED_INDEX" ]] || return 0
  python3 - "$lock" "$STAGED_INDEX" "$DATA_ROOT" <<'PY'
import json, sys
from pathlib import Path

lock, index_path, data_root = json.load(open(sys.argv[1])), Path(sys.argv[2]), Path(sys.argv[3])
pins = {p["id"]: p["artifact_digest"] for p in lock.get("packs", []) if p.get("artifact_digest")}

def keep(entries, where):
    staged = {(e.get("id"), e.get("artifact_digest")) for e in entries}
    kept = []
    for entry in entries:
        pack_id, digest = entry.get("id"), entry.get("artifact_digest")
        if pack_id in pins and digest != pins[pack_id] and (pack_id, pins[pack_id]) in staged:
            print(f"Not staging {pack_id}@{entry.get('version', '?')} ({digest}) in {where}: "
                  f"the lock pins {pins[pack_id]}")
            continue
        kept.append(entry)
    return kept

index = json.loads(index_path.read_text(encoding="utf-8"))
changed = False
for key in ("asset_packs", "level_packs"):
    entries = index.get(key) or []
    kept = keep(entries, index_path.name)
    changed |= len(kept) != len(entries)
    index[key] = kept
if changed:
    index_path.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")

for level in index.get("level_packs") or []:
    relative = level.get("resolved_pack_set")
    pack_set_path = data_root / str(relative or "")
    if not relative or not pack_set_path.is_file():
        continue
    pack_set = json.loads(pack_set_path.read_text(encoding="utf-8"))
    entries = pack_set.get("asset_packs") or []
    kept = keep(entries, f"{level.get('id')}/{pack_set_path.name}")
    if len(kept) != len(entries):
        pack_set["asset_packs"] = kept
        pack_set_path.write_text(json.dumps(pack_set, indent=2) + "\n", encoding="utf-8")
PY
}

if [[ -f "$STAGED_INDEX" && ! "$STORE_INDEX" -nt "$STAGED_INDEX" \
      && -f "$STAGED_STAMP" && "$(cat "$STAGED_STAMP")" == "$IMAGE" ]]; then
  drop_superseded_staged_packs
  echo "ScenarioLab pack index is current: $STAGED_INDEX"
  report_unexportable_library_packs
  exit 0
fi

# MNS_HOST_UID/GID: the dashboard backend runs this as root inside its
# container; the staged files must stay owned by the operator on the host.
contract_args=()
if [[ -n "${MNS_AUTHORING_HOST_CONTRACT:-}" ]]; then
  contract_args=(-e "MNS_AUTHORING_HOST_CONTRACT=$(container_path "$MNS_AUTHORING_HOST_CONTRACT")")
fi
docker run --pull "$PULL_POLICY" --rm \
  --user "$HOST_UID:$HOST_GID" \
  -e HOME=/tmp \
  -e MNS_WORKSPACE_ROOT=/workspace \
  -e "MNS_PACK_STORE_ROOT=$CONTAINER_STORE" \
  -e "MNS_AUTHORING_DATA_ROOT=$CONTAINER_DATA" \
  "${contract_args[@]}" \
  -v "$ROOT:/workspace:rw" \
  "$IMAGE" packs stage-authoring

drop_superseded_staged_packs
mkdir -p "$STAGED_DIR"
printf '%s\n' "$IMAGE" >"$STAGED_STAMP"
own_by_host "$STAGED_DIR" "$STAGED_STAMP"
report_unexportable_library_packs
