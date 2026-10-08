#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE=development
DRY_RUN=false
CHANNEL=v1

usage() {
  echo "Usage: tools/ensure-images.sh [--development|--production] [--channel NAME] [--dry-run]"
  echo
  echo "Uses an existing local image and pulls only when the selected ref is absent."
  echo "--development (default) uses the bare tags: a tag that points at another"
  echo "  digest than its catalog pin (a tag republished since this machine pulled"
  echo "  it) is pointed at the pin, pulling the pinned digest only if it is not"
  echo "  local. A tag whose image has no registry digest (built here) is kept, and"
  echo "  MNS_KEEP_LOCAL_TAGS=1 keeps every existing tag."
  echo "--production uses the exact repo:tag@digest refs."
  echo "--channel selects a release channel from images/catalog.yaml (default v1);"
  echo "  a channel's locally built (channel: local) images are checked for presence, never pulled."
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --development) MODE=development ;;
    --production) MODE=production ;;
    --channel) CHANNEL="${2:?--channel needs a name}"; shift ;;
    --dry-run) DRY_RUN=true ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
  shift
done

# shellcheck source=tools/image-tags.sh
. "$ROOT/tools/image-tags.sh"

# One row per pullable image: the ref this mode runs, then (development only)
# the digest the catalog pins that tag to, empty when nothing pins it.
# Captured first, then split: a process substitution's exit status is lost.
if [[ "$MODE" == development ]]; then
  rows_out="$("$ROOT/tools/images.sh" pins --channel "$CHANNEL")" || exit 1
else
  rows_out="$("$ROOT/tools/images.sh" refs --channel "$CHANNEL")" || exit 1
fi
mapfile -t rows <<<"$rows_out"
[[ "${#rows[@]}" -gt 0 && -n "${rows[0]}" ]] || { echo "No active product images are declared." >&2; exit 1; }

# channel: local rows have no registry counterpart. They were built on this
# machine (the catalog row's purpose says from what); a missing one is a
# build step the operator has to run, not a pull this script can do.
mapfile -t local_refs < <("$ROOT/tools/images.sh" local-refs --channel "$CHANNEL")
absent_local=()
for local_ref in "${local_refs[@]}"; do
  [[ -n "$local_ref" ]] || continue
  if docker image inspect "$local_ref" >/dev/null 2>&1; then
    echo "LOCAL   $local_ref (channel: local, built here)"
  else
    absent_local+=("$local_ref")
  fi
done
if [[ "${#absent_local[@]}" -gt 0 ]]; then
  echo "ERROR: channel $CHANNEL needs locally built image(s) that are not in the Docker store:" >&2
  printf '  %s\n' "${absent_local[@]}" >&2
  echo "       See the row's purpose in images/catalog.yaml and packs/README.md for the build steps." >&2
  [[ "$DRY_RUN" == true ]] || exit 1
fi

pull_with_retries() {
  local attempt
  for attempt in 1 2 3; do
    docker pull "$1" && return 0
    [[ "$attempt" == 3 ]] || sleep "$((attempt * 5))"
  done
  return 1
}

present=0
pulled=0
retagged=0
missing=0
failed=()
for row in "${rows[@]}"; do
  ref="${row%%$'\t'*}"
  digest=""
  [[ "$row" == *$'\t'* ]] && digest="${row#*$'\t'}"

  if [[ "$ref" == local/* ]]; then
    echo "ERROR: local/ repository reference escaped the image catalog: $ref" >&2
    exit 1
  fi

  if [[ -z "$digest" ]]; then
    # Production's exact refs, or a development tag nothing pins: present is
    # enough.
    if docker image inspect "$ref" >/dev/null 2>&1; then
      echo "LOCAL   $ref"
      present=$((present + 1))
      continue
    fi
    if [[ "$DRY_RUN" == true ]]; then
      echo "MISSING $ref"
      missing=$((missing + 1))
      continue
    fi
    if pull_with_retries "$ref"; then
      pulled=$((pulled + 1))
    else
      failed+=("$ref")
    fi
    continue
  fi

  # A development tag the catalog pins: it must point at the pinned digest.
  tag_state "$ref" "$digest"
  case "$TAG_STATE" in
    at-pin)
      echo "LOCAL   $ref"
      present=$((present + 1))
      continue ;;
    built-here)
      echo "LOCAL   $ref (built here: not the pinned $digest)"
      present=$((present + 1))
      continue ;;
    stale)
      if keep_local_tags; then
        echo "LOCAL   $ref (kept by MNS_KEEP_LOCAL_TAGS=1: it is $TAG_DIGESTS, the pin is $digest)"
        present=$((present + 1))
        continue
      fi ;;
  esac

  if [[ "$DRY_RUN" == true ]]; then
    if [[ "$TAG_STATE" == stale ]]; then
      echo "STALE   $ref (it is $TAG_DIGESTS, the pin is $digest)"
    else
      echo "MISSING $ref"
    fi
    missing=$((missing + 1))
    continue
  fi

  if ! pin_present "$ref" "$digest"; then
    if ! pull_with_retries "$(tag_repo "$ref")@$digest"; then
      failed+=("$(tag_repo "$ref")@$digest")
      continue
    fi
    pulled=$((pulled + 1))
  fi
  if ! point_tag_at_pin "$ref" "$digest"; then
    failed+=("$ref (docker tag to $digest)")
    continue
  fi
  if [[ "$TAG_STATE" == stale ]]; then
    echo "TAGGED  $ref -> $digest (it was $TAG_DIGESTS)"
  else
    echo "TAGGED  $ref -> $digest"
  fi
  retagged=$((retagged + 1))
done

if [[ "${#failed[@]}" -gt 0 ]]; then
  printf "ERROR: failed: %s\n" "${failed[@]}" >&2
  exit 1
fi

if [[ "$DRY_RUN" == true ]]; then
  echo "$MODE image check: $present local, $missing missing or stale."
elif [[ "$MODE" == development ]]; then
  echo "$MODE images ready: $present already local, $pulled pulled, $retagged tag(s) pointed at their pins."
else
  echo "$MODE images ready: $present already local, $pulled pulled because missing."
fi
