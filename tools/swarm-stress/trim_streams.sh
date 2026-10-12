#!/usr/bin/env bash
# trim_streams.sh <compose project>
#
# Lower every PX4's onboard MAVLink stream rates at runtime (a swarm
# profile). The PX4 image's px4-rc.mavlink asks for 100 Hz HIGHRES_IMU,
# ATTITUDE, ATTITUDE_QUATERNION and LOCAL_POSITION_NED and 50 Hz for five
# more, about 670 messages a second into MAVROS per vehicle. This sets
# 20 Hz for those four, 10 Hz global position, 2-5 Hz targets, altitude,
# GPS, VFR_HUD and system state, and turns servo outputs, RC channels and
# optical flow off. The rates last until the PX4 container restarts.
proj=$1
streams="HIGHRES_IMU:20 ATTITUDE:20 ATTITUDE_QUATERNION:20 LOCAL_POSITION_NED:20 GLOBAL_POSITION_INT:10 ATTITUDE_TARGET:5 POSITION_TARGET_LOCAL_NED:5 ALTITUDE:5 SERVO_OUTPUT_RAW_0:0 GPS_RAW_INT:5 NAV_CONTROLLER_OUTPUT:2 RC_CHANNELS:0 VFR_HUD:2 OPTICAL_FLOW_RAD:0"
for c in $(docker ps --format '{{.Names}}' | grep "^${proj}-px4-drone-"); do
  i=$(( ${c##*-} - 1 ))
  cmds=""; for s in $streams; do cmds+="./bin/px4-mavlink --instance $i stream -u $((18570 + i)) -s ${s%%:*} -r ${s##*:}; "; done
  docker exec "$c" bash -lc "cd /px4_workspace/PX4-Autopilot/build/px4_sitl_default; $cmds" >/dev/null 2>&1 &
done
wait
