#!/bin/bash
set -e
# Overriding `command` bypasses the image's /ros_entrypoint.sh, which
# is what would normally put ros2 on PATH.
source /opt/ros/humble/setup.bash
source /ws/install/setup.bash
# Fast DDS in Humble parses ROS_DISCOVERY_SERVER as a locator and accepts
# only an IP; the pod DNS name OSMO hands us is rejected, silently.
DS_IP=$(getent hosts "$DS_HOST" | awk '{print $1; exit}')
[ -n "$DS_IP" ] || { echo "cannot resolve $DS_HOST" >&2; exit 42; }
export ROS_DISCOVERY_SERVER="${DS_IP}:11811"
echo "discovery server $DS_HOST -> $ROS_DISCOVERY_SERVER"
# The calibration is frozen beside the scenario and staged into the
# generated stack; it is part of the run, not of the image.
exec ros2 launch ov_msckf subscribe.launch.py \
  config_path:="/workspace/generated/${STACK}/config/vio/estimator_config.yaml" \
  max_cameras:=2 use_stereo:=true
