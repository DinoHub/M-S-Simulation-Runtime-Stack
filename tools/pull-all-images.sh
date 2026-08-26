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
  echo "Pulls the active product's remote images at exact catalog tag+digest pins."
  echo "--all-catalog includes legacy and optional observability images."
  echo "--development refreshes mutable tag-only refs used by make dashboard."
  echo "--refresh-moving first advances mutable catalog pins, regenerates, and verifies."
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

if [[ "$REFRESH_MOVING" == true ]]; then
  "$ROOT/tools/images.sh" bump --channel moving
  "$ROOT/tools/images.sh" sync
  "$ROOT/tools/images.sh" verify
fi

refs_args=()
[[ "$ALL_CATALOG" == true ]] && refs_args+=(--all-catalog)
[[ "$DEVELOPMENT" == true ]] && refs_args+=(--development)
mapfile -t refs < <("$ROOT/tools/images.sh" refs "${refs_args[@]}")
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
else
  echo "Pulled ${#refs[@]} exact remote image pin(s); the next production run uses this approved catalog set."
fi
