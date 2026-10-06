#!/usr/bin/env bash
# Run mns-stacks ($MNS_STACKS_IMAGE) as a sibling container, the way the
# dashboard does: one wrapper for make fly/stop/campaign/stacks and
# tools/images.sh drift, so every headless caller mounts the same paths.
#
#   tools/mns-stacks.sh generate scenarios/<name> --profile docker --out "$PWD/generated/<name>"
#   tools/mns-stacks.sh run --stack "$PWD/generated/<name>" --record --until-done
#   tools/mns-stacks.sh campaign run vio-reference
#   tools/mns-stacks.sh --help
#
# Host paths only (the mns-stacks contract): the checkout, the runs directory
# and any input outside the checkout are mounted at their identical host
# paths, and mns-stacks is told them (MNS_WORKSPACE_ROOT, SIM2REAL_RUNS_DIR),
# so every bind mount it hands Compose resolves on the host. It always runs as
# the host user (never root), so every file it writes is yours. The Docker
# socket, with its group and your Docker login, is mounted only for the
# commands that drive containers or look at the host (run, stop, status,
# restart, logs, record, check, and campaign run/preflight/status/watch/
# cancel), together with --network=host; the rest run with --network=none. The same rule as the dashboard backend's mns_cli.docker_argv.
#
# Inputs come from the environment; `make` exports the selected channel's
# (make fly / make stacks), and each has the same default as `make dashboard`:
#   MNS_STACKS_IMAGE                         required (images/v1.0.0.generated.env;
#                                            ./.env overrides it, with a note)
#   MNS_IMAGE_SET, MNS_IMAGE_SET_FILE        the image set generated stacks run
#   MNS_PACK_STORE_ROOT                      the channel's pack store
#   MNS_RUNTIME_HOST_COMPATIBILITY_CONTRACT  the runtime host contract
#   MNS_CAPABILITY_KIT                       a local kit folder instead of the pinned
#                                            host's contract (the override; warns)
#   TEVV_RUNS_DIR                            runs directory (default ./runs, or .env)
#   MNS_IMAGE_PULL_POLICY                    missing (default) | always | never
#   MNS_STACKS_DOCKER_ARGS                   extra `docker run` flags (word-split)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=tools/load-images-env.sh
. "$ROOT/tools/load-images-env.sh"
IMAGE="${MNS_STACKS_IMAGE:-}"
if [[ -z "$IMAGE" ]]; then
  IMAGE="$(dotenv_value MNS_STACKS_IMAGE "$ROOT/.env")"
  [[ -n "$IMAGE" ]] && echo "NOTE: MNS_STACKS_IMAGE from ./.env overrides the catalog pin: $IMAGE" >&2
fi
if [[ -z "$IMAGE" ]]; then
  echo "ERROR: MNS_STACKS_IMAGE is not set. Run this through make (make stacks ARGS=...)," >&2
  echo "       or: set -a; . images/v1.0.0.generated.env; set +a" >&2
  exit 2
fi
[[ $# -gt 0 ]] || set -- --help

# TEVV_RUNS_DIR from the shell, then ./.env, then ./runs: the same order
# `make dashboard` and compose use, so a headless bag lands where the
# dashboard's replay looks.
runs="${TEVV_RUNS_DIR:-$(dotenv_value TEVV_RUNS_DIR "$ROOT/.env")}"
runs="${runs:-$ROOT/runs}"
case "$runs" in "~"*) runs="$HOME${runs#\~}" ;; esac
mkdir -p "$runs"
RUNS_DIR="$(cd "$runs" && pwd)"

PACK_STORE="${MNS_PACK_STORE_ROOT:-$ROOT/.mns/v1/pack-store}"
IMAGE_SET_FILE="${MNS_IMAGE_SET_FILE:-$ROOT/images/image-set.generated.yaml}"
# Generated stacks take their images from the image-set file alone, so an
# image override in the shell or ./.env (MNS_RUNTIME_HOST_IMAGE,
# MNS_ROS2_BRIDGE_IMAGE) is applied to a copy of it, with a NOTE per override,
# for the commands that generate stacks. Without overrides the file is used
# as it is.
case "$1" in
  generate|campaign)
    if effective="$("${PYTHON:-python3}" "$ROOT/tools/images.py" effective-image-set \
          --in "$IMAGE_SET_FILE" --out "$ROOT/.mns/image-set.effective.yaml" --dotenv "$ROOT/.env")"; then
      IMAGE_SET_FILE="$effective"
    else
      echo "WARNING: could not apply the shell/./.env image overrides to $IMAGE_SET_FILE (see above);" >&2
      echo "         generated stacks run its images unchanged" >&2
    fi ;;
esac
CONTRACT="${MNS_RUNTIME_HOST_COMPATIBILITY_CONTRACT:-$ROOT/packs/runtime-host-compatibility.v1.json}"
KIT="${MNS_CAPABILITY_KIT:-}"
if [[ -n "$KIT" ]]; then
  [[ -d "$KIT" ]] || { echo "ERROR: MNS_CAPABILITY_KIT=$KIT is not a folder" >&2; exit 2; }
  KIT="$(cd "$KIT" && pwd)"
fi
HOST_UID="${MNS_HOST_UID:-$(id -u)}"
HOST_GID="${MNS_HOST_GID:-$(id -g)}"

# Which commands need the Docker socket (and the host network). campaign
# preflight inspects the pinned images (docker image/manifest inspect) and
# the host's listening ports (ss -ltn), as the dashboard runs it.
needs_socket=false
case "$1" in
  run|stop|status|restart|logs|record|check) needs_socket=true ;;
  campaign)
    case "${2:-}" in run|preflight|status|watch|cancel) needs_socket=true ;; esac ;;
  # init reads the container image (docker image inspect) to prefill the
  # command; check only reads files.
  component)
    case "${2:-}" in init) needs_socket=true ;; esac ;;
esac

args=(--rm --pull "${MNS_IMAGE_PULL_POLICY:-missing}"
      --user "$HOST_UID:$HOST_GID" -e HOME=/tmp)
[[ -t 0 && -t 1 ]] && args+=(-it)
args+=(
  -v "$ROOT:$ROOT" -w "$PWD"
  -e "MNS_WORKSPACE_ROOT=$ROOT"
  -e "SIM2REAL_RUNS_DIR=$RUNS_DIR"
  -e "MNS_IMAGE_SET=${MNS_IMAGE_SET:-v1}"
  -e "MNS_IMAGE_SET_FILE=$IMAGE_SET_FILE"
  -e "MNS_PACK_STORE_ROOT=$PACK_STORE"
  -e "MNS_HOST_UID=$HOST_UID" -e "MNS_HOST_GID=$HOST_GID"
)
if [[ -n "$KIT" ]]; then
  # The escape hatch for a kit that is not in an image yet: its contract
  # replaces the pinned host's, so the contract variable is not set at all.
  echo "WARNING: MNS_CAPABILITY_KIT=$KIT overrides the pinned runtime host contract" >&2
  args+=(-e "MNS_CAPABILITY_KIT=$KIT")
else
  args+=(-e "MNS_RUNTIME_HOST_COMPATIBILITY_CONTRACT=$CONTRACT")
fi
# Stack settings to take from this shell for one experiment, without editing
# a generated stack's .env (e.g. MNS_STACKS_PASSENV=POLL_RATE_HZ POLL_RATE_HZ=12).
# Compose reads them where the stack's .env leaves them unset.
for name in ${MNS_STACKS_PASSENV:-}; do
  [[ -n "${!name+x}" ]] && args+=(-e "$name=${!name}")
done
case "$PWD/" in "$ROOT"/*) ;; *) args+=(-v "$PWD:$PWD") ;; esac

# Inputs outside the checkout, at their identical paths. --mount (not -v)
# fails on a missing source instead of creating an empty directory there.
mount_outside() {
  local path="$1" mode="$2"
  case "$path/" in "$ROOT"/*) return 0 ;; esac
  args+=(--mount "type=bind,source=$path,target=$path${mode:+,$mode}")
}
mount_outside "$RUNS_DIR" ""
[[ -e "$PACK_STORE" ]] && mount_outside "$PACK_STORE" ""
[[ -e "$IMAGE_SET_FILE" ]] && mount_outside "$IMAGE_SET_FILE" readonly
if [[ -n "$KIT" ]]; then
  # Read-only at its identical path, even inside the checkout (-v twice is fine).
  args+=(--mount "type=bind,source=$KIT,target=$KIT,readonly")
elif [[ -e "$CONTRACT" ]]; then
  mount_outside "$CONTRACT" readonly
fi

if [[ "$needs_socket" == true ]]; then
  # Still the host user: the socket's group is added so it may drive the
  # daemon, and your Docker login is mounted read-only so it can pull the
  # private images the stack needs. DISPLAY and XAUTHORITY are only
  # interpolated into the generated compose files (host paths for the host
  # daemon); this container opens neither.
  socket=/var/run/docker.sock
  args+=(-v "$socket:$socket" --network=host
         -e "DISPLAY=${DISPLAY:-:0}" -e "XAUTHORITY=${XAUTHORITY:-}")
  if sock_gid="$(stat -c %g "$socket" 2>/dev/null)"; then
    args+=(--group-add "$sock_gid")
  fi
  docker_config="${DOCKER_CONFIG:-$HOME/.docker}"
  if [[ -e "$docker_config" ]]; then
    args+=(-e DOCKER_CONFIG=/tmp/.docker -v "$docker_config:/tmp/.docker:ro")
  fi
else
  args+=(--network=none)
fi

# shellcheck disable=SC2206 # deliberate word splitting of extra docker flags
extra=(${MNS_STACKS_DOCKER_ARGS:-})
exec docker run "${args[@]}" "${extra[@]}" "$IMAGE" "$@"
