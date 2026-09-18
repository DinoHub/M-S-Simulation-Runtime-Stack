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
#   MNS_PACK_STORE_ROOT       (default .mns/ue582/pack-store)
#   MNS_AUTHORING_DATA_ROOT   (default .mns/ue582/authoring-data)
#   MNS_PRODUCT_SHELL_IMAGE   the shell whose SDK stages (required once packs exist)
#   MNS_IMAGE_PULL_POLICY     always|missing|never for that shell (default missing)
#   MNS_AUTHORING_HOST_CONTRACT  ScenarioLab's host contract (host path under the
#                             checkout); staging validates packs against it instead
#                             of the shell's baked copy. Unset = baked copy.
#   MNS_SEED_AUTHORING_DEFAULTS  1 to seed the v1 default asset packs (5.5.4 channel)
# Both roots must be inside this checkout: the shell mounts the checkout at
# /workspace and cannot see anything else.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STORE_ROOT="${MNS_PACK_STORE_ROOT:-$ROOT/.mns/ue582/pack-store}"
DATA_ROOT="${MNS_AUTHORING_DATA_ROOT:-$ROOT/.mns/ue582/authoring-data}"
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

# ScenarioLab on the 5.5.4 channel cannot add a drone without the
# `mns_vehicle_models` asset pack (six authoring vehicle models; "required
# mns_vehicle_models pack is missing" on every export otherwise), and
# `scenario_runtime_basic` carries the blocking-box placeable. Neither is a
# release pack: the v1 authoring image shipped both under
# /opt/mns/default-packs and its entrypoint copied them into the PackLibrary
# on first start, but the standalone-v2 5.5.4 authoring image ships no default
# packs at all. Until it does (catalog follow_up), the pinned v1 authoring
# image in product-images.env is the one published source, so seed the
# PackLibrary from it once. -n: never overwrite a pack the operator replaced.
# The 5.8.2 authoring image ships none either, and the 5.5.4-cooked pak
# mounts there too, so both channels seed.
PACK_LIBRARY="$DATA_ROOT/PackLibrary"
seed_authoring_defaults() {
  if [[ "${MNS_SEED_AUTHORING_DEFAULTS:-0}" != "1" ]]; then
    return 0
  fi
  if [[ -f "$PACK_LIBRARY/asset_packs/mns_vehicle_models.mnsassetpack/mns_asset_pack.json" ]]; then
    return 0
  fi
  local v1_authoring
  v1_authoring="$(grep -E '^MNS_AUTHORING_IMAGE=' "$ROOT/product-images.env" | tail -1 | cut -d= -f2-)"
  if [[ -z "$v1_authoring" ]]; then
    echo "WARNING: product-images.env has no MNS_AUTHORING_IMAGE; cannot seed ScenarioLab's default asset packs." >&2
    return 0
  fi
  mkdir -p "$PACK_LIBRARY/asset_packs" "$PACK_LIBRARY/level_packs"
  own_by_host "$PACK_LIBRARY" "$PACK_LIBRARY/asset_packs" "$PACK_LIBRARY/level_packs"
  # Seeding is a convenience, so it must never be the thing that stops a
  # dashboard starting. This pull used to ignore MNS_IMAGE_PULL_POLICY and run
  # under `set -e`, so on a machine with no registry (or none of this image)
  # `make dashboard` died here -- even with MNS_SKIP_PACK_INSTALL=1, which
  # only skips the pack install, not this step.
  if ! docker image inspect "$v1_authoring" >/dev/null 2>&1; then
    if [[ "${MNS_IMAGE_PULL_POLICY:-missing}" == "never" ]]; then
      echo "WARNING: $v1_authoring is not present and MNS_IMAGE_PULL_POLICY=never;" >&2
      echo "         skipping the default asset pack seed. ScenarioLab will start without" >&2
      echo "         mns_vehicle_models, so a drone cannot be placed on this channel." >&2
      return 0
    fi
    echo "Pulling $v1_authoring for ScenarioLab's default asset packs (mns_vehicle_models)..."
    if ! docker pull "$v1_authoring"; then
      echo "WARNING: could not pull $v1_authoring (offline?); skipping the default asset" >&2
      echo "         pack seed. ScenarioLab will start without mns_vehicle_models, so a" >&2
      echo "         drone cannot be placed on this channel. Everything else still works." >&2
      return 0
    fi
  fi
  echo "Seeding ScenarioLab default asset packs into $PACK_LIBRARY/asset_packs from $v1_authoring"
  if ! docker run --rm --user "$HOST_UID:$HOST_GID" --entrypoint sh \
      -v "$PACK_LIBRARY/asset_packs:/out:rw" \
      "$v1_authoring" -c 'cp -a -n /opt/mns/default-packs/asset_packs/. /out/'; then
    echo "WARNING: seeding the default asset packs failed; continuing without them." >&2
  fi
}
seed_authoring_defaults

# A PackLibrary asset pack is placeable as soon as it has a manifest, but
# ScenarioLab takes its artifact digest ONLY from ResolvedPacks/index.json
# (MnSAuthoringSessionSubsystem ReloadPackLibrary merges the two by pack id).
# A pack present here but absent there therefore lets an operator build a whole
# scene and then refuses the export with "standalone v2 export requires
# immutable asset pack id/version/artifact_digest". Say so now rather than an
# hour of authoring later. Not fatal: the seeded vehicle-model pack is what
# lets a drone be placed at all, and it is the only published source of those
# models until an authoring image ships its own.
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
  echo "Install some with: tools/install-demo-packs.sh --all"
  echo "(make dashboard does this itself unless MNS_SKIP_PACK_INSTALL=1)"
  exit 0
fi

IMAGE="${MNS_PRODUCT_SHELL_IMAGE:?MNS_PRODUCT_SHELL_IMAGE is required}"
PULL_POLICY="${MNS_IMAGE_PULL_POLICY:-missing}"
case "$PULL_POLICY" in
  always|missing|never) ;;
  *)
    echo "MNS_IMAGE_PULL_POLICY must be always, missing, or never; got: $PULL_POLICY" >&2
    exit 2
    ;;
esac

# ScenarioLab accepts a staged level pack only when the three paths its index
# entry names all exist: resolved_pack_set, bundle_root and manifest
# (MnSAuthoringSessionSubsystem, "resolved pack index entry is incomplete or
# missing staged files"). The product shell pinned for the 5.5.4 channel
# (mns-product-shell-20260826.2) stages the payload and writes the `manifest`
# entry but never copies the level pack's mns_level_pack.json into the staged
# bundle -- TEVV-Authoring's stage_resolved_level_pack gained that copy after
# the image was cut -- so every level pack was rejected and the editor
# reported "No MnS level packs are installed" over a full store. Until a shell
# with the fix is pinned, put the manifest where the index says it is, copied
# from the immutable store blob the index entry's artifact_digest names. A
# newer shell already wrote the file, in which case this is a no-op.
backfill_level_manifests() {
  python3 - "$DATA_ROOT" "$STORE_ROOT" "$STAGED_INDEX" <<'PY'
import json, shutil, sys
from pathlib import Path

data_root, store_root, index_path = (Path(p) for p in sys.argv[1:4])
blobs = store_root / "blobs" / "sha256"
try:
    entries = json.loads(index_path.read_text(encoding="utf-8")).get("level_packs") or []
except (OSError, ValueError):
    sys.exit(0)
for entry in entries:
    manifest = entry.get("manifest")
    digest = str(entry.get("artifact_digest") or "")
    if not manifest or not digest.startswith("sha256:"):
        continue
    target = data_root / manifest
    if target.is_file():
        continue
    source = blobs / digest.removeprefix("sha256:") / Path(manifest).name
    if not source.is_file():
        print(f"WARNING: cannot backfill {target}: {source} does not exist", file=sys.stderr)
        continue
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    print(f"Backfilled staged manifest for {entry.get('id')}@{entry.get('version')}: {target}")
PY
  # Files the python above wrote (and the directory the shell container may
  # have created before it) are the caller's; make them the host user's.
  [[ "$(id -u)" == 0 && -d "$STAGED_DIR" ]] && find "$STAGED_DIR" -user 0 -exec chown "$HOST_UID:$HOST_GID" {} + 2>/dev/null
  return 0
}

if [[ -f "$STAGED_INDEX" && ! "$STORE_INDEX" -nt "$STAGED_INDEX" \
      && -f "$STAGED_STAMP" && "$(cat "$STAGED_STAMP")" == "$IMAGE" ]]; then
  backfill_level_manifests
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

backfill_level_manifests
mkdir -p "$STAGED_DIR"
printf '%s\n' "$IMAGE" >"$STAGED_STAMP"
own_by_host "$STAGED_DIR" "$STAGED_STAMP"
report_unexportable_library_packs
