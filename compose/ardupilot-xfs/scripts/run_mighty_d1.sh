#!/bin/bash
# Launch the MIGHTY local planner against ardupilot-xfs drone 1.
#
# Targets the registry image dhdevspace/auto_mns:mighty_algo_only (runs as
# `appuser`, workspaces under /workspace/generated/mighty — the OLD local
# MIGHTY-docker/run.sh sources /root/* paths that do not exist in this image).
#
# Stack-specific wiring:
#   1. --network agent_internal-1 (NOT host): the airsim_bridge_d1 +
#      mavros_d1 DDS island lives on this docker network.
#   2. ROS_DOMAIN_ID defaults to 1 (drone 1's domain).
#   3. autopilot:=ardupilot — the adaptor natively handles ArduPilot
#      (publish policy / start phase differ from PX4 OFFBOARD prestream).
#   4. vehicle_output_topic:=/Copter1/mavros/setpoint_raw/local — MAVROS
#      setpoint_raw plugin converts PositionTarget to
#      SET_POSITION_TARGET_LOCAL_NED, accepted by ArduCopter in GUIDED.
#
# Prereqs:
#   - main stack up:      ./launch.sh ardupilot-xfs
#   - mavros overlay up:  cd compose/ardupilot-xfs && \
#                           docker compose -f docker-compose.mavros-test.yml up -d mavros_d1
#   - drone 1 airborne in GUIDED (see README, MIGHTY section)
#
# Env overrides:
#   ROS_DOMAIN_ID   - DDS domain (default 1, must match DRONE_1_DOMAIN_ID)
#   VEHICLE         - vehicle namespace (default Copter1)
#   USE_RVIZ        - true to start RViz inside the container (needs X11)
#   MIGHTY_PARAMS   - host path to a mighty.yaml to use instead of the
#                     image's baked config
#   AUTOPILOT       - ardupilot (default) | px4. The adaptor's heartbeat
#                     publish policy / start phase differ (PX4 needs an
#                     OFFBOARD setpoint prestream; ArduCopter GUIDED does
#                     not).
#   MIGHTY_NETWORK  - docker network (default agent_internal-1). For the
#                     all-host-net px4-condo scenario use:
#                       MIGHTY_NETWORK=host ROS_DOMAIN_ID=0 AUTOPILOT=px4
set -euo pipefail

IMAGE="${MIGHTY_IMAGE:-dhdevspace/auto_mns:mighty_algo_only}"
NAME="${NAME:-mighty_d1}"
NETWORK="${MIGHTY_NETWORK:-agent_internal-1}"
VEHICLE="${VEHICLE:-Copter1}"
DOMAIN="${ROS_DOMAIN_ID:-1}"
USE_RVIZ="${USE_RVIZ:-false}"
AUTOPILOT="${AUTOPILOT:-ardupilot}"

WS=/workspace/generated/mighty
PARAMS_IN_IMAGE="${WS}/.planner_ws/ros_ws/src/mighty/config/mighty.yaml"
# Default to the repo's fixed params copy. The image's baked mighty.yaml has a
# bare `mighty_node:` header that never matches the node inside /NX01 — every
# param silently ignored (use_free_start=0 -> "Start is not free" + segfault).
REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
MIGHTY_PARAMS="${MIGHTY_PARAMS:-${REPO_ROOT}/config/experiments/mighty.yaml}"

if [ "$NETWORK" != "host" ] && ! docker network inspect "$NETWORK" >/dev/null 2>&1; then
  echo "ERROR: docker network '$NETWORK' not found. Is the main stack up? (./launch.sh ardupilot-xfs)"
  exit 1
fi

PARAMS_MOUNT=()
PARAMS_PATH="$PARAMS_IN_IMAGE"
if [ -n "${MIGHTY_PARAMS:-}" ] && [ "$MIGHTY_PARAMS" != "baked" ]; then
  if [ ! -f "$MIGHTY_PARAMS" ]; then
    echo "ERROR: MIGHTY_PARAMS not found: $MIGHTY_PARAMS"
    exit 1
  fi
  PARAMS_MOUNT=(-v "${MIGHTY_PARAMS}:/config/mighty.yaml:ro")
  PARAMS_PATH=/config/mighty.yaml
fi

xhost +local:docker >/dev/null 2>&1 || true
docker rm -f "$NAME" >/dev/null 2>&1 || true

docker run -d \
  --name "$NAME" \
  --network "$NETWORK" \
  --ipc=host \
  -e DISPLAY="${DISPLAY:-:0}" \
  -e QT_X11_NO_MITSHM=1 \
  -e ROS_DOMAIN_ID="$DOMAIN" \
  -v /tmp/.X11-unix:/tmp/.X11-unix:rw \
  "${PARAMS_MOUNT[@]}" \
  "$IMAGE" \
  bash -lc "
    set -e
    source /opt/ros/humble/setup.bash
    source ${WS}/.planner_ws/decomp_ws/install/setup.bash
    source ${WS}/.planner_ws/ros_ws/install/setup.bash
    # adaptor_ws/install/setup.bash does NOT register the package (its
    # prefix-level chain is broken in this image — no package-level
    # local_setup, COLCON_IGNORE at the install root). Export the ament
    # prefix + python path manually instead.
    export AMENT_PREFIX_PATH=${WS}/adaptor_ws/install/mighty_adaptor:\$AMENT_PREFIX_PATH
    export PYTHONPATH=${WS}/adaptor_ws/install/mighty_adaptor/lib/python3.10/site-packages:\$PYTHONPATH

    exec ros2 launch mighty_adaptor mighty_adaptor.launch.py \
      namespace:='${NAMESPACE:-NX01}' \
      autopilot:='${AUTOPILOT}' \
      odom_external_topic:='/${VEHICLE}/ground_truth/odom' \
      occupancy_external_topic:='/${VEHICLE}/registered_point_cloud' \
      term_goal_external_topic:='${GOAL_TOPIC:-/goal}' \
      vehicle_output_topic:='/${VEHICLE}/mavros/setpoint_raw/local' \
      runtime_params_file:='${PARAMS_PATH}' \
      use_rviz:='${USE_RVIZ}'
  "

echo "Started: ${NAME} (network=${NETWORK}, domain=${DOMAIN}, vehicle=${VEHICLE}, autopilot=${AUTOPILOT})"
echo "Logs:    docker logs -f ${NAME}"
echo "Goal:    docker exec mavros_d1 bash -lc \"ros2 topic pub --once /goal geometry_msgs/msg/PoseStamped '{header: {frame_id: map}, pose: {position: {x: 10.0, y: 0.0, z: 5.0}, orientation: {w: 1.0}}}'\""
