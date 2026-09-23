#!/bin/bash
# No `set -u`: ROS's setup.bash reads unset variables.
set -o pipefail
source /opt/ros/humble/setup.bash
source /ws/install/setup.bash

# Fast DDS parses this as a locator and accepts only an address.
DS_IP=$(getent hosts "$DS_HOST" | awk '{print $1; exit}')
[ -n "$DS_IP" ] || { echo "cannot resolve $DS_HOST" >&2; exit 42; }
export ROS_DISCOVERY_SERVER="${DS_IP}:11811"
echo "discovery server $DS_HOST -> $ROS_DISCOVERY_SERVER"

# sim_real_eval discovers runs by globbing run_* directories and
# finding a rosbag2 inside (tools/sim_real_eval/manifest.py).
python3 /tmp/record.py
rc=$?
ls -la "${OUT_DIR}/bag" 2>/dev/null
exit $rc
