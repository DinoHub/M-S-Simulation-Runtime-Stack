#!/usr/bin/env bash
# Is this machine ready to run the product? Checks Docker and Compose, creates
# the working directories, and lists every image the channel pins that is not
# in the local Docker store. In development mode (the default, as for make)
# it also lists every pinned tag that points at another digest than its pin:
# development mode runs the bare tags. Changes nothing else and pulls nothing.
#
#   tools/doctor.sh [--development|--production] [--channel v1]   (make doctor)
#
# Exit 0 when every image is present (and, in development mode, every pinned
# tag is at its pin), 1 otherwise. `make ensure-images` or `./setup.sh` fixes
# either.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHANNEL=v1
MODE=development
while [[ $# -gt 0 ]]; do
  case "$1" in
    --channel) CHANNEL="${2:?--channel needs a name}"; shift ;;
    --development) MODE=development ;;
    --production) MODE=production ;;
    -h|--help) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "usage: tools/doctor.sh [--development|--production] [--channel NAME]" >&2; exit 2 ;;
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

# Development mode runs repo:tag, so the tag has to name the pinned image. A
# tag republished in place since this machine pulled it does not, and the
# digest check above cannot see that.
stale=0
if [[ "$MODE" == development ]]; then
  # shellcheck source=tools/image-tags.sh
  . "$ROOT/tools/image-tags.sh"
  if ! pins_out="$("$ROOT/tools/images.sh" pins --channel "$CHANNEL")"; then
    echo "ERROR: could not list the channel's tag pins (tools/images.sh pins failed)." >&2
    exit 1
  fi
  while IFS=$'\t' read -r tag_ref digest; do
    [[ -n "$tag_ref" && -n "$digest" ]] || continue
    tag_state "$tag_ref" "$digest"
    case "$TAG_STATE" in
      at-pin) ;;
      built-here) echo "NOTE: $tag_ref is a local build, not the pinned $digest" ;;
      absent)
        # Already a MISSING IMAGE when the pinned image is absent too.
        if pin_present "$tag_ref" "$digest"; then
          echo "UNTAGGED: $tag_ref (the pinned $digest is here under its digest only)"
          stale=$((stale + 1))
        fi ;;
      stale)
        if keep_local_tags; then
          echo "NOTE: $tag_ref is $TAG_DIGESTS, not the pinned $digest (kept by MNS_KEEP_LOCAL_TAGS=1)"
        else
          echo "STALE TAG: $tag_ref is $TAG_DIGESTS, but the catalog pins $digest"
          stale=$((stale + 1))
        fi ;;
    esac
  done <<<"$pins_out"
fi

if [[ "$missing" == 0 && "$stale" == 0 ]]; then
  if [[ "$MODE" == development ]]; then
    echo "Ready: channel $CHANNEL, ${#images[@]} pinned image(s) present, and their tags point at the pins."
  else
    echo "Ready: channel $CHANNEL, ${#images[@]} pinned image(s) present."
  fi
  exit 0
fi
if [[ "$missing" != 0 ]]; then
  echo "$missing pinned image(s) missing. Pull them with ./setup.sh (or tools/pull-all-images.sh)." >&2
fi
if [[ "$stale" != 0 ]]; then
  echo "$stale tag(s) do not point at their pinned digest, so make dashboard, fly, author and" >&2
  echo "campaign (IMAGE_MODE=development, the default) would run other images. Fix:" >&2
  echo "  make ensure-images        (points each tag at its pin; pulls the pin only if it is not here)" >&2
fi
exit 1
