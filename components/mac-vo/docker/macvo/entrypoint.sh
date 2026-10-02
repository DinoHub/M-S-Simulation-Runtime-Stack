#!/bin/bash
# ROS 2 and the MAC-VO workspace, then whatever command the stack gives.
set -e
source /opt/ros/humble/setup.bash
source /opt/ws/install/setup.bash
exec "$@"
