# Bridge v1.0.0 with the fisheye image subscriber from TEVV-Airsim-ROS2-Bridge #75 (drop frames
# the sim re-sent with the same capture stamp) and #76 (publish the lens the sim renders).
# Stopgap until those PRs ship in a bridge release; build with build.sh.
FROM dhdevspace/auto_mns:tevv-airsim-ros2-bridge-humble-v1.0.0@sha256:b91e4b92d6a8ec538da33ab40758ab3d1c0af99cb9bdf853452561e44046530c
COPY iceoryx_image_subscriber /ws/install/airsim_iceoryx_transport/lib/airsim_iceoryx_transport/iceoryx_image_subscriber
