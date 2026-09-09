#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Which standalone-v2 release channel to run (same knob as `make dashboard
# CHANNEL=`): ue582 is UE 5.8.2 (default), v2 the previous UE 5.5.4 set. Each channel has
# its own generated image env, pack lock, runtime-host contract and, because
# packs cooked for one engine never mount on the other, its own pack store
# and authoring data root under .mns/.
CHANNEL="${MNS_CHANNEL:-ue582}"
case "$CHANNEL" in
  v2)
    CHANNEL_NAME=standalone_v2
    CHANNEL_ENV="$ROOT/images/standalone-v2-images.generated.env"
    CHANNEL_LOCK="$ROOT/packs/standalone-v2-review.1.lock.json"
    CHANNEL_CONTRACT="$ROOT/packs/runtime-host-compatibility.json"
    CHANNEL_DIR="$ROOT/.mns"
    ;;
  ue582)
    CHANNEL_NAME=standalone_v2_ue582
    CHANNEL_ENV="$ROOT/images/standalone-v2-ue582.generated.env"
    CHANNEL_LOCK="$ROOT/packs/standalone-v2-ue582.lock.json"
    CHANNEL_CONTRACT="$ROOT/packs/runtime-host-compatibility.ue582.json"
    CHANNEL_DIR="$ROOT/.mns/ue582"
    ;;
  *) echo "ERROR: MNS_CHANNEL must be v2 or ue582; got: $CHANNEL" >&2; exit 2 ;;
esac
# shellcheck disable=SC1090
source "$CHANNEL_ENV"
# The generated env names the channel's image_sets entry (MNS_IMAGE_SET);
# `published` is the 5.5.4 set's name and the fallback for an older file.
IMAGE_SET="${MNS_IMAGE_SET:-published}"
export MNS_DEMO_PACK_LOCK="$CHANNEL_LOCK"
export MNS_RUNTIME_HOST_COMPATIBILITY_CONTRACT="$CHANNEL_CONTRACT"
DATA_ROOT="${MNS_AUTHORING_DATA_ROOT:-$CHANNEL_DIR/authoring-data}"
PACK_STORE_ROOT="${MNS_PACK_STORE_ROOT:-$CHANNEL_DIR/pack-store}"
export MNS_PACK_STORE_ROOT="$PACK_STORE_ROOT"
# Paths the shell sees: the checkout is mounted at /workspace, so anything
# under $ROOT maps 1:1 and anything outside is unreachable from the container.
container_path() {
  case "$1" in
    "$ROOT"/*) printf '/workspace/%s' "${1#"$ROOT"/}" ;;
    *) echo "ERROR: $1 is outside the checkout $ROOT; the product shell cannot see it" >&2; exit 2 ;;
  esac
}
CONTAINER_PACK_STORE="$(container_path "$PACK_STORE_ROOT")"
CONTAINER_CONTRACT="$(container_path "$CHANNEL_CONTRACT")"
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
    -e "MNS_PACK_STORE_ROOT=$CONTAINER_PACK_STORE" \
    -e "MNS_HOST_WORKSPACE_ROOT=$ROOT" \
    -e "MNS_HOST_UID=$(id -u)" \
    -e "MNS_HOST_GID=$(id -g)" \
    -e "MNS_SCENARIO_EXPORTS_ROOT=$EXPORT_ROOT" \
    -e "MNS_AUTHORING_DATA_ROOT=$DATA_ROOT" \
    -e "MNS_AUTHORING_IMAGE=$MNS_AUTHORING_IMAGE" \
    -e "MNS_AUTHORING_DOCKER_ARGS=$AUTHORING_DOCKER_ARGS" \
    -e "MNS_AUTHORING_DOCKER_GPU_ARGS=${MNS_AUTHORING_DOCKER_GPU_ARGS:-}" \
    -e "MNS_STACK_GENERATOR_IMAGE=$MNS_STACK_GENERATOR_IMAGE" \
    -e "MNS_STACK_GENERATOR_DOCKER_ARGS=-e MNS_RUNTIME_HOST_COMPATIBILITY_CONTRACT=$CONTAINER_CONTRACT${MNS_STACK_GENERATOR_DOCKER_ARGS:+ $MNS_STACK_GENERATOR_DOCKER_ARGS}" \
    -e "MNS_IMAGE_SET=$IMAGE_SET" \
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
    "$ROOT/tools/pull-all-images.sh" --development
    ;;
  pull-images)
    shift
    exec "$ROOT/tools/pull-all-images.sh" "$@"
    ;;
  doctor)
    prepare_dirs; docker compose version >/dev/null
    # NOT `mapfile -t images < <(... refs)`: a process substitution's exit
    # status is discarded, so a failing `images.sh refs` (no python3, no
    # pyyaml, unreadable catalog) left `images` empty, skipped the loop
    # entirely, and printed "Product prerequisites are ready." on a machine
    # with zero images pulled. Capture first, check the status, then split.
    if ! images_out="$("$ROOT/tools/images.sh" refs --channel "$CHANNEL_NAME")"; then
      echo "ERROR: could not enumerate product images (tools/images.sh refs failed)." >&2
      echo "       See the Requirements section of README.md." >&2
      exit 1
    fi
    mapfile -t images <<<"$images_out"
    if [[ "${#images[@]}" -eq 0 || -z "${images[0]}" ]]; then
      echo "ERROR: tools/images.sh refs returned no images; refusing to report success." >&2
      exit 1
    fi
    # channel: local rows are excluded from refs (nothing can pull them) but the
    # product still needs them present; a fully published channel lists none.
    mapfile -t local_images < <("$ROOT/tools/images.sh" local-refs --channel "$CHANNEL_NAME")
    failed=0
    for image in "${images[@]}" "${local_images[@]}"; do
      [[ -n "$image" ]] || continue
      docker image inspect "$image" >/dev/null 2>&1 || { echo "MISSING IMAGE: $image"; failed=1; }
    done
    if [[ "$failed" == 0 ]]; then
      echo "Product prerequisites are ready (channel $CHANNEL, ${#images[@]} pulled + ${#local_images[@]} local image(s))."
    else
      echo "Missing local/ images are built on this machine, not pulled: see packs/README.md." >&2
    fi
    exit "$failed"
    ;;
  start) prepare_dirs; docker rm -f mns-product-shell >/dev/null 2>&1 || true; run_shell serve --host 0.0.0.0 --port 8765; echo "MnS product shell: http://127.0.0.1:$PORT" ;;
  stop) docker rm -f mns-product-shell >/dev/null 2>&1 || true ;;
  cli) prepare_dirs; shift; run_shell cli "$@" ;;
  -h|--help|help|"") usage ;;
  *) usage >&2; exit 2 ;;
esac
