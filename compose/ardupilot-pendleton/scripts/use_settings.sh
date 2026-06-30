#!/usr/bin/env bash
# Swap ~/Documents/AirSim/settings.json between host-net and bridge variants.
# Symlinks (idempotent), so the source-of-truth stays the canonical files.
#
# Usage:
#   bash use_settings.sh host    # -> settings-ardupilot.json (UdpIp 127.0.0.1)
#   bash use_settings.sh bridge  # -> settings-ardupilot-bridge.json (UdpIp 172.26.0.x)
# Then press Play in Unreal Editor (settings.json is read on PIE replay).

set -euo pipefail

CONFIG_ROOT="${CONFIG_ROOT:-/home/mnsuser/TEVV-Metrics/configs}"
TARGET="$HOME/Documents/AirSim/settings.json"
mkdir -p "$(dirname "$TARGET")"

case "${1:-}" in
  host)
    SRC="$CONFIG_ROOT/unreal-airsim/pendleton/settings-ardupilot.json"
    ;;
  bridge)
    SRC="$CONFIG_ROOT/unreal-airsim/pendleton/settings-ardupilot-bridge.json"
    ;;
  *)
    echo "usage: $0 host|bridge" >&2
    exit 2
    ;;
esac

[ -f "$SRC" ] || { echo "missing: $SRC" >&2; exit 1; }

ln -sfn "$SRC" "$TARGET"
echo "[$1] $(readlink "$TARGET")"
echo "Now: stop+play PIE in Unreal Editor to reload settings."
