#!/usr/bin/env bash
# Launch 4 MAVROS namespaces (/Copter{1..4}/mavros) inside the running ardupilot-xfs-ros2 container.
# Run after `docker compose up` once /Copter1, /Copter2, /Copter3, /Copter4 nodes are visible
# in `ros2 node list`.

set -euo pipefail

CONTAINER="${CONTAINER:-ardupilot-xfs-ros2}"

if ! docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  echo "container '$CONTAINER' is not running" >&2
  exit 1
fi

docker exec "$CONTAINER" bash -lc '
  set -e
  source /opt/ros/humble/setup.bash
  source /airsim_ros2_ws/install/setup.bash
  for i in 0 1 2 3; do
    vehicle="Copter$((i+1))"
    port=$((5760 + 10*i))
    cleanup=$([ "$i" = "0" ] && echo true || echo false)
    echo "[launch_4_mavros] starting MAVROS for $vehicle on tcp://127.0.0.1:$port (cleanup=$cleanup)"
    nohup ros2 launch airsim_ros_pkgs mavros_bringup.launch.py \
      vehicle:=$vehicle \
      fcu_url:=tcp://127.0.0.1:$port \
      mavros_config:=mavros_ardupilot.yaml \
      enable_dds_cleanup:=$cleanup \
      > /tmp/mavros_${vehicle}.log 2>&1 &
    sleep 2
  done
  echo "[launch_4_mavros] all 4 MAVROS launchers fired; logs in /tmp/mavros_Copter*.log"
'
