#!/usr/bin/env bash
# tmux dev session over a RUNNING scenario — the runtime-stack counterpart of
# the TEVV-Airsim-ROS2-Bridge repo's `make dev` session. The stack itself
# stays a detached docker compose project (launch.sh `up -d`); this script
# only attaches panes to it, so detaching/killing the tmux session never
# touches the containers.
#
# Layout:
#   window "dev" (focus): rviz2 pane (left) | teleop pane (right), side by
#                         side. rviz2 opens its GUI on $DISPLAY; each pane
#                         drops to an interactive shell inside its container
#                         when the process exits (or fails — e.g. no X).
#   window "sim"        : sim container logs (airsim-xfs / airsim-condo)
#   window "droneN"     : airsim_bridge_dN logs (top) | mavros_dN logs (bottom)
#
# The rviz layout is the image-baked lidar_single.rviz (registered cloud +
# camera + raw lidar) with its hardcoded Drone1 topics rewritten to the
# scenario's vehicle. Teleop keys: wasd move, r/f up/down, q/e yaw, 1 mode,
# 2 arm, 3 takeoff, 4 land, 0 disarm, space stop, x quit.
#
# Usage:  ./tools/attach-session.sh [session-name]    (or: make attach)
# Detach: Ctrl-b d   |   Kill dashboard only: tmux kill-session -t simstack
set -euo pipefail

SESSION="${1:-simstack}"
RVIZ_CFG="${RVIZ_CFG:-/opt/airsim/rviz/lidar_single.rviz}"

command -v tmux >/dev/null 2>&1 || { echo "ERROR: tmux not installed (apt install tmux)"; exit 1; }

# Vehicle naming follows .env (single source of truth), default Copter1.
if [ -f .env ]; then set -a; . ./.env; set +a; fi
VEHICLE="${VEHICLE_1_NAME:-${VEHICLE_PREFIX:-Copter}1}"

bridges=$(docker ps --format '{{.Names}}' | grep -E '^airsim_bridge_d[0-9]+$' | sort -V || true)
if [ -z "$bridges" ]; then
  echo "ERROR: no airsim_bridge_dN containers running — start a scenario first (make <scenario>)."
  exit 1
fi
first_bridge=$(echo "$bridges" | head -1)
sim=$(docker ps --format '{{.Names}}' | grep -E '^airsim-(xfs|condo)$' | head -1 || true)

# Teleop flavour: ardupilot SITL running -> GUIDED flow, else PX4 OFFBOARD.
if docker ps --format '{{.Names}}' | grep -q '^ardupilot'; then
  AUTOPILOT=ardupilot
else
  AUTOPILOT=px4
fi

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

# dev window: rviz2 (left) | teleop (right), each falling back to a shell in
# its container so the panes stay usable after the process exits.
tmux new-window -t "$SESSION" -n dev \
  "docker exec -it -e DISPLAY=${DISPLAY:-:1} $first_bridge bash -lc '\
     source /opt/ros/humble/setup.bash && source install/setup.bash && \
     sed \"s/Drone1/${VEHICLE}/g\" ${RVIZ_CFG} > /tmp/rviz_live.rviz && \
     rviz2 -d /tmp/rviz_live.rviz; exec bash -l'"
if docker ps --format '{{.Names}}' | grep -qx mavros_d1; then
  tmux split-window -t "$SESSION:dev" -h \
    "docker exec -it mavros_d1 bash -lc '\
       source /opt/ros/humble/setup.bash && source install/setup.bash && \
       ros2 run airsim_mavros_bringup mavros_teleop_keyboard.py \
         --ros-args -p vehicle:=${VEHICLE} -p autopilot:=${AUTOPILOT}; exec bash -l'"
else
  tmux split-window -t "$SESSION:dev" -h \
    "docker exec -it $first_bridge bash -lc '\
       source /opt/ros/humble/setup.bash && source /airsim_ros2_ws/install/setup.bash && exec bash'"
fi
tmux select-window -t "$SESSION:dev"

exec tmux attach -t "$SESSION"
