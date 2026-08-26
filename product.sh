#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$ROOT/images/standalone-v2-images.generated.env"
if [[ -n "${MNS_AUTHORING_IMAGE_OVERRIDE:-}" ]]; then
  MNS_AUTHORING_IMAGE="$MNS_AUTHORING_IMAGE_OVERRIDE"
fi
DATA_ROOT="${MNS_AUTHORING_DATA_ROOT:-$ROOT/.mns/authoring-data}"
PACK_STORE_ROOT="$ROOT/.mns/pack-store"
EXPORT_ROOT="$ROOT/scenarios"
GENERATED_ROOT="$ROOT/generated"
AUTHORING_AIRSIM_SETTINGS="$ROOT/config/unreal-airsim/authoring-preview.json"
AUTHORING_DOCKER_ARGS="-v \"$AUTHORING_AIRSIM_SETTINGS:/tmp/Documents/AirSim/settings.json:ro\"${MNS_AUTHORING_DOCKER_ARGS:+ $MNS_AUTHORING_DOCKER_ARGS}"
# 8760 (was 8765): 8765 is the Foxglove websocket standard — Lichtblick's
# default connection URL and the dashboard backend's FOXGLOVE_PROBE_PORT both
# assume it, so the dashboard's ros2-node foxglove_bridge owns it now.
PORT="${MNS_SCENARIO_LAUNCHER_PORT:-8760}"

usage() { echo "Usage: ./product.sh setup|pull-images|doctor|start|stop|cli [launcher args...]"; }

prepare_dirs() { mkdir -p "$DATA_ROOT/PackLibrary/level_packs" "$DATA_ROOT/PackLibrary/asset_packs" "$PACK_STORE_ROOT" "$EXPORT_ROOT" "$GENERATED_ROOT"; }
run_shell() {
  local mode=(--rm -d --name mns-product-shell)
  local port_args=(-p "$PORT:8765")
  local xauth_args=()
  local docker_config_args=()
  local docker_config="${DOCKER_CONFIG:-$HOME/.docker}/config.json"
  if [[ -n "${XAUTHORITY:-}" && -e "${XAUTHORITY:-}" ]]; then
    xauth_args=(-v "$XAUTHORITY:$XAUTHORITY:ro" -e "XAUTHORITY=$XAUTHORITY" -e "MNS_HOST_XAUTHORITY=$XAUTHORITY")
  fi
  if [[ -f "$docker_config" ]]; then
    docker_config_args=(-v "$docker_config:/root/.docker/config.json:ro")
  fi
  if [[ "${1:-}" == "cli" ]]; then mode=(--rm); port_args=(); shift; fi
  # MNS_IMAGE_SET_FILE below is a CONTAINER path, not the host path:
  # launcher.py's require_workspace_path()
  # (MnS-Integration-Platform/apps/scenario_launcher/launcher.py:252) rejects
  # any --image-set-file/MNS_IMAGE_SET_FILE outside MNS_WORKSPACE_ROOT
  # (/workspace, mounted below). The launcher translates this back to a real
  # HOST path via MNS_HOST_WORKSPACE_ROOT (also set below) when it re-mounts
  # the workspace for the nested generator container it spawns — the same
  # docker-outside-of-docker indirection docker-compose-dashboard.yml's
  # MNS_WORKSPACE_ROOT comment describes.
  # Contrast tools/images.sh drift, which runs the generator directly from
  # the host with an identical-path mount ($ROOT:$ROOT) and so passes the
  # HOST path form instead.
  docker run "${mode[@]}" \
    "${port_args[@]}" \
    -v /var/run/docker.sock:/var/run/docker.sock \
    "${docker_config_args[@]}" \
    -v "$ROOT:/workspace:rw" \
    -e MNS_LAUNCH_BACKEND=docker \
    -e MNS_WORKSPACE_ROOT=/workspace \
    -e MNS_GENERATED_STACKS_ROOT=/workspace/generated \
    -e MNS_PACK_STORE_ROOT=/workspace/.mns/pack-store \
    -e "MNS_HOST_WORKSPACE_ROOT=$ROOT" \
    -e "MNS_HOST_UID=$(id -u)" \
    -e "MNS_HOST_GID=$(id -g)" \
    -e "MNS_SCENARIO_EXPORTS_ROOT=$EXPORT_ROOT" \
    -e "MNS_AUTHORING_DATA_ROOT=$DATA_ROOT" \
    -e "MNS_AUTHORING_IMAGE=$MNS_AUTHORING_IMAGE" \
    -e "MNS_AUTHORING_DOCKER_ARGS=$AUTHORING_DOCKER_ARGS" \
    -e "MNS_AUTHORING_DOCKER_GPU_ARGS=${MNS_AUTHORING_DOCKER_GPU_ARGS:-}" \
    -e "MNS_STACK_GENERATOR_IMAGE=$MNS_STACK_GENERATOR_IMAGE" \
    -e MNS_IMAGE_SET=published \
    -e MNS_IMAGE_SET_FILE=/workspace/images/image-set.generated.yaml \
    -e "DISPLAY=${DISPLAY:-:0}" \
    -v /tmp/.X11-unix:/tmp/.X11-unix:rw \
    "${xauth_args[@]}" \
    "$MNS_PRODUCT_SHELL_IMAGE" "$@"
}

case "${1:-}" in
  setup)
    prepare_dirs
    "$ROOT/tools/pull-all-images.sh"
    ;;
  pull-images)
    shift
    exec "$ROOT/tools/pull-all-images.sh" "$@"
    ;;
  doctor)
    prepare_dirs; docker compose version >/dev/null
    mapfile -t images < <("$ROOT/tools/images.sh" refs)
    failed=0
    for image in "${images[@]}"; do docker image inspect "$image" >/dev/null 2>&1 || { echo "MISSING IMAGE: $image"; failed=1; }; done
    [[ "$failed" == 0 ]] && echo "Product prerequisites are ready."
    exit "$failed"
    ;;
  start) prepare_dirs; docker rm -f mns-product-shell >/dev/null 2>&1 || true; run_shell serve --host 0.0.0.0 --port 8765; echo "MnS product shell: http://127.0.0.1:$PORT" ;;
  stop) docker rm -f mns-product-shell >/dev/null 2>&1 || true ;;
  cli) prepare_dirs; shift; run_shell cli "$@" ;;
  -h|--help|help|"") usage ;;
  *) usage >&2; exit 2 ;;
esac
