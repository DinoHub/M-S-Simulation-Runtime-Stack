#!/usr/bin/env bash
# DEPRECATED — check-image-pins.sh is superseded by tools/images.sh, which
# reads pins from images/catalog.yaml (the single source; product-images.env
# is now generated from it). This shim maps the old flags through so existing
# muscle memory and PR descriptions referencing --bump/--drift keep working.
# See docs/adr/0002-one-image-catalog.md.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "tools/check-image-pins.sh is deprecated; use tools/images.sh (report|bump|drift|baked)." >&2

case "${1:-report}" in
  report)  exec "$ROOT/tools/images.sh" report ;;
  --bump)  exec "$ROOT/tools/images.sh" bump ;;
  --drift) exec "$ROOT/tools/images.sh" drift ;;
  *)       echo "usage: $0 [--bump|--drift]" >&2; exit 2 ;;
esac
