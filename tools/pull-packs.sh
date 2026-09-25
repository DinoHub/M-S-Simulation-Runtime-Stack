#!/usr/bin/env bash
# Pull published MnS packs into the pack mount directory and install them.
#
#   tools/pull-packs.sh --list-remote                      # pack releases cooked for this channel's host
#   tools/pull-packs.sh --release-tag pack-level-blocks-1.0.1 [--release-tag ...] [--repo owner/repo]
#   tools/pull-packs.sh --import [DIR]                     # archives already in the mount directory
#   tools/pull-packs.sh --all                              # everything in the channel's lock
#   tools/pull-packs.sh --status                           # locked vs installed vs published
#   tools/pull-packs.sh --remove blocks [--unstage]        # uninstall one pack
#   tools/pull-packs.sh --check                            # offline: what is installed
#
# Thin wrapper over tools/install_demo_packs.py so the channel roots (lock,
# store, contract, pack mount directory MNS_PACKS_DIR) come from the same env
# the Makefile, product.sh and the dashboard backend export. Every archive is
# verified (size, sha256 from the release, and the product shell's own
# `packs verify`) before it enters the store, and ScenarioLab's index is
# re-staged afterwards. Split releases (>1900 MB, published as .part-NNN
# assets) are reassembled here.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ $# -gt 0 ]] || { sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }
exec python3 "$SCRIPT_DIR/install_demo_packs.py" "$@"
