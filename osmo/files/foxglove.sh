#!/bin/bash
set -o pipefail
source /opt/ros/humble/setup.bash
source /ws/install/setup.bash
DS_IP=$(getent hosts "$DS_HOST" | awk '{print $1; exit}')
[ -n "$DS_IP" ] || { echo "cannot resolve $DS_HOST" >&2; exit 0; }
cat > /tmp/super_client.xml <<XML
<?xml version="1.0" encoding="UTF-8" ?>
<dds>
  <profiles xmlns="http://www.eprosima.com/XMLSchemas/fastRTPS_Profiles">
    <participant profile_name="super_client" is_default_profile="true">
      <rtps>
        <builtin>
          <discovery_config>
            <discoveryProtocol>SUPER_CLIENT</discoveryProtocol>
            <discoveryServersList>
              <RemoteServer prefix="44.53.00.5f.45.50.52.4f.53.49.4d.41">
                <metatrafficUnicastLocatorList>
                  <locator><udpv4><address>${DS_IP}</address><port>11811</port></udpv4></locator>
                </metatrafficUnicastLocatorList>
              </RemoteServer>
            </discoveryServersList>
          </discovery_config>
        </builtin>
      </rtps>
    </participant>
  </profiles>
</dds>
XML
export FASTRTPS_DEFAULT_PROFILES_FILE=/tmp/super_client.xml
unset ROS_DISCOVERY_SERVER
echo "foxglove_bridge on ws://0.0.0.0:8765, super client of ${DS_IP}:11811"
# send_buffer_limit: a slow tunnel drops frames rather than stalling
# the bridge; raw 640x480 bgr8 is ~27 MB/s per camera.
# How many people are watching, for the recorder's hold (viewer_count.py).
python3 /tmp/viewer_count.py &
ros2 launch foxglove_bridge foxglove_bridge_launch.xml \
  port:=8765 address:=0.0.0.0 use_sim_time:=true \
  send_buffer_limit:=50000000
exit 0
