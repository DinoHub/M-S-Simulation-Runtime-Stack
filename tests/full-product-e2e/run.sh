#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT/product-images.env"
FIXTURE="$ROOT/tests/full-product-e2e/fixture"
AUTHORED="$ROOT/scenarios/full-product-e2e-export"
STACK="$ROOT/generated/full-product-e2e-export"
EVIDENCE="$STACK/e2e-evidence"
AUTHORING_CONTAINER="mns-full-product-e2e-authoring"
STACK_NAME=""
CLEANED_UP=0

log() { printf '[full-product-e2e] %s\n' "$*"; }

compose() {
  (
    set -a
    # shellcheck disable=SC1090
    source "$STACK/.env"
    set +a
    export HOST_UID="$(id -u)"
    export GID="$(id -g)"
    export CONFIG_ROOT="$STACK/config"
    export DISPLAY="${DISPLAY:-:1}"
    export XAUTHORITY="${XAUTHORITY:-/run/user/$(id -u)/gdm/Xauthority}"
    docker compose -f "$STACK/docker-compose.yml" "$@"
  )
}

cleanup() {
  [[ "$CLEANED_UP" == 0 ]] || return 0
  CLEANED_UP=1
  set +e
  if [[ -f "$STACK/docker-compose.yml" ]]; then
    "$ROOT/product.sh" cli stop --stack /workspace/generated/full-product-e2e-export >/dev/null 2>&1
    compose down --remove-orphans >/dev/null 2>&1
  fi
  "$ROOT/product.sh" stop >/dev/null 2>&1
  docker rm -f "$AUTHORING_CONTAINER" >/dev/null 2>&1
  if [[ -n "$STACK_NAME" ]]; then
    mapfile -t leftovers < <(docker ps -aq --filter "label=com.docker.compose.project=$STACK_NAME")
    if [[ "${#leftovers[@]}" -gt 0 ]]; then
      docker rm -f "${leftovers[@]}" >/dev/null 2>&1
    fi
  fi
  set -e
}
trap cleanup EXIT INT TERM

verify() {
  local phase="$1"
  local root="$2"
  docker run --rm \
    --user "$(id -u):$(id -g)" \
    -e HOME=/tmp \
    -v "$ROOT:/workspace:ro" \
    --entrypoint python3 \
    "$MNS_PRODUCT_SHELL_IMAGE" \
    /workspace/tests/full-product-e2e/verify.py \
    --phase "$phase" \
    --root "/workspace/${root#$ROOT/}"
}

wait_for_shell() {
  local attempt
  for attempt in $(seq 1 30); do
    if curl -fsS "http://127.0.0.1:${MNS_SCENARIO_LAUNCHER_PORT:-8765}/" >/dev/null; then
      return 0
    fi
    sleep 1
  done
  log "product shell did not become ready"
  return 1
}

wait_for_airsim() {
  local id status state attempt
  for attempt in $(seq 1 120); do
    id="$(compose ps -q unreal-airsim 2>/dev/null || true)"
    if [[ -n "$id" ]]; then
      state="$(docker inspect --format '{{.State.Status}}' "$id")"
      status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$id")"
      if [[ "$status" == healthy ]]; then
        return 0
      fi
      if [[ "$state" == exited || "$state" == dead ]]; then
        compose logs --no-color unreal-airsim || true
        log "Unreal runtime exited before becoming healthy"
        return 1
      fi
    fi
    sleep 2
  done
  compose logs --no-color unreal-airsim || true
  log "Unreal runtime did not become healthy"
  return 1
}

wait_for_sensor_topics() {
  local service="$1"
  local runtime_name="$2"
  local camera_topic="/$runtime_name/front_rgb/image_raw"
  local lidar_topic="/$runtime_name/LidarSensor1/points"
  local container attempt
  container="$(compose ps -q "$service")"
  [[ -n "$container" ]] || { log "missing bridge container for $runtime_name"; return 1; }
  for attempt in $(seq 1 90); do
    docker exec "$container" bash -lc \
      'source /opt/ros/humble/setup.bash; source /ws/install/setup.bash; ROS_DISABLE_DAEMON=1 ros2 topic list' \
      >"$EVIDENCE/topics-$runtime_name.txt" 2>&1 || true
    if grep -Fxq "$camera_topic" "$EVIDENCE/topics-$runtime_name.txt" && \
       grep -Fxq "$lidar_topic" "$EVIDENCE/topics-$runtime_name.txt"; then
      return 0
    fi
    sleep 2
  done
  docker logs "$container" || true
  log "camera/lidar topics did not appear for $runtime_name"
  return 1
}

capture_rate() {
  local service="$1"
  local runtime_name="$2"
  local kind="$3"
  local topic="$4"
  local container rc
  container="$(compose ps -q "$service")"
  set +e
  docker exec "$container" bash -lc \
    "source /opt/ros/humble/setup.bash; source /ws/install/setup.bash; ROS_DISABLE_DAEMON=1 timeout 25 stdbuf -oL ros2 topic hz '$topic' --window 10" \
    >"$EVIDENCE/$kind-hz-$runtime_name.txt" 2>&1
  rc=$?
  set -e
  if [[ "$rc" != 0 && "$rc" != 124 ]]; then
    log "$kind rate command failed for $runtime_name (exit $rc)"
    return 1
  fi
  grep -q "average rate:" "$EVIDENCE/$kind-hz-$runtime_name.txt" || {
    log "no live $kind samples received for $runtime_name"
    return 1
  }
}

log "checking the four pinned product images"
"$ROOT/product.sh" doctor

log "starting the browser product shell"
"$ROOT/product.sh" start
wait_for_shell

log "materializing drones, a static object, a spawn zone, conditions, and sensors in packaged ScenarioLab"
rm -rf "$AUTHORED" "$STACK"
export MNS_AUTHORING_DOCKER_ARGS="--name $AUTHORING_CONTAINER"
"$ROOT/product.sh" cli editor \
  --scenario /workspace/tests/full-product-e2e/fixture \
  --export-root /workspace/scenarios \
  --wait \
  --automation-export-after-load full-product-e2e-export
verify authored "$AUTHORED"

log "validating and generating the runtime stack through the generator image"
"$ROOT/product.sh" cli runtime \
  --scenario /workspace/scenarios/full-product-e2e-export \
  --out /workspace/generated/full-product-e2e-export \
  --profile docker \
  --image-set published \
  --no-run
verify generated "$STACK"

mkdir -p "$EVIDENCE"
STACK_NAME="$(sed -n 's/^stack_name: //p' "$STACK/generated-manifest.yaml" 2>/dev/null | head -1)"
if [[ -z "$STACK_NAME" ]]; then
  STACK_NAME="$(sed -n 's/^name: //p' "$STACK/docker-compose.yml" | head -1)"
fi
STACK_NAME="${STACK_NAME//\"/}"

log "starting the generated Blocks runtime"
"$ROOT/product.sh" cli run-stack --stack /workspace/generated/full-product-e2e-export --detach
wait_for_airsim

log "waiting for per-drone camera and lidar topics"
wait_for_sensor_topics airsim_bridge_c1 Copter1
wait_for_sensor_topics airsim_bridge_c2 Copter2

log "sampling live camera and lidar output"
capture_rate airsim_bridge_c1 Copter1 camera /Copter1/front_rgb/image_raw
capture_rate airsim_bridge_c1 Copter1 lidar /Copter1/LidarSensor1/points
capture_rate airsim_bridge_c2 Copter2 camera /Copter2/front_rgb/image_raw
capture_rate airsim_bridge_c2 Copter2 lidar /Copter2/LidarSensor1/points
compose logs --no-color unreal-airsim >"$EVIDENCE/unreal.log" 2>&1
verify live "$EVIDENCE"

log "stopping and removing every container started by this test"
cleanup
trap - EXIT INT TERM
if [[ -n "$STACK_NAME" ]] && [[ -n "$(docker ps -aq --filter "label=com.docker.compose.project=$STACK_NAME")" ]]; then
  log "test-owned Compose containers remain after cleanup"
  exit 1
fi
if docker ps -a --format '{{.Names}}' | grep -Eq "^($AUTHORING_CONTAINER|mns-product-shell)$"; then
  log "test-owned product containers remain after cleanup"
  exit 1
fi
log "PASS: authored scenario, generated artifacts, live runtime behavior, sensor output, and cleanup all verified"
