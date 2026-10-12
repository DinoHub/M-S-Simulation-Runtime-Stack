#!/usr/bin/env bash
# Copy runs data from the old locations into the one runs folder.
#
#   tools/migrate-runs-dir.sh            # dry run: list what would be copied
#   tools/migrate-runs-dir.sh --apply    # copy it
#
# Before this, runs data could land in ~/tevv-runs (the old generator and
# dashboard default) or /tmp/tevv-runs (generated stacks whose .env was written
# from the generator container's own $HOME). The one runs folder is now
# $TEVV_RUNS_DIR, default runs/ in this checkout (see docs/metrics.md).
#
# Only copies; nothing is deleted. Files that already exist in the target are
# left alone. Root-owned files (written by containers that ran as root) are
# copied through a throwaway container and handed back to you. Once you have
# checked the result, delete the old folders yourself.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${TEVV_RUNS_DIR:-$(sed -n 's/^TEVV_RUNS_DIR=//p' "$ROOT/.env" 2>/dev/null | tail -1)}"
TARGET="${TARGET:-$ROOT/runs}"
case "$TARGET" in "~"*) TARGET="$HOME${TARGET#\~}" ;; esac
APPLY=0
[[ "${1:-}" == "--apply" ]] && APPLY=1
SOURCES=("$HOME/tevv-runs" "/tmp/tevv-runs")

mkdir -p "$TARGET"
TARGET="$(cd "$TARGET" && pwd)"
echo "Target runs folder: $TARGET"

found=0
for src in "${SOURCES[@]}"; do
  [[ -d "$src" ]] || continue
  src="$(cd "$src" && pwd)"
  [[ "$src" == "$TARGET" ]] && continue
  while IFS= read -r -d '' entry; do
    name="$(basename "$entry")"
    found=1
    if [[ -e "$TARGET/$name" ]]; then
      echo "  skip   $entry (already in target)"
      continue
    fi
    owner="$(stat -c %U "$entry" 2>/dev/null || echo '?')"
    size="$(du -sh "$entry" 2>/dev/null | cut -f1)"
    echo "  copy   $entry  ($size, owner $owner)"
    [[ "$APPLY" == 1 ]] || continue
    if [[ -r "$entry" ]] && { [[ -f "$entry" ]] || [[ -z "$(find "$entry" ! -readable -print -quit 2>/dev/null)" ]]; }; then
      cp -a "$entry" "$TARGET/$name"
    else
      docker run --rm -v "$src:/src:ro" -v "$TARGET:/dst" alpine:3 \
        sh -c "cp -a /src/$name /dst/$name && chown -R $(id -u):$(id -g) /dst/$name"
    fi
  done < <(find "$src" -mindepth 1 -maxdepth 1 -print0)
done

if [[ "$found" == 0 ]]; then
  echo "Nothing to migrate."
elif [[ "$APPLY" == 0 ]]; then
  echo "Dry run. Re-run with --apply to copy."
else
  echo "Done. Old folders were left in place: ${SOURCES[*]}"
fi
