#!/usr/bin/env bash
# One square-flight experiment on a running stack with casio-edge attached.
#   STACK=generated/<stack> square-flight.sh <label> [square_flight.py options]
# Needs the generated stack up, casio-edge attached (README "Run"); STACK is
# its folder (make fly prints it). PX4 flies
# a mission (take-off, laps of a square with the nose on its centre, land),
# so no planner is needed. While it flies this records casio's outputs,
# sim truth over AirSim RPC, the annotated stream, cloud_relay's POSTs
# (a stand-in RC3 on the docker bridge's port 3030) and annotated snapshots
# of targets at least SNAPSHOT_MIN_H px tall (default 40), then scores it.
# PERTURB="--fog 0.9" (any perturb.py options: --fog/--rain/--dust/--snow,
# --time) changes the sim's weather or light before the flight and resets it
# after; what was applied goes into perturb.json.
# Output: <stack>/outputs/flights/<time>-<label>/ (summary.md,
# map.png, timeline.png, annotated.mp4, snapshots/, the CSVs, rc3/posts/).
set -euo pipefail
label=${1:?usage: square-flight.sh <label> [square_flight.py options]}
shift
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../../.." && pwd)
: "${STACK:?set STACK=generated/<stack> (the folder make fly printed)}"
# Relative to the repository, or absolute (the run director's {stack}).
case "$STACK" in /*) stack=$(cd "$STACK" && pwd) ;; *) stack=$(cd "$repo/$STACK" && pwd) ;; esac
# The stack's own record of what it runs: its name (container prefix), and
# from its contract the network and ROS domain casio is on; whether the
# siyi-a8-realism component is attached.
read -r stack_name CASIO_STACK_NETWORK ros_domain realism < <(python3 - "$stack" <<'PY'
import json, sys
m = json.load(open(sys.argv[1] + "/generated-manifest.json"))
c = json.load(open(sys.argv[1] + "/config/stack/contract.json"))
v = c["vehicles"][0]
ids = [x["id"] for x in m.get("components") or []]
if "casio-edge" not in ids:
    sys.exit("this stack has no casio-edge component attached")
print(m["stack_name"], v["network"], v["ros_domain_id"], int("siyi-a8-realism" in ids))
PY
)
bridge_image=$(sed -n 's/^ROS2_IMAGE=//p' "$stack/.env")
casio_image=dhdevspace/auto_mns:casio-node
px4=$stack_name-px4-drone-1
run=$stack/outputs/flights/$(date +%Y%m%d-%H%M%S)-$label
mkdir -p "$run/rc3"
user="$(id -u):$(id -g)"
ros_env=(-e HOME=/tmp -e ROS_DOMAIN_ID=$ros_domain -e RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
         -e CYCLONEDDS_URI=file:///c.xml -v "$here/../cyclonedds.xml:/c.xml:ro")

# The sim has no RC; PX4 must not abandon the mission for its absence. And
# PX4 runs without lockstep here: when Unreal's frame time spikes, sensor
# data arrives late, the estimate's accuracy briefly crosses the default
# 5 m / 1 m/s position-loss limits, and PX4 lands for ~3 s mid-mission
# (8 times in one 5-minute run). Loosen those limits for sim runs only.
# PX4_FS_EPH / PX4_FS_EVH override; the values used go into px4_params.txt.
px4_param() { docker exec "$px4" /px4_workspace/PX4-Autopilot/build/px4_sitl_default/bin/px4-param "$@"; }
px4_param set COM_RCL_EXCEPT 4 >/dev/null
px4_param set COM_POS_FS_EPH "${PX4_FS_EPH:-20}" >/dev/null
px4_param set COM_VEL_FS_EVH "${PX4_FS_EVH:-5}" >/dev/null
for p in COM_RCL_EXCEPT COM_POS_FS_EPH COM_VEL_FS_EVH; do px4_param show "$p" | grep -E "^\s*x"; done > "$run/px4_params.txt"

perturb() {
  docker run --rm --network "$CASIO_STACK_NETWORK" --add-host host.docker.internal:host-gateway \
    -v "$here:/bench:ro" --entrypoint python3 "$bridge_image" /bench/perturb.py "$@"
}
perturb --reset >/dev/null
if [[ -n "${PERTURB:-}" ]]; then
  # shellcheck disable=SC2086  # PERTURB is a list of options
  perturb $PERTURB > "$run/perturb.json"
  sleep 5   # let the new light / particles settle before recording
fi

# Camera realism (the siyi-a8-realism component): keep what the camera looked like.
if [[ $realism == 1 ]]; then
  docker ps --format '{{.Names}}' | grep -qx "$stack_name-siyi-a8-realism-realism" \
    || { echo "this stack has camera realism but $stack_name-siyi-a8-realism-realism is not running" >&2; exit 1; }
  python3 -c 'import json,sys; s=json.load(open(sys.argv[1])); c=s["Vehicles"]["Drone1"]["Cameras"]["siyi"]["CaptureSettings"]; print(json.dumps({"capture_settings": c}, indent=1))' \
    "$stack/config/unreal-airsim/settings.json" > "$run/realism.json"
  docker logs "$stack_name-siyi-a8-realism-realism" 2>&1 | grep -m1 "camera_realism:" >> "$run/realism.json" || true
fi

# RC3 stand-in where host.docker.internal (host-gateway) lands: the docker0 address.
gateway=$(docker network inspect bridge --format '{{(index .IPAM.Config 0).Gateway}}')
python3 "$here/rc3_sink.py" --out "$run/rc3" --bind "$gateway" &
sink=$!
docker rm -f casio-flight-gt casio-flight casio-flight-snap >/dev/null 2>&1 || true
docker run -d --name casio-flight-gt --network "$CASIO_STACK_NETWORK" -u "$user" \
  --add-host host.docker.internal:host-gateway -v "$here:/bench:ro" -v "$run:/out" \
  --entrypoint python3 "$bridge_image" /bench/gt_objects.py --out /out >/dev/null
docker run -d --name casio-flight-snap --network "$CASIO_STACK_NETWORK" --ipc host -u "$user" \
  "${ros_env[@]}" -v "$here:/bench:ro" -v "$run:/out" --entrypoint bash "$casio_image" -c \
  "source /opt/ros/humble/setup.bash && source /ros2_ws/install/setup.bash && exec python3 /bench/snapshots.py --out /out --min-h ${SNAPSHOT_MIN_H:-40}" >/dev/null
# Fragmented while recording so an interrupted run still keeps its video;
# remuxed to a plain MP4 (index up front) at the end, which every player opens.
ffmpeg -loglevel error -rtsp_transport tcp -i rtsp://127.0.0.1:8554/annotated -c copy \
  -f mp4 -movflags +frag_keyframe+empty_moov "$run/annotated.part.mp4" </dev/null &
video=$!

cleanup() {
  kill -INT "$video" 2>/dev/null || true
  docker stop -t 5 casio-flight-gt casio-flight-snap >/dev/null 2>&1 || true
  docker rm -f casio-flight-gt casio-flight-snap >/dev/null 2>&1 || true
  kill "$sink" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup EXIT

rc=0
docker run --rm --name casio-flight --network "$CASIO_STACK_NETWORK" --ipc host -u "$user" \
  "${ros_env[@]}" -v "$here:/bench:ro" -v "$run:/out" --entrypoint bash "$casio_image" -c \
  'source /opt/ros/humble/setup.bash && source /ros2_ws/install/setup.bash && exec python3 /bench/square_flight.py --out /out "$@"' \
  _ "$@" || rc=$?
cleanup
trap - EXIT
[[ -n "${PERTURB:-}" ]] && perturb --reset >/dev/null
if [[ -s "$run/annotated.part.mp4" ]] && ffmpeg -loglevel error -i "$run/annotated.part.mp4" -c copy \
    -movflags +faststart -y "$run/annotated.mp4"; then
  rm -f "$run/annotated.part.mp4"
fi

docker run --rm -u "$user" -e HOME=/tmp -e MPLCONFIGDIR=/tmp -v "$here:/bench:ro" -v "$run:/out" \
  --entrypoint python3 "$casio_image" /bench/analyse_flight.py /out
echo "run: $run (flight exit $rc)"
exit "$rc"
