#!/usr/bin/env bash
# A/B the casio-siyi camera rate for one bridge image.
#   bench.sh <label> <bridge image>
# Recreates only the bridge with that image (same host compose invocation
# every time), waits for /camera/image_raw, then takes 3 x 20 s rate windows
# while sampling load, bridge/unreal CPU and GPU utilisation.
# Appends one CSV row per window to bench.csv beside this script.
set -u
label=$1 image=$2
here=$(cd "$(dirname "$0")" && pwd)
G=/home/mnsuser/M-S-Simulation-Runtime-Stack/generated/casio-siyi
W=/home/mnsuser/M-S-Simulation-Runtime-Stack/.worktrees/casio/scenarios/casio-siyi/casio
P=px4-safti-level-casio-perception-on-a-siyi-a8-mini-single
BR=$P-airsim-bridge-d1 UE=$P-unreal-airsim
source "$W/.env"
csv="${BENCH_CSV:-$here/camera-rate.csv}"
[[ -f "$csv" ]] || echo "time,label,image,window,rate_hz,load1,bridge_cpu,unreal_cpu,gpu_util,pulling" > "$csv"

sed -i "s|^ROS2_IMAGE=.*|ROS2_IMAGE=$image|" "$G/.env"
(cd "$G" && XAUTHORITY=/run/user/1000/gdm/Xauthority MNS_IMAGE_PULL_POLICY=never \
  docker compose -p "$P" up -d --no-deps --force-recreate airsim_bridge_d1 >/dev/null 2>&1)

docker rm -f casio-bench >/dev/null 2>&1
docker run -d --name casio-bench --network "$CASIO_STACK_NETWORK" --ipc host -e HOME=/tmp \
  -e ROS_DOMAIN_ID=42 -e RMW_IMPLEMENTATION=rmw_cyclonedds_cpp -e CYCLONEDDS_URI=file:///c.xml \
  -v "$W/cyclonedds.xml:/c.xml:ro" --entrypoint sleep \
  dhdevspace/auto_mns:tevv-airsim-ros2-bridge-humble-v1.0.0 infinity >/dev/null
r() { docker exec casio-bench bash -c "source /opt/ros/humble/setup.bash; $1"; }
for i in $(seq 1 40); do r 'ros2 topic list' 2>/dev/null | grep -q /camera/image_raw && break; sleep 3; done
sleep 15   # let the bridge settle past its first frames

for w in 1 2 3; do
  r 'timeout 20 ros2 topic hz /camera/image_raw' > /tmp/bench_hz.txt 2>&1 &
  hzpid=$!
  sleep 10
  stats=$(docker stats --no-stream --format '{{.Name}} {{.CPUPerc}}' "$BR" "$UE")
  bcpu=$(awk -v n="$BR" '$1==n{print $2}' <<<"$stats")
  ucpu=$(awk -v n="$UE" '$1==n{print $2}' <<<"$stats")
  gpu=$(nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits | head -1)
  load=$(cut -d' ' -f1 /proc/loadavg)
  pulling=$(pgrep -x docker -a | grep -c ' pull ' || true)
  wait $hzpid
  rate=$(grep average /tmp/bench_hz.txt | tail -1 | awk '{print $3}')
  echo "$(date +%T),$label,$image,$w,${rate:-NA},$load,$bcpu,$ucpu,$gpu,$pulling" | tee -a "$csv"
done
docker rm -f casio-bench >/dev/null
