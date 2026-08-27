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
  echo "--refresh-moving first runs 'images.sh bump --channel moving', regenerates, and"
  echo "  verifies. That advances every channel: moving row (the legacy sims,"
  echo "  observability, and dashboard images) to the digest its mutable tag points at"
  echo "  now. It does NOT touch the standalone-v2 rows: those are channel: pinned,"
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
else
  echo "Pulled ${#refs[@]} exact remote image pin(s); the next production run uses this approved catalog set."
fi
