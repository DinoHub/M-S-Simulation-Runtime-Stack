#!/bin/bash
set -e
# Fast DDS in Humble parses ROS_DISCOVERY_SERVER as a locator and accepts
# only an IP; the pod DNS name OSMO hands us is rejected, silently.
DS_IP=$(getent hosts "$DS_HOST" | awk '{print $1; exit}')
[ -n "$DS_IP" ] || { echo "cannot resolve $DS_HOST" >&2; exit 42; }
export ROS_DISCOVERY_SERVER="${DS_IP}:11811"
echo "discovery server $DS_HOST -> $ROS_DISCOVERY_SERVER"
S="/workspace/generated/${STACK}/config"
# The topic map is generated per stack; never hand-write topic names.
cp "$S/topic_names.yaml" /tmp/topic_names.yaml

# MAVROS speaks to each flight stack in its own dialect: PX4 through its
# mavlink-router's MAVROS_UDP server on 14555 (not AirSim_Inbound on 14580,
# which AirSim floods), ArduPilot over TCP 5760.
case "${AUTOPILOT:-px4}" in
  ardupilot) MAVROS_CONFIG=mavros_ardupilot.yaml
             MAVROS_FCU_URL="tcp://${AUTOPILOT_HOST}:5760" ;;
  *)         MAVROS_CONFIG=mavros_px4.yaml
             MAVROS_FCU_URL="udp://:14556@${AUTOPILOT_HOST}:14555" ;;
esac

# A ready container is not an answering simulator. Without this the
# bridge reads settings over RPC before UE5 is listening, logs
# "Direct-RPC getSettingsString failed: Connection refused", and
# carries on with a partial topic remap that silently drops the
# camera and sensor renames.
deadline=$(( $(date +%s) + SIM_DIAL_WAIT_SEC ))
until (exec 3<>"/dev/tcp/${SIM_HOST}/${SIM_PORT}") 2>/dev/null; do
  if [ "$(date +%s)" -ge "$deadline" ]; then
    echo "READINESS TIMEOUT: ${SIM_HOST}:${SIM_PORT} unreachable after ${SIM_DIAL_WAIT_SEC}s" >&2
    exit 42
  fi
  sleep 5
done
echo "sim RPC ${SIM_HOST}:${SIM_PORT} reachable"
# Flags mirror the generated stack. enable_vio:=false and
# disable_rpc_aux_sensors:=false are what the generator sets when an
# estimator is present: images over RPC, the IMU at 200 Hz. host_ip:= is
# explicit because the image's entrypoint otherwise leaves 127.0.0.1 and
# the bridge looks for the simulator inside its own pod. ENABLE_MAVROS is
# false when the run does not fly.
exec /entrypoint.sh ros2 launch airsim_ros2_bridge single_vehicle.launch.py \
  host_ip:="${SIM_HOST}" \
  host_port:="${SIM_PORT}" \
  vehicle_name:="${VEHICLE}" \
  enable_vio:=false \
  auto_discover_cameras:=false \
  disable_rpc_aux_sensors:=false \
  poll_rate_hz:=30.0 \
  topic_prefix:=/ \
  topic_names_config:=/tmp/topic_names.yaml \
  enable_mavros:="${ENABLE_MAVROS}" \
  mavros_vehicle:="${VEHICLE}" \
  mavros_config:="${MAVROS_CONFIG}" \
  mavros_fcu_url:="${MAVROS_FCU_URL}" \
  enable_localization:=true \
  localization_source:=sim \
  enable_local_obs:=false \
  use_sim_time:=true
