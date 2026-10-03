#!/bin/bash
# Replay a recorded stereo flight through MAC-VO + its adaptor, offline, and
# score the estimate against the bag's ground truth.
#
#   tools/replay.sh OUT_DIR BAG CONTRACT MACVO_YAML [MAX_HZ] [START_OFFSET_S] [RATE]
#
# Isolated docker network and ROS domain; nothing of a running stack is used.
set -euo pipefail
OUT=$(realpath -m "$1"); BAG=$(realpath "$2"); CONTRACT=$(realpath "$3"); CFG=$(realpath "$4")
HZ=${5:-10}; OFFSET=${6:-0}; RATE=${7:-1.0}
HERE=$(cd "$(dirname "$0")" && pwd)
MACVO=${MACVO_IMAGE:-dhdevspace/auto_mns:mac-vo-ros2-a22f89e-cu128}
ADAPTOR=${ADAPTOR_IMAGE:-dhdevspace/auto_mns:mac-vo-adaptor-0.1.0}
PLAYER=${PLAYER_IMAGE:-dhdevspace/auto_mns:tevv-airsim-ros2-bridge-humble-v1.0.0-rc}
NET=macvo-replay; DOMAIN=${REPLAY_DOMAIN:-77}; P=macvo-replay
mkdir -p "$OUT/work"; cp "$CFG" "$OUT/work/macvo.yaml"; chmod -R a+rwX "$OUT"
docker network inspect $NET >/dev/null 2>&1 || docker network create $NET >/dev/null
cleanup() { docker rm -f $P-macvo $P-adaptor $P-collect >/dev/null 2>&1 || true; }
trap cleanup EXIT; cleanup
ENV=(--network $NET -e ROS_DOMAIN_ID=$DOMAIN -e RMW_IMPLEMENTATION=rmw_fastrtps_cpp -e FASTDDS_BUILTIN_TRANSPORTS=UDPv4)
docker run -d --name $P-macvo --gpus all "${ENV[@]}" -v "$OUT/work:/work:ro" "$MACVO" \
  ros2 run MACVO_ROS2 MACVO --config /work/macvo.yaml >/dev/null
docker run -d --name $P-adaptor "${ENV[@]}" -v "$CONTRACT:/cfg/stack/contract.json:ro" \
  -e MNS_ESTIMATE_TOPIC=/mns/estimate/odom -e MNS_ADAPTOR_MAX_HZ=$HZ -e MNS_ADAPTOR_MAX_INFLIGHT=${MAX_INFLIGHT:-2} "$ADAPTOR" run >/dev/null
docker run -d --name $P-collect "${ENV[@]}" -v "$OUT:/out" -v "$HERE/collect.py:/collect.py:ro" \
  --entrypoint bash "$ADAPTOR" -c "source /opt/ros/humble/setup.bash && exec python3 /collect.py /out" >/dev/null
# MAC-VO is ready when its optimiser worker has started.
for i in $(seq 1 90); do docker logs $P-macvo 2>&1 | grep -q "OptimizationParallelWorker started" && break; sleep 2; done
sleep 3
docker run --rm "${ENV[@]}" -u "$(id -u):$(id -g)" -e HOME=/tmp -v "$BAG:/bag:ro" "$PLAYER" bash -lc \
  "source /opt/ros/humble/setup.bash && ros2 bag play /bag --rate $RATE --start-offset $OFFSET \
     --topics /camera/front/image_raw /camera/front_right/image_raw /ground_truth/odom" >/dev/null 2>&1
sleep 5
docker exec $P-adaptor bash -lc 'source /opt/ros/humble/setup.bash; timeout 5 ros2 topic echo --once /mns/component/mac_vo/status' \
  2>/dev/null | head -1 > "$OUT/status.txt" || true
docker kill -s INT $P-collect >/dev/null; sleep 2
docker logs $P-macvo > "$OUT/macvo.log" 2>&1 || true
python3 "$HERE/ate.py" "$OUT" | tee "$OUT/ate.txt"
