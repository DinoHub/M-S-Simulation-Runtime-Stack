#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$ROOT/product-images.env"
DATA_ROOT="$ROOT/.mns/authoring-data"
EXPORT_ROOT="$ROOT/scenarios"
GENERATED_ROOT="$ROOT/generated"
PORT="${MNS_SCENARIO_LAUNCHER_PORT:-8765}"
images=("$MNS_PRODUCT_SHELL_IMAGE" "$MNS_AUTHORING_IMAGE" "$MNS_STACK_GENERATOR_IMAGE" "$MNS_BLOCKS_IMAGE")

usage() { echo "Usage: ./product.sh setup|doctor|start|stop|cli [launcher args...]"; }
prepare_dirs() { mkdir -p "$DATA_ROOT/PackLibrary/level_packs" "$DATA_ROOT/PackLibrary/asset_packs" "$EXPORT_ROOT" "$GENERATED_ROOT"; }
run_shell() {
  local mode=(--rm -d --name mns-product-shell)
  local port_args=(-p "$PORT:8765")
  local xauth_args=()
  if [[ -n "${XAUTHORITY:-}" && -e "${XAUTHORITY:-}" ]]; then
    xauth_args=(-v "$XAUTHORITY:$XAUTHORITY:ro" -e "XAUTHORITY=$XAUTHORITY" -e "MNS_HOST_XAUTHORITY=$XAUTHORITY")
  fi
  if [[ "${1:-}" == "cli" ]]; then mode=(--rm); port_args=(); shift; fi
  docker run "${mode[@]}" \
    "${port_args[@]}" \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v "$ROOT:/workspace:rw" \
    -e MNS_LAUNCH_BACKEND=docker \
    -e MNS_WORKSPACE_ROOT=/workspace \
    -e MNS_GENERATED_STACKS_ROOT=/workspace/generated \
    -e "MNS_HOST_WORKSPACE_ROOT=$ROOT" \
    -e "MNS_HOST_UID=$(id -u)" \
    -e "MNS_HOST_GID=$(id -g)" \
    -e "MNS_SCENARIO_EXPORTS_ROOT=$EXPORT_ROOT" \
    -e "MNS_AUTHORING_DATA_ROOT=$DATA_ROOT" \
    -e "MNS_AUTHORING_IMAGE=$MNS_AUTHORING_IMAGE" \
    -e "MNS_AUTHORING_DOCKER_ARGS=${MNS_AUTHORING_DOCKER_ARGS:-}" \
    -e "MNS_AUTHORING_DOCKER_GPU_ARGS=${MNS_AUTHORING_DOCKER_GPU_ARGS:-}" \
    -e "MNS_STACK_GENERATOR_IMAGE=$MNS_STACK_GENERATOR_IMAGE" \
    -e MNS_IMAGE_SET=published \
    -e "DISPLAY=${DISPLAY:-:0}" \
    -v /tmp/.X11-unix:/tmp/.X11-unix:rw \
    "${xauth_args[@]}" \
    "$MNS_PRODUCT_SHELL_IMAGE" "$@"
}

case "${1:-}" in
  setup) prepare_dirs; for image in "${images[@]}"; do docker pull "$image"; done ;;
  doctor)
    prepare_dirs; docker compose version >/dev/null
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
