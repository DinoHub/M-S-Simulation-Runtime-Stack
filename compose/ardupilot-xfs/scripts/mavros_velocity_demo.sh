#!/usr/bin/env bash
# Sanity-test the bridge-network multi-drone stack:
#  - Launches 4 MAVROS instances pointing at ardupilot-drone-N:5760 (TCP, via bridge DNS)
#  - For each Copter, sets GUIDED mode → ARM → TAKEOFF 5m → publishes a forward velocity
#    setpoint for 8 seconds → LAND.
#
# Run from inside ardupilot-xfs-ros2:
#   docker exec -it ardupilot-xfs-ros2 bash -lc 'bash /scripts/mavros_velocity_demo.sh'
# (Or mount this dir into the ros2 container.)

set -euo pipefail
source /opt/ros/humble/setup.bash
source /airsim_ros2_ws/install/setup.bash

VEHICLES=(Copter1 Copter2 Copter3 Copter4)

echo "=== launching MAVROS x4 ==="
for i in 0 1 2 3; do
  v="${VEHICLES[$i]}"
  port=$((5760 + 10*i))
  # On bridge: SITL is at the Docker service name `ardupilot-drone-N`
  # On host-net: 127.0.0.1 works.
  fcu_host=$([ -n "${BRIDGE_MODE:-}" ] && echo "ardupilot-drone-$i" || echo "127.0.0.1")
  cleanup=$([ "$i" = "0" ] && echo true || echo false)
  echo "  $v -> tcp://$fcu_host:$port"
  # SITL run_ardupilot_airsim.sh sets SYSID_THISMAV = instance_num + 1, so
  # MAVROS must be told to address the matching system_id or commands tagged
  # for system 1 are ignored by SITLs 2/3/4. This mismatch caused Copter2-4
  # to never take off in earlier tests despite "armed:true mode:GUIDED".
  sysid=$((i + 1))
  nohup ros2 launch airsim_ros_pkgs mavros_bringup.launch.py \
    vehicle:=$v \
    fcu_url:=tcp://$fcu_host:$port \
    target_system_id:=$sysid \
    mavros_config:=mavros_ardupilot.yaml \
    enable_dds_cleanup:=$cleanup \
    > /tmp/mavros_${v}.log 2>&1 &
  sleep 2
done

echo "=== waiting 15s for MAVROS+FCU connection (looking for /Copter*/mavros/state IS connected) ==="
sleep 15

for v in "${VEHICLES[@]}"; do
  echo -n "$v connected: "
  timeout 3 ros2 topic echo /$v/mavros/state --once 2>/dev/null | grep -E "^connected:" | head -1 || echo "unknown"
done

echo
echo "=== flight test loop (GUIDED -> ARM -> TAKEOFF -> velocity setpoint -> LAND) ==="
for v in "${VEHICLES[@]}"; do
  echo "--- $v ---"

  echo "  set_mode GUIDED"
  ros2 service call /$v/mavros/set_mode mavros_msgs/srv/SetMode \
    "{base_mode: 0, custom_mode: 'GUIDED'}" >/dev/null

  echo "  arm"
  ros2 service call /$v/mavros/cmd/arming mavros_msgs/srv/CommandBool \
    "{value: true}" >/dev/null
  sleep 2

  echo "  takeoff 5m"
  ros2 service call /$v/mavros/cmd/takeoff mavros_msgs/srv/CommandTOL \
    "{min_pitch: 0.0, yaw: 0.0, latitude: 0.0, longitude: 0.0, altitude: 5.0}" >/dev/null
  sleep 8

  echo "  publish 1 m/s forward velocity for 8s"
  # Use ros2 topic pub with --rate so MAVROS sees a constant stream (required to stay in GUIDED)
  timeout 8 ros2 topic pub --rate 20 /$v/mavros/setpoint_velocity/cmd_vel_unstamped \
    geometry_msgs/msg/Twist \
    '{linear: {x: 1.0, y: 0.0, z: 0.0}, angular: {x: 0.0, y: 0.0, z: 0.0}}' \
    >/dev/null 2>&1 || true

  echo "  land"
  ros2 service call /$v/mavros/cmd/land mavros_msgs/srv/CommandTOL \
    "{min_pitch: 0.0, yaw: 0.0, latitude: 0.0, longitude: 0.0, altitude: 0.0}" >/dev/null
  sleep 2
done

echo
echo "=== final state ==="
for v in "${VEHICLES[@]}"; do
  echo -n "$v: "
  timeout 2 ros2 topic echo /$v/mavros/state --once 2>/dev/null | grep -E "^(armed|mode):" | tr "\n" " "
  echo
done
echo "(logs: /tmp/mavros_Copter*.log)"
