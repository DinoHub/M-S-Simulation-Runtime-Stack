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

IMAGE="${MNS_PRODUCT_SHELL_IMAGE:?MNS_PRODUCT_SHELL_IMAGE is required}"
PULL_POLICY="${MNS_IMAGE_PULL_POLICY:-missing}"
case "$PULL_POLICY" in
  always|missing|never) ;;
  *)
    echo "MNS_IMAGE_PULL_POLICY must be always, missing, or never; got: $PULL_POLICY" >&2
    exit 2
    ;;
esac

if [[ -f "$STAGED_INDEX" && ! "$STORE_INDEX" -nt "$STAGED_INDEX" \
      && -f "$STAGED_STAMP" && "$(cat "$STAGED_STAMP")" == "$IMAGE" ]]; then
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

mkdir -p "$STAGED_DIR"
printf '%s\n' "$IMAGE" >"$STAGED_STAMP"
own_by_host "$STAGED_DIR" "$STAGED_STAMP"
report_unexportable_library_packs
