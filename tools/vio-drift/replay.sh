#!/bin/bash
# replay.sh BAG_DIR VIO_CONFIG_DIR TAG N: re-run OpenVINS N times on a recorded stereo flight.
set -u
BAG=$1 CFG=$2 TAG=$3 N=${4:-3}
D=$(cd "$(dirname "$0")" && pwd); OUTROOT=${OUTROOT:-$PWD/vio-drift-replays}
OV=${OV_IMAGE:-dhdevspace/auto_mns:vio-estimator-openvins-69488123}
PLAYER=${PLAYER_IMAGE:-dhdevspace/auto_mns:tevv-airsim-ros2-bridge-humble-v1.0.0}
OUT=$OUTROOT/$TAG; mkdir -p $OUT
cat > $OUT/qos.yaml <<'Q'
/imu/data: {reliability: reliable, durability: volatile, history: keep_last, depth: 1000}
/camera/front/image_raw: {reliability: reliable, durability: volatile, history: keep_last, depth: 100}
/camera/front_right/image_raw: {reliability: reliable, durability: volatile, history: keep_last, depth: 100}
/ground_truth/odom: {reliability: reliable, durability: volatile, history: keep_last, depth: 100}
Q
E=(-e ROS_DOMAIN_ID=${DOMAIN:-23} -e ROS_LOCALHOST_ONLY=0 -e HOME=/tmp -e FASTDDS_BUILTIN_TRANSPORTS=UDPv4 -e USE_SIM_TIME=true -e RMW_IMPLEMENTATION=rmw_fastrtps_cpp)
net=drift-replay; docker network inspect $net >/dev/null 2>&1 || docker network create $net >/dev/null
for i in $(seq 1 $N); do
  for c in ov rec play; do docker rm -f drift-$c >/dev/null 2>&1; done
  rm -rf $OUT/rep$i
  docker run -d --name drift-ov --network $net --user $(id -u):$(id -g) "${E[@]}" -v $CFG:/cfg/vio:ro $OV \
    ros2 launch ov_msckf subscribe.launch.py config_path:=/cfg/vio/estimator_config.yaml max_cameras:=2 use_stereo:=true >/dev/null
  docker run -d --name drift-rec --network $net --user $(id -u):$(id -g) "${E[@]}" -v $OUT:/out $PLAYER \
    bash -lc "source /opt/ros/humble/setup.bash; ros2 bag record -s mcap -o /out/rep$i /ov_msckf/odomimu /ground_truth/odom" >/dev/null
  sleep 8
  docker run --name drift-play --network $net --user $(id -u):$(id -g) "${E[@]}" -v $BAG:/bag:ro -v $OUT:/out $PLAYER \
    bash -lc "source /opt/ros/humble/setup.bash; ros2 bag play /bag --qos-profile-overrides-path /out/qos.yaml --topics /clock /imu/data /camera/front/image_raw /camera/front_right/image_raw /camera/front/camera_info /camera/front_right/camera_info /ground_truth/odom" > $OUT/play$i.log 2>&1
  sleep 3
  docker stop -t 10 drift-rec >/dev/null; docker logs drift-ov > $OUT/ov$i.log 2>&1
  docker rm -f drift-ov drift-rec drift-play >/dev/null 2>&1
  echo "replay $i: $(python3 $D/score_bag.py $OUT/rep$i 2>&1 | sed 's/^rep[0-9]* *//')" | tee -a $OUT/scores.txt
done
