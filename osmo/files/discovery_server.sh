#!/bin/bash
set -e
source /opt/ros/humble/setup.bash
exec fastdds discovery -i 0 -l 0.0.0.0 -p 11811
