#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE=development
DRY_RUN=false

usage() {
  echo "Usage: tools/ensure-images.sh [--development|--production] [--dry-run]"
  echo
  echo "Uses an existing local image and pulls only when the selected ref is absent."
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --development) MODE=development ;;
    --production) MODE=production ;;
    --dry-run) DRY_RUN=true ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
  shift
done

refs_args=()
[[ "$MODE" == development ]] && refs_args+=(--development)
mapfile -t exact_refs < <("$ROOT/tools/images.sh" refs "${refs_args[@]}")
[[ "${#exact_refs[@]}" -gt 0 ]] || { echo "No active product images are declared." >&2; exit 1; }

present=0
pulled=0
missing=0
failed=()
for exact_ref in "${exact_refs[@]}"; do
  ref="$exact_ref"

  if [[ "$ref" == local/* ]]; then
    echo "ERROR: local/ repository reference escaped the image catalog: $ref" >&2
    exit 1
  fi
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

  ok=false
  for attempt in 1 2 3; do
    if docker pull "$ref"; then
      ok=true
      pulled=$((pulled + 1))
      break
    fi
    [[ "$attempt" == 3 ]] || sleep "$((attempt * 5))"
  done
  [[ "$ok" == true ]] || failed+=("$ref")
done

if [[ "${#failed[@]}" -gt 0 ]]; then
  printf "ERROR: failed to pull %s\n" "${failed[@]}" >&2
  exit 1
fi

if [[ "$DRY_RUN" == true ]]; then
  echo "$MODE image check: $present local, $missing would pull."
else
  echo "$MODE images ready: $present already local, $pulled pulled because missing."
fi
