#!/usr/bin/env bash
# Build tevv-airsim-ros2-bridge:v1.0.0-shmfix.1 from a subscriber binary built from the
# bridge PR branches (#75 + #76). BIN defaults to the copy kept in tevv_ws.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
BIN=${BIN:-$HOME/tevv_ws/testing/lighting/bin/iceoryx_image_subscriber}
[ -x "$BIN" ] || { echo "no subscriber binary at $BIN" >&2; exit 1; }
ctx=$(mktemp -d); trap 'rm -rf "$ctx"' EXIT
cp "$HERE/Dockerfile" "$BIN" "$ctx/"
docker build -t tevv-airsim-ros2-bridge:v1.0.0-shmfix.1 "$ctx"
