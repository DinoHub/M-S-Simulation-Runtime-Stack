#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DRY_RUN=false
REFRESH_MOVING=false
ALL_CATALOG=false
DEVELOPMENT=false

usage() {
  echo "Usage: tools/pull-all-images.sh [--dry-run] [--refresh-moving] [--all-catalog] [--development]"
  echo
  echo "Pulls the active product's remote images at exact catalog tag+digest pins,"
  echo "then points each pinned tag at its digest (docker tag repo@digest repo:tag):"
  echo "pulling by digest does not move a tag, and development mode (the default)"
  echo "runs the bare tags."
  echo "--all-catalog includes optional catalog images too."
  echo "--development refreshes mutable tag-only refs used by make dashboard."
  echo "--refresh-moving first runs 'images.sh bump --channel moving', regenerates, and"
  echo "  verifies. That advances every channel: moving row (the dashboard, autopilot"
  echo "  and QGroundControl images) to the digest its mutable tag points at"
  echo "  now. It does NOT touch the v1 release rows: those are channel: pinned,"
  echo "  and bump refuses pinned rows by design. Rewrites the catalog, so it is"
  echo "  refused together with --dry-run."
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=true ;;
    --refresh-moving) REFRESH_MOVING=true ;;
    --all-catalog) ALL_CATALOG=true ;;
    --development) DEVELOPMENT=true ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
  shift
done

# Refuse rather than silently ignore: --refresh-moving REWRITES
# images/catalog.yaml and every generated artifact, which is the opposite of
# --dry-run's documented "print exact refs without pulling". Ordering the
# checks the other way round (bump first, DRY_RUN exit later) meant
# `--dry-run --refresh-moving` mutated eight tracked files.
if [[ "$REFRESH_MOVING" == true && "$DRY_RUN" == true ]]; then
  echo "ERROR: --refresh-moving rewrites images/catalog.yaml and the generated" >&2
  echo "       artifacts, so it cannot be combined with --dry-run." >&2
  echo "       To preview: tools/images.sh report" >&2
  exit 2
fi

if [[ "$REFRESH_MOVING" == true ]]; then
  "$ROOT/tools/images.sh" bump --channel moving
  "$ROOT/tools/images.sh" sync
  "$ROOT/tools/images.sh" verify
fi

refs_args=()
[[ "$ALL_CATALOG" == true ]] && refs_args+=(--all-catalog)
[[ "$DEVELOPMENT" == true ]] && refs_args+=(--development)
# Capture before splitting: a process substitution's exit status is discarded,
# so a failing `images.sh refs` used to surface as the misleading "No pullable
# remote images are declared." instead of naming the real cause.
if ! refs_out="$("$ROOT/tools/images.sh" refs "${refs_args[@]}")"; then
  echo "ERROR: could not enumerate catalog images (tools/images.sh refs failed)." >&2
  echo "       See the Requirements section of README.md." >&2
  exit 1
fi
mapfile -t refs <<<"$refs_out"
[[ "${#refs[@]}" -eq 1 && -z "${refs[0]}" ]] && refs=()
if [[ "${#refs[@]}" -eq 0 ]]; then
  echo "No pullable remote images are declared." >&2
  exit 1
fi

for ref in "${refs[@]}"; do
  if [[ "$ref" == local/* ]]; then
    echo "ERROR: local image escaped catalog filtering: $ref" >&2
    exit 1
  fi
done

if [[ "$DRY_RUN" == true ]]; then
  printf '%s\n' "${refs[@]}"
  echo "Would pull ${#refs[@]} remote image(s)." >&2
  exit 0
fi

failed=()
for ref in "${refs[@]}"; do
  ok=false
  for attempt in 1 2 3; do
    if docker pull "$ref"; then
      ok=true
      break
    fi
    [[ "$attempt" == 3 ]] || sleep "$((attempt * 5))"
  done
  [[ "$ok" == true ]] || failed+=("$ref")
done

if [[ "${#failed[@]}" -ne 0 ]]; then
  printf 'ERROR: failed to pull %s\n' "${failed[@]}" >&2
  exit 1
fi

if [[ "$DEVELOPMENT" == true ]]; then
  echo "Refreshed ${#refs[@]} development image tag(s); make dashboard will use them locally."
  exit 0
fi

# `docker pull repo:tag@digest` stores the image under its digest and leaves
# repo:tag wherever it was. When the registry tag was republished in place,
# that is the OLD image, and development mode (make dashboard, fly, author,
# campaign) runs the bare tag. Point every pinned tag at the pin just pulled.
# shellcheck source=tools/image-tags.sh
. "$ROOT/tools/image-tags.sh"
pins_args=()
[[ "$ALL_CATALOG" == true ]] && pins_args+=(--all-catalog)
if ! pins_out="$("$ROOT/tools/images.sh" pins "${pins_args[@]}")"; then
  echo "ERROR: could not list the catalog's tag pins (tools/images.sh pins failed)." >&2
  exit 1
fi
retagged=0
while IFS=$'\t' read -r tag_ref digest; do
  [[ -n "$tag_ref" && -n "$digest" ]] || continue
  tag_state "$tag_ref" "$digest"
  [[ "$TAG_STATE" == at-pin ]] && continue
  if ! point_tag_at_pin "$tag_ref" "$digest"; then
    failed+=("$tag_ref")
    continue
  fi
  if [[ "$TAG_STATE" == absent ]]; then
    echo "TAGGED  $tag_ref -> $digest"
  else
    echo "TAGGED  $tag_ref -> $digest (it was ${TAG_DIGESTS:-a local build})"
  fi
  retagged=$((retagged + 1))
done <<<"$pins_out"
if [[ "${#failed[@]}" -ne 0 ]]; then
  printf 'ERROR: could not point %s at its pinned digest\n' "${failed[@]}" >&2
  exit 1
fi

echo "Pulled ${#refs[@]} exact remote image pin(s) and pointed $retagged tag(s) at them; development and production runs both use this approved catalog set."
