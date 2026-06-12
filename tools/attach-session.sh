#!/usr/bin/env bash
# tmux dashboard over a RUNNING scenario — the runtime-stack counterpart of
# the TEVV-Airsim-ROS2-Bridge repo's `make dev` session. The stack itself
# stays a detached docker compose project (launch.sh `up -d`); this script
# only attaches log panes + a shell to it, so detaching/killing the tmux
# session never touches the containers.
#
# Layout:
#   window "sim"      : sim container logs (airsim-xfs / airsim-condo)
#   window "droneN"   : airsim_bridge_dN logs (top) | mavros_dN logs (bottom)
#   window "shell"    : interactive shell inside airsim_bridge_d1 (ROS sourced
#                       by the image entrypoint; ros2 CLI works directly)
#
# Usage:  ./tools/attach-session.sh [session-name]    (or: make attach)
# Detach: Ctrl-b d   |   Kill dashboard only: tmux kill-session -t simstack
set -euo pipefail

SESSION="${1:-simstack}"

command -v tmux >/dev/null 2>&1 || { echo "ERROR: tmux not installed (apt install tmux)"; exit 1; }

bridges=$(docker ps --format '{{.Names}}' | grep -E '^airsim_bridge_d[0-9]+$' | sort -V || true)
if [ -z "$bridges" ]; then
  echo "ERROR: no airsim_bridge_dN containers running — start a scenario first (make <scenario>)."
  exit 1
fi
first_bridge=$(echo "$bridges" | head -1)
sim=$(docker ps --format '{{.Names}}' | grep -E '^airsim-(xfs|condo)$' | head -1 || true)

# Fresh session each time; never kills containers, only the old dashboard.
tmux kill-session -t "$SESSION" 2>/dev/null || true

tmux new-session -d -s "$SESSION" -n sim \
  "docker logs -f --tail 50 ${sim:-$first_bridge}"

for b in $bridges; do
  n="${b##*_d}"
  tmux new-window -t "$SESSION" -n "drone${n}" "docker logs -f --tail 50 $b"
  if docker ps --format '{{.Names}}' | grep -qx "mavros_d${n}"; then
    tmux split-window -t "$SESSION:drone${n}" -v "docker logs -f --tail 50 mavros_d${n}"
  fi
done

tmux new-window -t "$SESSION" -n shell "docker exec -it $first_bridge bash -l"
tmux select-window -t "$SESSION:shell"

exec tmux attach -t "$SESSION"
