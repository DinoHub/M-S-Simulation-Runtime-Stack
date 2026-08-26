#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STORE_INDEX="$ROOT/.mns/pack-store/index.json"
STAGED_INDEX="$ROOT/.mns/authoring-data/ResolvedPacks/index.json"
IMAGE="${MNS_PRODUCT_SHELL_IMAGE:?MNS_PRODUCT_SHELL_IMAGE is required}"

mkdir -p "$ROOT/.mns/pack-store" "$ROOT/.mns/authoring-data"
if [[ -f "$STAGED_INDEX" && -f "$STORE_INDEX" && ! "$STORE_INDEX" -nt "$STAGED_INDEX" ]]; then
  echo "ScenarioLab pack index is current: $STAGED_INDEX"
  exit 0
fi

docker run --rm \
  --user "$(id -u):$(id -g)" \
  -e HOME=/tmp \
  -e MNS_WORKSPACE_ROOT=/workspace \
  -e MNS_PACK_STORE_ROOT=/workspace/.mns/pack-store \
  -e MNS_AUTHORING_DATA_ROOT=/workspace/.mns/authoring-data \
  -v "$ROOT:/workspace:rw" \
  "$IMAGE" packs stage-authoring
