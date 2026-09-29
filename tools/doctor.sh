#!/usr/bin/env bash
# Is this machine ready to run the product? Checks Docker and Compose, creates
# the working directories, and lists every image the channel pins that is not
# in the local Docker store. Changes nothing else and pulls nothing.
#
#   tools/doctor.sh [--channel v1]      (make doctor)
#
# Exit 0 when every image is present, 1 otherwise. `./setup.sh` (or
# tools/pull-all-images.sh) pulls what is missing.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHANNEL=v1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --channel) CHANNEL="${2:?--channel needs a name}"; shift ;;
    -h|--help) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "usage: tools/doctor.sh [--channel NAME]" >&2; exit 2 ;;
  esac
  shift
done

# shellcheck source=tools/check_docker.sh
. "$ROOT/tools/check_docker.sh"
check_docker || exit 1
if ! docker compose version >/dev/null 2>&1; then
  echo "ERROR: the Docker Compose v2 plugin is missing: sudo apt-get install -y docker-compose-plugin" >&2
  exit 1
fi

DATA_ROOT="${MNS_AUTHORING_DATA_ROOT:-$ROOT/.mns/$CHANNEL/authoring-data}"
mkdir -p "$DATA_ROOT/PackLibrary/level_packs" "$DATA_ROOT/PackLibrary/asset_packs" \
  "${MNS_PACK_STORE_ROOT:-$ROOT/.mns/$CHANNEL/pack-store}" "${MNS_PACKS_DIR:-$ROOT/.mns/$CHANNEL/packs}" \
  "$ROOT/scenarios" "$ROOT/generated"

# Captured first, then split: a process substitution's exit status is lost,
# and an empty list must never read as "everything is present".
if ! images_out="$("$ROOT/tools/images.sh" refs --channel "$CHANNEL")"; then
  echo "ERROR: could not list the channel's images (tools/images.sh refs failed)." >&2
  echo "       See the Requirements section of README.md." >&2
  exit 1
fi
mapfile -t images <<<"$images_out"
if [[ "${#images[@]}" -eq 0 || -z "${images[0]}" ]]; then
  echo "ERROR: tools/images.sh refs returned no images; refusing to report success." >&2
  exit 1
fi
# channel: local rows are not pullable, but must be present all the same.
mapfile -t local_images < <("$ROOT/tools/images.sh" local-refs --channel "$CHANNEL")

missing=0
for image in "${images[@]}" "${local_images[@]}"; do
  [[ -n "$image" ]] || continue
  docker image inspect "$image" >/dev/null 2>&1 || { echo "MISSING IMAGE: $image"; missing=$((missing + 1)); }
done
if [[ "$missing" == 0 ]]; then
  echo "Ready: channel $CHANNEL, ${#images[@]} pinned image(s) present."
  exit 0
fi
echo "$missing pinned image(s) missing. Pull them with ./setup.sh (or tools/pull-all-images.sh)." >&2
exit 1
