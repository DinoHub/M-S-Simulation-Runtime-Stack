#!/usr/bin/env bash
# End-to-end MIGHTY-on-ardupilot-xfs demo (drone 1).
#
# Reproduces the full verified flow:
#   1. main stack (SITL x N + AirSim XFS + per-drone bridges), with
#      airsim_bridge_d1 publishing a WORLD-frame cloud (MIGHTY consumes
#      /Copter1/registered_point_cloud raw; body-frame points corrupt its map)
#   2. mavros_d1 overlay (MAVLink TCP to the SITL on sim_net)
#   3. GUIDED + arm + takeoff via MAVROS services
#   4. MIGHTY planner container on agent_internal-1
#   5a. --goal x y z   : publish a single /goal and watch it fly
#   5b. --with-metrics : metrics-collector flies mission.json waypoints via
#                        /goal (goal-triggered mode) and evaluates the run
#
# Usage:
#   ./run-mighty-demo.sh                     # steps 1-4, then print goal cmd
#   ./run-mighty-demo.sh --goal 20 0 3       # + publish one goal
#   ./run-mighty-demo.sh --with-metrics      # + metrics mission flow
#   ./run-mighty-demo.sh --teardown          # stop demo extras (keeps main stack)
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
TAKEOFF_ALT="${TAKEOFF_ALT:-3.0}"
MIGHTY_PARAMS="${MIGHTY_PARAMS:-${REPO_ROOT}/config/experiments/mighty.yaml}"

MODE="stack-only"
GOAL_XYZ=()
case "${1:-}" in
  --teardown)
    docker rm -f mighty_d1 metrics-collector >/dev/null 2>&1 || true
    (cd "$HERE" && docker compose -f docker-compose.mavros-test.yml down --remove-orphans >/dev/null 2>&1) || true
    echo "demo extras torn down (main stack left running; ./stop.sh for full stop)"
    exit 0
    ;;
  --goal)
    MODE="goal"; GOAL_XYZ=("${2:?x}" "${3:?y}" "${4:?z}")
    ;;
  --with-metrics)
    MODE="metrics"
    ;;
esac

cd "$REPO_ROOT"

echo "=== [1/5] main stack (LOCAL_OBS_TARGET_FRAME=map for MIGHTY) ==="
# Shell env beats .env for compose interpolation — the repo default is
# base_link, MIGHTY needs world-frame clouds.
export LOCAL_OBS_TARGET_FRAME=map
if docker ps --format '{{.Names}}' | grep -qx ardupilot-xfs-airsim; then
  echo "  stack already up — ensuring bridge d1 has map-frame cloud"
  export CONFIG_ROOT="$REPO_ROOT/config" XAUTHORITY="${XAUTHORITY:-$HOME/.Xauthority}"
  if ! docker exec airsim_bridge_d1 printenv LOCAL_OBS_TARGET_FRAME 2>/dev/null | grep -qx map; then
    docker compose --project-directory "$REPO_ROOT" \
      -f compose/ardupilot-xfs/docker-compose.yml \
      --profile sim --profile per-drone-bridge \
      up -d --force-recreate airsim_bridge_d1
  fi
else
  ./launch.sh ardupilot-xfs
fi

echo "=== [2/5] mavros_d1 overlay ==="
(cd "$HERE" && XAUTHORITY="${XAUTHORITY:-$HOME/.Xauthority}" \
  docker compose -f docker-compose.mavros-test.yml up -d mavros_d1)

echo "  waiting for FCU connection..."
for i in $(seq 1 30); do
  if docker exec mavros_d1 bash -lc \
      'timeout 5 ros2 topic echo /Copter1/mavros/state --once 2>/dev/null | grep -q "connected: true"'; then
    echo "  FCU connected"; break
  fi
  [ "$i" -eq 30 ] && { echo "ERROR: MAVROS never connected"; exit 1; }
  sleep 2
done

echo "=== [3/5] GUIDED + arm + takeoff (${TAKEOFF_ALT}m) ==="
docker exec mavros_d1 bash -lc "
  set -e
  ros2 service call /Copter1/mavros/set_mode mavros_msgs/srv/SetMode '{custom_mode: GUIDED}' >/dev/null
  sleep 2
  ros2 service call /Copter1/mavros/cmd/arming mavros_msgs/srv/CommandBool '{value: true}' >/dev/null
  sleep 2
  ros2 service call /Copter1/mavros/cmd/takeoff mavros_msgs/srv/CommandTOL '{altitude: ${TAKEOFF_ALT}}' >/dev/null
  echo '  takeoff commanded'
"
# Gate on ~target altitude, not just "off the ground": starting MIGHTY
# mid-climb kills the takeoff (its pre-plan zero-velocity heartbeat
# overrides the GUIDED climb and the drone sinks back down). Window is
# generous — GPU-lidar render load can slow the whole sim well below
# real-time.
MIN_ALT=$(awk -v a="$TAKEOFF_ALT" 'BEGIN{print a-0.7}')
echo "  waiting to reach ${MIN_ALT}m..."
for i in $(seq 1 90); do
  z=$(docker exec airsim_bridge_d1 bash -lc \
    'ros2 topic echo /Copter1/ground_truth/odom --once 2>/dev/null | sed -n "/position:/,/orientation:/p" | grep "z:" | awk "{print \$2}"' || echo 0)
  awk -v z="$z" -v m="$MIN_ALT" 'BEGIN{exit !(z>m)}' && { echo "  airborne (z=${z})"; break; }
  [ "$i" -eq 90 ] && { echo "ERROR: never reached ${MIN_ALT}m (z=${z})"; exit 1; }
  sleep 2
done

echo "=== [4/5] MIGHTY planner ==="
MIGHTY_PARAMS="$MIGHTY_PARAMS" "$HERE/scripts/run_mighty_d1.sh"
sleep 8
docker logs mighty_d1 2>&1 | grep -q "Adaptor ready" || { echo "ERROR: adaptor not ready — docker logs mighty_d1"; exit 1; }
echo "  MIGHTY ready"

case "$MODE" in
  goal)
    echo "=== [5/5] publishing goal (${GOAL_XYZ[*]}) ==="
    docker exec mavros_d1 bash -lc "ros2 topic pub --once /goal geometry_msgs/msg/PoseStamped \
      '{header: {frame_id: map}, pose: {position: {x: ${GOAL_XYZ[0]}, y: ${GOAL_XYZ[1]}, z: ${GOAL_XYZ[2]}}, orientation: {w: 1.0}}}'" >/dev/null
    echo "  goal sent — watch: docker logs -f mighty_d1"
    ;;
  metrics)
    echo "=== [5/5] metrics mission (mission.json via /goal) ==="
    # Goal-triggered mode: scenario_controller flies mission.json waypoints.
    # ROS_DOMAIN_ID=1 puts the host-net collector on drone 1's DDS domain.
    # GOAL_TOLERANCE=1.0: MIGHTY's goal_seen_radius leaves the drone ~0.8 m
    # short of the exact goal — 0.5 m never fires (scenario_controller.yaml
    # carries the matching value for the controller side).
    CONFIG_ROOT="$REPO_ROOT/config" ROS_DOMAIN_ID=1 LOCAL_PLANNER=mighty \
      USE_RUN_STATE_TRIGGER=false GOAL_TOLERANCE=1.0 \
      docker compose -f docker-compose-metrics.yml --profile metrics \
      up -d --force-recreate metrics-collector
    echo "  waiting for scenario controller..."
    for i in $(seq 1 30); do
      docker logs metrics-collector 2>&1 | grep -q "Scenario controller running" && break
      [ "$i" -eq 30 ] && { echo "ERROR: controller never ready — docker logs metrics-collector"; exit 1; }
      sleep 2
    done
    docker exec metrics-collector bash -lc \
      'source /opt/ros/*/setup.bash; ros2 service call /scenario/start_mission std_srvs/srv/Trigger' \
      | grep -E "success|message"
    echo "  mission started — watch: docker logs -f metrics-collector"
    echo "  results land in ./metrics_outputs/ on finalize"
    ;;
  *)
    echo "=== [5/5] ready — send a goal: ==="
    echo "  docker exec mavros_d1 bash -lc \"ros2 topic pub --once /goal geometry_msgs/msg/PoseStamped '{header: {frame_id: map}, pose: {position: {x: 20.0, y: 0.0, z: 3.0}, orientation: {w: 1.0}}}'\""
    ;;
esac
