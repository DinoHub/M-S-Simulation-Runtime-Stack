#!/usr/bin/env bash
# Open ScenarioLab headless of the dashboard: the same `docker run <authoring
# image> editor ...` the dashboard's editor window starts (TEVV-Web-Dashboard
# backend/app/services/authoring.py), with the same container name, mounts and
# flags, so only one editor runs whichever started it. `make author` stages
# the packs first, as `make dashboard` does.
#
#   tools/author.sh [SCENARIO]     SCENARIO: a folder or ScenarioSpec file to open
#   tools/author.sh --stop         close it
#
# Exports land in scenarios/<name>/. Environment (make exports the channel's):
#   MNS_AUTHORING_IMAGE          required (images/v1.0.0.generated.env; ./.env overrides)
#   MNS_AUTHORING_DATA_ROOT      staged packs (default .mns/v1/authoring-data)
#   MNS_AUTHORING_DOCKER_GPU_ARGS  default: --gpus all
#   MNS_AUTHORING_DOCKER_ARGS    extra docker run flags (word-split)
#   DISPLAY, XAUTHORITY          the desktop session (make resolves XAUTHORITY)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=tools/load-images-env.sh
. "$ROOT/tools/load-images-env.sh"
# The dashboard's container name, with its prefix from the shell or ./.env
# (compose reads it from .env), so the two never start two editors.
PREFIX="${DASHBOARD_CONTAINER_PREFIX-$(dotenv_value DASHBOARD_CONTAINER_PREFIX "$ROOT/.env")}"
NAME="${PREFIX}mns-scenariolab-editor"
usage() { sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; }

case "${1:-}" in
  -h|--help) usage; exit 0 ;;
  --stop) docker rm -f "$NAME" >/dev/null 2>&1 || true; echo "ScenarioLab stopped."; exit 0 ;;
esac

IMAGE="${MNS_AUTHORING_IMAGE:-}"
if [[ -z "$IMAGE" ]]; then
  IMAGE="$(dotenv_value MNS_AUTHORING_IMAGE "$ROOT/.env")"
  [[ -n "$IMAGE" ]] && echo "NOTE: MNS_AUTHORING_IMAGE from ./.env overrides the catalog pin: $IMAGE" >&2
fi
[[ -n "$IMAGE" ]] || { echo "ERROR: MNS_AUTHORING_IMAGE is not set; run this through make author" >&2; exit 2; }
EXPORTS="$ROOT/scenarios"
DATA="${MNS_AUTHORING_DATA_ROOT:-$ROOT/.mns/v1/authoring-data}"
mkdir -p "$EXPORTS" "$DATA"
DATA="$(cd "$DATA" && pwd)"

args=(-d --name "$NAME" --init --ipc=host --network=host
      --user "$(id -u):$(id -g)" -e HOME=/tmp
      -v "$EXPORTS:/mnt/mns/exports:rw"
      -e MNS_AUTHORING_EXPORT_ROOT=/mnt/mns/exports
      -e "MNS_AUTHORING_EXPORT_DISPLAY_ROOT=$EXPORTS"
      -v "$DATA:/mnt/mns/authoring-data:rw"
      -e MNS_AUTHORING_DATA_ROOT=/mnt/mns/authoring-data)

scenario_args=()
if [[ -n "${1:-}" ]]; then
  target="$1"
  [[ -e "$target" ]] || target="$ROOT/scenarios/$1"
  if [[ ! -e "$target" ]]; then
    echo "ERROR: scenario path does not exist: $1 (nor scenarios/$1)" >&2
    exit 2
  fi
  target="$(cd "$(dirname "$target")" && pwd)/$(basename "$target")"
  if [[ -f "$target" ]]; then
    args+=(-v "$(dirname "$target"):/mnt/mns/scenario:rw")
    scenario_args=(--scenario "/mnt/mns/scenario/$(basename "$target")")
  else
    args+=(-v "$target:/mnt/mns/scenario:rw")
    scenario_args=(--scenario /mnt/mns/scenario)
  fi
fi

for var in DISPLAY SDL_VIDEODRIVER QT_X11_NO_MITSHM; do
  [[ -n "${!var+x}" ]] && args+=(-e "$var=${!var}")
done
if [[ -n "${XAUTHORITY:-}" && -f "$XAUTHORITY" ]]; then
  args+=(-e XAUTHORITY=/tmp/.Xauthority -v "$XAUTHORITY:/tmp/.Xauthority:ro")
else
  echo "WARNING: no X11 cookie found (XAUTHORITY); the editor may fail to open its window." >&2
  echo "         Run this from a terminal on the desktop." >&2
fi
args+=(-v /tmp/.X11-unix:/tmp/.X11-unix:rw)
# shellcheck disable=SC2206 # deliberate word splitting, as the dashboard does
gpu=(${MNS_AUTHORING_DOCKER_GPU_ARGS:---gpus all})
# The dashboard's compose file passes the same AirSim settings mount.
# shellcheck disable=SC2206
extra=(-v "$ROOT/config/scenariolab/airsim-settings.json:/tmp/Documents/AirSim/settings.json:ro"
       ${MNS_AUTHORING_DOCKER_ARGS:-})

docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run "${args[@]}" "${gpu[@]}" "${extra[@]}" "$IMAGE" \
  editor --export-root /mnt/mns/exports "${scenario_args[@]}" >/dev/null

# A doomed editor (bad X cookie, no NVIDIA runtime) exits within a second or
# two: say so here instead of reporting a launch that is not there.
sleep 2
if [[ "$(docker inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null)" != true ]]; then
  echo "ERROR: ScenarioLab exited immediately. Its last log lines:" >&2
  docker logs --tail 40 "$NAME" >&2 2>&1 || true
  exit 1
fi
echo "ScenarioLab is starting on ${DISPLAY:-:0} ($IMAGE)."
echo "Exports land in scenarios/<name>/; fly one with: make fly SCENARIO=<name>"
echo "Close it from its window, or: make author-stop"
