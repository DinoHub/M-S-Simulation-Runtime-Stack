#!/usr/bin/env bash
set -euo pipefail

# Fly a single simulated vehicle through an already-running external MAVROS
# sidecar. This is an operator smoke-test helper, not a stack validator:
#   1. Start a stack with stacks/scripts/run_stack.sh.
#   2. Start MAVROS with stacks/tests/test_external_mavros.sh <stack> <index>.
#   3. Run this script to arm and take off through MAVROS.

usage() {
  cat <<'EOF'
Usage:
  scripts/fly_mavros_cli.sh <stack-dir-or-name> [vehicle-index] [options]

Examples:
  scripts/fly_mavros_cli.sh ardupilot-xfs-single
  scripts/fly_mavros_cli.sh ardupilot-xfs-multi 2 --alt 5
  scripts/fly_mavros_cli.sh px4-xfs-single
  scripts/fly_mavros_cli.sh px4-xfs-multi 3 --hold 20

Options:
  --vehicle NAME      Override vehicle name, for example Copter1 or Drone2.
  --container NAME    Override external MAVROS container name.
  --alt METERS        Takeoff/setpoint altitude. Default: 5.
  --hold SECONDS      PX4 setpoint hold time after arming. Default: 20.
  -h, --help          Show this help.

Notes:
  The expected MAVROS sidecar name is:
    mavros-<stack-name>-<lowercase-vehicle-name>

  This script expects that sidecar to already be running. Start it with:
    stacks/tests/test_external_mavros.sh <stack-name> <vehicle-index>
EOF
}

die() {
  echo "ERROR: $*" >&2
  exit 1
}

if [[ $# -lt 1 || "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

STACK_ARG="$1"
shift

VEHICLE_INDEX="1"
if [[ $# -gt 0 && "$1" =~ ^[0-9]+$ ]]; then
  VEHICLE_INDEX="$1"
  shift
fi

ALTITUDE="5"
HOLD_SECONDS="20"
VEHICLE_NAME=""
MAVROS_CONTAINER=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --vehicle)
      [[ $# -ge 2 ]] || die "--vehicle requires a value"
      VEHICLE_NAME="$2"
      shift 2
      ;;
    --container)
      [[ $# -ge 2 ]] || die "--container requires a value"
      MAVROS_CONTAINER="$2"
      shift 2
      ;;
    --alt)
      [[ $# -ge 2 ]] || die "--alt requires a value"
      ALTITUDE="$2"
      shift 2
      ;;
    --hold)
      [[ $# -ge 2 ]] || die "--hold requires a value"
      HOLD_SECONDS="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown option: $1"
      ;;
  esac
done

if [[ "$VEHICLE_INDEX" -lt 1 ]]; then
  die "vehicle-index must be 1 or greater"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STACKS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ -d "$STACK_ARG" ]]; then
  STACK_DIR="$(cd "$STACK_ARG" && pwd)"
elif [[ -d "$STACKS_DIR/$STACK_ARG" ]]; then
  STACK_DIR="$(cd "$STACKS_DIR/$STACK_ARG" && pwd)"
else
  die "stack directory does not exist: $STACK_ARG"
fi

STACK_NAME="$(basename "$STACK_DIR")"

case "$STACK_NAME" in
  ardupilot-*)
    AUTOPILOT="ardupilot"
    VEHICLE_NAME="${VEHICLE_NAME:-Copter${VEHICLE_INDEX}}"
    ;;
  px4-*-single)
    AUTOPILOT="px4"
    VEHICLE_NAME="${VEHICLE_NAME:-Copter${VEHICLE_INDEX}}"
    ;;
  px4-*-multi)
    AUTOPILOT="px4"
    VEHICLE_NAME="${VEHICLE_NAME:-Drone${VEHICLE_INDEX}}"
    ;;
  *)
    die "unsupported stack name: $STACK_NAME"
    ;;
esac

MAVROS_CONTAINER="${MAVROS_CONTAINER:-mavros-${STACK_NAME}-${VEHICLE_NAME,,}}"

if ! docker ps --format '{{.Names}}' | grep -Fxq "$MAVROS_CONTAINER"; then
  die "MAVROS sidecar is not running: $MAVROS_CONTAINER"
fi

ros_exec() {
  local cmd="$1"
  docker exec -i "$MAVROS_CONTAINER" bash -lc "
    set -e
    source /opt/ros/humble/setup.bash
    source /ws/install/setup.bash
    $cmd
  "
}

wait_connected() {
  echo "Waiting for /${VEHICLE_NAME}/mavros/state connected=true..."
  ros_exec "
    for attempt in \$(seq 1 20); do
      if timeout 6 bash -lc 'ROS_DISABLE_DAEMON=1 ros2 topic echo \
          --qos-reliability reliable \
          --qos-durability transient_local \
          /${VEHICLE_NAME}/mavros/state \
          mavros_msgs/msg/State 2>/dev/null | grep -m1 -q \"connected: true\"'; then
        echo 'connected: true'
        exit 0
      fi
      echo \"WAIT: ${VEHICLE_NAME} MAVROS connected state (attempt \$attempt/20)\"
      sleep 1
    done

    exit 1
  "
}

fly_ardupilot() {
  wait_connected
  ros_exec "
    echo 'Setting GUIDED mode...'
    ros2 service call /${VEHICLE_NAME}/mavros/set_mode mavros_msgs/srv/SetMode \
      \"{custom_mode: GUIDED}\"

    sleep 1
    echo 'Arming...'
    ros2 service call /${VEHICLE_NAME}/mavros/cmd/arming mavros_msgs/srv/CommandBool \
      \"{value: true}\"

    sleep 1
    echo 'Taking off to ${ALTITUDE} m...'
    ros2 service call /${VEHICLE_NAME}/mavros/cmd/takeoff mavros_msgs/srv/CommandTOL \
      \"{min_pitch: 0.0, yaw: 0.0, latitude: 0.0, longitude: 0.0, altitude: ${ALTITUDE}}\"

    echo 'Latest local pose:'
    timeout 8 ros2 topic echo --once /${VEHICLE_NAME}/mavros/local_position/pose \
      geometry_msgs/msg/PoseStamped || true
  "
}

fly_px4() {
  wait_connected
  ros_exec "
    setpoint_msg='{header: {frame_id: map}, pose: {position: {x: 0.0, y: 0.0, z: ${ALTITUDE}}, orientation: {w: 1.0}}}'

    echo 'Starting PX4 OFFBOARD setpoint stream...'
    ros2 topic pub --rate 10 /${VEHICLE_NAME}/mavros/setpoint_position/local \
      geometry_msgs/msg/PoseStamped \
      \"\$setpoint_msg\" >/tmp/${VEHICLE_NAME}_setpoint.log 2>&1 &
    setpoint_pid=\$!

    cleanup_setpoint() {
      kill \"\$setpoint_pid\" >/dev/null 2>&1 || true
      wait \"\$setpoint_pid\" >/dev/null 2>&1 || true
    }
    trap cleanup_setpoint EXIT

    sleep 3

    echo 'Setting OFFBOARD mode...'
    ros2 service call /${VEHICLE_NAME}/mavros/set_mode mavros_msgs/srv/SetMode \
      \"{custom_mode: OFFBOARD}\"

    sleep 1
    echo 'Arming...'
    ros2 service call /${VEHICLE_NAME}/mavros/cmd/arming mavros_msgs/srv/CommandBool \
      \"{value: true}\"

    echo 'Holding ${ALTITUDE} m setpoint for ${HOLD_SECONDS}s...'
    sleep ${HOLD_SECONDS}

    echo 'Latest MAVROS state:'
    timeout 8 ros2 topic echo --once --qos-reliability reliable --qos-durability transient_local \
      /${VEHICLE_NAME}/mavros/state mavros_msgs/msg/State || true
    echo 'Latest local pose:'
    timeout 8 ros2 topic echo --once /${VEHICLE_NAME}/mavros/local_position/pose \
      geometry_msgs/msg/PoseStamped || true
  "
}

echo "========================================"
echo " MAVROS CLI flight smoke test"
echo "========================================"
echo "STACK_NAME=$STACK_NAME"
echo "AUTOPILOT=$AUTOPILOT"
echo "VEHICLE_INDEX=$VEHICLE_INDEX"
echo "VEHICLE_NAME=$VEHICLE_NAME"
echo "MAVROS_CONTAINER=$MAVROS_CONTAINER"
echo "ALTITUDE=$ALTITUDE"
echo

case "$AUTOPILOT" in
  ardupilot)
    fly_ardupilot
    ;;
  px4)
    fly_px4
    ;;
esac
