#!/usr/bin/env bash
# Sim-to-real gap tests on the fisheye VIO rig: generate a stack from a committed
# ScenarioSpec, fly a route under a lighting / weather condition, record the bag,
# replay OpenVINS on it and score it against ground truth.
#
#   tools/gap-tests/gap.sh stack   SCENARIO                 # generate generated/gap-tests/SCENARIO/stack
#   tools/gap-tests/gap.sh up      SCENARIO                 # start it, wait for the cameras
#   tools/gap-tests/gap.sh down    SCENARIO
#   tools/gap-tests/gap.sh fly     SCENARIO OUT [--time H] [--weather W] [--flare on|off]
#                                  [--veil on|off] [--route FILE.b64] [--chase]
#   tools/gap-tests/gap.sh replay  OUT TAG [--config t2|t2zc] [--dump]
#   tools/gap-tests/gap.sh video   OUT TAG OUT.mp4 [--title TEXT]
#   tools/gap-tests/gap.sh cases   SCENARIO CASES_FILE OUT  # fly + replay every case in a file
#   tools/gap-tests/gap.sh sweep   ARGS...                  # lighting_sweep.py (stack must be up)
#
# SCENARIO is a directory under scenarios/ (gap-fisheye-xfs, gap-fisheye-safti). Each holds
# ScenarioSpec.yaml, overlay.json (what the generator cannot express yet: runtime images,
# the real-sun keys, bridge switches), routes/ and openvins/<config>/.
#
# One flight holds the GPU, the simulator ports and the X display; run one at a time.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
GEN_IMAGE=${GEN_IMAGE:-dhdevspace/auto_mns:mns-stack-generator-v1.0.1}
OV_IMAGE=${OV_IMAGE:-dhdevspace/auto_mns:vio-estimator-openvins-69488123}
METRICS_IMAGE=${METRICS_IMAGE:-tevv-metrics:humble}
METRICS_DIR=${METRICS_DIR:-$HOME/tevv_ws/metrics}
PILOT=${PILOT:-$ROOT/osmo/files/fly_mission_mavros.py}
PACK_STORE=${MNS_PACK_STORE_ROOT:-$ROOT/.mns/v1/pack-store}
ROS="source /opt/ros/humble/setup.bash >/dev/null 2>&1; source /ws/install/setup.bash >/dev/null 2>&1"
TOPICS="/clock /imu/data /ground_truth/odom /tf_static /fisheye_front/image_raw /fisheye_back/image_raw /fisheye_front/camera_info /fisheye_back/camera_info"
export LIGHT_MASK_DIR=${LIGHT_MASK_DIR:-$HERE/masks}

die(){ echo "gap: $*" >&2; exit 1; }
scn_dir(){ local d="$ROOT/scenarios/$1"; [ -f "$d/ScenarioSpec.yaml" ] || die "no scenarios/$1/ScenarioSpec.yaml"; echo "$d"; }
stack_dir(){ echo "$ROOT/generated/gap-tests/$1/stack"; }
dc(){ local s; s=$(stack_dir "$1"); shift
  docker compose --project-directory "$s" --env-file "$s/.env" -f "$s/docker-compose.yml" "$@"; }
# down leaves bridge subscriber nodes in the host-wide iceoryx2 state; the next sim then
# fails to create its publishers. Only safe when no stack is running.
clean_iox(){ rm -rf /tmp/iceoryx2/nodes/* /tmp/iceoryx2/services/* 2>/dev/null; rm -f /dev/shm/iox2_* 2>/dev/null; true; }
bridge(){ dc "$1" ps --format '{{.Name}}' | grep -E 'airsim-bridge' | head -1; }

cmd_stack(){ # SCENARIO
  local s=${1:?scenario} d out; d=$(scn_dir "$s"); out=$(stack_dir "$s")
  [ -d "$PACK_STORE" ] || die "pack store $PACK_STORE missing: run ./download-packs.sh"
  mkdir -p "$(dirname "$out")"; rm -rf "$out"
  # Identical-path mount: the compose file the generator writes then names host paths, so
  # the host daemon can resolve every bind mount (a /workspace path would not exist there).
  # One mount over checkout and pack store, because the generator hard-links pack payloads
  # into the stack and link() fails across mount points.
  local mnt; mnt=$(python3 -c 'import os,sys; print(os.path.commonpath(sys.argv[1:]))' "$ROOT" "$PACK_STORE")
  docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -v "$mnt:$mnt" -w "$ROOT" \
    -e MNS_WORKSPACE_ROOT="$ROOT" -e MNS_PACK_STORE_ROOT="$PACK_STORE" \
    -e MNS_RUNTIME_HOST_COMPATIBILITY_CONTRACT="$ROOT/packs/runtime-host-compatibility.v1.json" \
    "$GEN_IMAGE" generate "$d/ScenarioSpec.yaml" --profile docker --image-set v1 \
    --image-set-file "$ROOT/images/image-set.generated.yaml" --out "$out" 2>&1 | grep -v 'cooked against base release' \
    | grep -vE '^(Run|Logs|Stop): '
  [ -f "$out/docker-compose.yml" ] || die "generation failed for $s"
  python3 - "$d/overlay.json" "$out" <<'PY'
import json, os, sys
ov_path, out = sys.argv[1], sys.argv[2]
ov = json.load(open(ov_path)) if os.path.exists(ov_path) else {}
def merge(a, b):
    for k, v in b.items():
        a[k] = merge(a.get(k, {}), v) if isinstance(v, dict) and isinstance(a.get(k), dict) else v
    return a
sp = f"{out}/config/unreal-airsim/settings.json"
s = json.load(open(sp)); merge(s, ov.get("settings", {}))
json.dump(s, open(sp, "w"), indent=2)
env = ov.get("env", {})
lines = [l for l in open(f"{out}/.env").read().splitlines() if l.split("=", 1)[0] not in env]
lines += [f"{k}={v}" for k, v in env.items()]
open(f"{out}/.env", "w").write("\n".join(lines) + "\n")
print(f"overlay: {len(ov.get('settings', {}))} settings key(s), {len(env)} env key(s)")
PY
  echo "stack: $out"
}

cmd_up(){ # SCENARIO
  local s=${1:?scenario} b _
  [ -f "$(stack_dir "$s")/docker-compose.yml" ] || cmd_stack "$s" >/dev/null || die "stack failed"
  dc "$s" down --remove-orphans >/dev/null 2>&1; clean_iox
  dc "$s" up -d >/dev/null 2>&1 || die "compose up failed (see: docker compose ... logs)"
  b=$(bridge "$s"); [ -n "$b" ] || die "no bridge container"
  for _ in $(seq 1 120); do
    docker exec "$b" bash -lc "$ROS; timeout 8 ros2 topic echo --once /fisheye_front/camera_info >/dev/null 2>&1" && { echo "$b"; return 0; }
    sleep 5
  done
  die "cameras never published (bridge $b)"
}

cmd_down(){ dc "${1:?scenario}" down --remove-orphans >/dev/null 2>&1; clean_iox; echo "down"; }

cmd_fly(){ # SCENARIO OUT [opts]
  local s=${1:?scenario} out=${2:?out dir}; shift 2
  local t=- w=level fl=on veil=on chase=0 route d b
  d=$(scn_dir "$s"); route=$(ls "$d"/routes/*.b64 | head -1)
  while [ $# -gt 0 ]; do case $1 in
    --time) t=$2; shift 2;; --weather) w=$2; shift 2;; --flare) fl=$2; shift 2;; --veil) veil=$2; shift 2;;
    --route) route=$2; shift 2;; --chase) chase=1; shift;; *) die "fly: unknown option $1";; esac; done
  mkdir -p "$out"; out=$(cd "$out" && pwd)
  printf 'scenario=%s time=%s weather=%s flare=%s veil=%s route=%s\n' "$s" "$t" "$w" "$fl" "$veil" "$(basename "$route")" > "$out/condition.txt"
  b=$(cmd_up "$s") || exit 1
  local args=(set --flare "$fl" --veil "$veil"); [ "$w" != "-" ] && args+=(--weather "$w"); [ "$t" != "-" ] && args+=(--time "$t")
  python3 "$HERE/lighting_sweep.py" "${args[@]}"
  python3 "$HERE/lighting_sweep.py" snapshot --out "$out/snap" --veil "$veil"
  sleep "${SETTLE_S:-10}"
  local hz; hz=$(docker exec "$b" bash -lc "$ROS; timeout 15 ros2 topic hz /fisheye_front/image_raw 2>/dev/null" | grep -m1 'average rate' || true)
  echo "camera rate: ${hz:-none}"
  local fp=""
  if [ "$chase" = 1 ]; then
    local ff geo size abs ax ay w_ h_
    ff=$(python3 -c "import imageio_ffmpeg as f; print(f.get_ffmpeg_exe())" 2>/dev/null) || die "--chase needs pip install imageio-ffmpeg"
    geo=$(xwininfo -display "${DISPLAY:-:0}" -root -tree | grep '("TEVVRuntimeHost" "TEVVRuntimeHost")' | head -1 | grep -oE '[0-9]+x[0-9]+\+-?[0-9]+\+-?[0-9]+ +\+-?[0-9]+\+-?[0-9]+')
    size=$(echo "$geo" | awk '{print $1}' | cut -d+ -f1); abs=$(echo "$geo" | awk '{print $2}')
    ax=$(echo "$abs" | cut -d+ -f2); ay=$(echo "$abs" | cut -d+ -f3)
    w_=$(( ${size%x*} / 2 * 2 )); h_=$(( ${size#*x} / 2 * 2 ))
    date +%s.%N > "$out/chase_start_wall.txt"
    # Records whatever is on screen: the desktop must stay unlocked for the whole flight.
    "$ff" -loglevel error -y -f x11grab -framerate 30 -video_size "${w_}x${h_}" -i "${DISPLAY:-:0}.0+$ax,$ay" \
      -c:v libx264 -preset veryfast -crf 23 -pix_fmt yuv420p "$out/chase.mp4" > "$out/ffmpeg.log" 2>&1 & fp=$!
  fi
  docker cp "$PILOT" "$b:/tmp/fly_mission_mavros.py" >/dev/null
  docker exec "$b" bash -lc "$ROS; rm -rf /tmp/vio_bag; exec ros2 bag record -s mcap --storage-preset-profile zstd_fast -o /tmp/vio_bag $TOPICS" > "$out/record.log" 2>&1 & local bp=$!
  sleep 12   # parked segment for OpenVINS static initialisation
  echo "flying $(date +%T)"
  docker exec "$b" bash -lc "$ROS; timeout 900 python3 /tmp/fly_mission_mavros.py --trajectory-b64 $(cat "$route") --vehicle Drone1 --autopilot ardupilot" > "$out/pilot.log" 2>&1
  local rc=$?; echo "pilot exit $rc $(date +%T)"; sleep 3
  [ -n "$fp" ] && { kill -INT "$fp"; wait "$fp" 2>/dev/null; }
  docker exec "$b" bash -lc "pkill -INT -f 'ros2 [b]ag record'"; sleep 8; wait $bp 2>/dev/null
  rm -rf "$out/bag"; docker cp "$b:/tmp/vio_bag/." "$out/bag/" >/dev/null
  docker logs "$(dc "$s" ps --format '{{.Name}}' | grep unreal-airsim | head -1)" 2>&1 \
    | grep -E "ScenarioManager.*(ended|collision)|Collision" | tail -5 > "$out/sim_collisions.log"
  echo "bag $(du -sh "$out/bag" | cut -f1)$([ -f "$out/chase.mp4" ] && echo ", chase $(du -sh "$out/chase.mp4" | cut -f1)")"
  [ -n "${KEEP_UP:-}" ] || cmd_down "$s" >/dev/null
  return $rc
}

cmd_replay(){ # OUT TAG [--config NAME] [--dump]
  local f t cfg="" dump="" scn ovcfg
  f=$(cd "${1:?flight dir}" && pwd); t=${2:?tag}; shift 2
  while [ $# -gt 0 ]; do case $1 in --config) cfg=$2; shift 2;; --dump) dump=1; shift;; *) die "replay: unknown option $1";; esac; done
  scn=$(sed -n 's/^scenario=\([^ ]*\).*/\1/p' "$f/condition.txt" 2>/dev/null)
  # Default: t2 where the scenario ships it (the XFS campaign config), else t2zc. t2zc
  # holds the city route but scored 7-8 m on the XFS yard (2 flights, 29 Sep 2026).
  if [ -z "$cfg" ]; then cfg=t2; [ -d "$ROOT/scenarios/${scn:-gap-fisheye-xfs}/openvins/t2" ] || cfg=t2zc; fi
  ovcfg=${OVCFG:-$ROOT/scenarios/${scn:-gap-fisheye-xfs}/openvins/$cfg}
  [ -f "$ovcfg/estimator_config.yaml" ] || die "no OpenVINS config at $ovcfg"
  [ -d "$METRICS_DIR" ] && docker image inspect "$METRICS_IMAGE" >/dev/null 2>&1 \
    || die "scoring needs $METRICS_IMAGE and $METRICS_DIR (the tevv_ws metrics package)"
  local net=gap-replay-$t dom=19 player res
  player=$(grep -E '^ROS2_IMAGE=' "$ROOT/generated/gap-tests/${scn:-gap-fisheye-xfs}/stack/.env" 2>/dev/null | cut -d= -f2-)
  player=${player:-dhdevspace/auto_mns:tevv-airsim-ros2-bridge-humble-v1.0.0}
  docker network inspect "$net" >/dev/null 2>&1 || docker network create "$net" >/dev/null
  for c in ov met play dump; do docker rm -f "$c-$t" >/dev/null 2>&1; done
  res=$f/results/$t; rm -rf "$res"; mkdir -p "$res"
  cat > "$f/qos.yaml" <<'Q'
/imu/data: {reliability: reliable, durability: volatile, history: keep_last, depth: 100}
/ground_truth/odom: {reliability: reliable, durability: volatile, history: keep_last, depth: 100}
/fisheye_front/image_raw: {reliability: reliable, durability: volatile, history: keep_last, depth: 100}
/fisheye_back/image_raw: {reliability: reliable, durability: volatile, history: keep_last, depth: 100}
Q
  local E=(-e "ROS_DOMAIN_ID=$dom" -e ROS_LOCALHOST_ONLY=0 -e HOME=/tmp)
  docker run -d --name "ov-$t" --network "$net" "${E[@]}" -v "$ovcfg:/opt/tevv/openvins-config:ro" "$OV_IMAGE" \
    ros2 run ov_msckf run_subscribe_msckf --ros-args -r __ns:=/ov_msckf -p use_sim_time:=true \
    -p config_path:=/opt/tevv/openvins-config/estimator_config.yaml >/dev/null
  docker run -d --name "met-$t" --network "$net" "${E[@]}" -e METRICS_TRUTH_TOPIC=/ground_truth/odom \
    -e METRICS_ESTIMATE_TOPIC=/ov_msckf/odomimu -v "$METRICS_DIR:/opt/tevv/metrics:ro" -v "$res:/results" \
    "$METRICS_IMAGE" --manual --duration 100000 --timeout 600 --finish-service /tevv/metrics/finish --output /results >/dev/null
  if [ -n "$dump" ]; then
    rm -rf "$f/tracks_$t"; mkdir -p "$f/tracks_$t"
    docker run -d --name "dump-$t" --network "$net" "${E[@]}" --user "$(id -u):$(id -g)" -v "$HERE:/h:ro" \
      -v "$f/tracks_$t:/out" --entrypoint bash "$player" -c "source /opt/ros/humble/setup.bash; exec python3 /h/trackdump.py /out" >/dev/null
  fi
  sleep 8
  docker run --rm --name "play-$t" --network "$net" "${E[@]}" --user "$(id -u):$(id -g)" -v "$f:/v:ro" --entrypoint bash "$player" \
    -c "source /opt/ros/humble/setup.bash; ros2 bag play /v/bag --qos-profile-overrides-path /v/qos.yaml --topics /clock /imu/data /ground_truth/odom /fisheye_front/image_raw /fisheye_back/image_raw >/dev/null 2>&1"
  sleep 5
  docker run --rm --network "$net" "${E[@]}" --entrypoint bash "$player" \
    -c "source /opt/ros/humble/setup.bash; ros2 service call /tevv/metrics/finish std_srvs/srv/Trigger" >/dev/null 2>&1 || true
  until [ "$(docker inspect -f '{{.State.Status}}' "met-$t" 2>/dev/null)" != "running" ]; do sleep 3; done
  docker logs "ov-$t" 2>&1 | sed 's/\x1b\[[0-9;]*m//g' > "$res/openvins.log"
  docker logs "met-$t" > "$res/metrics.log" 2>&1
  docker rm -f "ov-$t" "met-$t" "dump-$t" >/dev/null 2>&1 || true
  docker network rm "$net" >/dev/null 2>&1 || true
  python3 - "$res" "$cfg" <<'P'
import glob, json, re, sys
d, cfg = sys.argv[1], sys.argv[2]; L = open(f"{d}/openvins.log").read()
sl = [int(m) for m in re.findall(r"SLAM update \((\d+) feats\)", L)]
s = [json.load(open(p)) for p in glob.glob(f"{d}/*/summary.json")]
s = s[0] if s else {}
if s.get("status") == "measured":
    print(f"[{cfg}] init={'yes' if 'successful initialization' in L else 'NO'} path={s['matched_truth_path_length_m']:.0f} m "
          f"ATE={s['ate_position_m']['rmse']:.2f} m (max {s['ate_position_m']['max']:.2f}) "
          f"RPE={s['rpe_translation_m']['rmse']:.2f} landmarks/upd={sum(sl)/max(1,len(sl)):.1f}")
else:
    print(f"[{cfg}] not measured: {s or 'no summary.json (see openvins.log / metrics.log)'}")
P
}

cmd_video(){ python3 "$HERE/render_video.py" "$@"; }
cmd_sweep(){ python3 "$HERE/lighting_sweep.py" "$@"; }

cmd_cases(){ # SCENARIO CASES_FILE OUT
  local s=${1:?scenario} cases=${2:?cases file} out=${3:?out dir} tag t w fl veil rep
  mkdir -p "$out"; out=$(cd "$out" && pwd)
  while read -r tag t w fl veil <&3; do
    [[ -z "${tag:-}" || "$tag" == \#* ]] && continue
    [ -f "$out/$tag/bag/metadata.yaml" ] || cmd_fly "$s" "$out/$tag" --time "$t" --weather "$w" --flare "$fl" --veil "$veil" || true
    [ -f "$out/$tag/bag/metadata.yaml" ] || { echo "[$tag] no bag"; continue; }
    for rep in $(seq 1 "${REPLAYS:-3}"); do
      ls "$out/$tag/results/r$rep"/*/summary.json >/dev/null 2>&1 || cmd_replay "$out/$tag" "r$rep" ${CONFIG:+--config "$CONFIG"}
    done
  done 3< "$cases"
  python3 "$HERE/score_vio.py" "$out" "$cases"   # -> $out/results.tsv
}

case "${1:-}" in
  stack|up|down|fly|replay|video|cases|sweep) c=$1; shift; "cmd_$c" "$@" ;;
  *) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
