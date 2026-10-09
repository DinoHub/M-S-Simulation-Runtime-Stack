#!/usr/bin/env bash
# A second TEVV dashboard beside the main one (`./tevv up`), to try another
# dashboard build without restarting or re-imaging the one in use.
#
#   tools/dashboard-instance.sh <name> up [--port 3011] [--api-port 8011] \
#       [--backend-image IMAGE] [--frontend-image IMAGE]
#   tools/dashboard-instance.sh <name> down
#   tools/dashboard-instance.sh <name> status
#
# What it runs: only its own backend and frontend, as containers
# <name>-airsim-dashboard-api and <name>-airsim-dashboard-frontend (compose
# project tevv-dash-<name>). They join the main dashboard's network and share
# its metrics service, Lichtblick, ros2-tools and Sim-view signalling server: a second
# metrics service would score every run twice. The main dashboard must be up.
#
# The frontend talks to the backend port it was BUILT for (NEXT_PUBLIC_API_URL
# is inlined at build time), so build it for --api-port, in tevv-web-dashboard:
#   NEXT_PUBLIC_API_URL=http://localhost:8011 \
#   TEVV_DASHBOARD_FRONTEND_IMAGE=<image> docker compose build frontend
# The backend lets that frontend in through DASHBOARD_PORT (CORS).
#
# Runs stay one at a time across both dashboards: they share the GPU and the
# simulator's ports, and a run's preflight refuses while another holds them.
# Each dashboard lists only the jobs it started itself. Bag replay uses one
# player for both: replay in one dashboard at a time.
set -euo pipefail
cd "$(dirname "$0")/.."

usage() { sed -n '2,26p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }
[ $# -ge 2 ] || usage
NAME=$1 ACTION=$2; shift 2
[[ $NAME =~ ^[a-z0-9][a-z0-9-]{0,30}$ ]] || { echo "ERROR: name must be lower-case letters, digits, hyphens" >&2; exit 2; }
PORT=3011 API_PORT=8011 BACKEND_IMAGE="" FRONTEND_IMAGE=""
while [ $# -gt 0 ]; do
  case $1 in
    --port) PORT=$2; shift 2 ;;
    --api-port) API_PORT=$2; shift 2 ;;
    --backend-image) BACKEND_IMAGE=$2; shift 2 ;;
    --frontend-image) FRONTEND_IMAGE=$2; shift 2 ;;
    *) usage ;;
  esac
done
for p in "$PORT" "$API_PORT"; do
  [[ $p =~ ^[0-9]+$ ]] || { echo "ERROR: ports are numbers" >&2; exit 2; }
  case $p in 3001|8001|8080|8082|8764|8770|8888) echo "ERROR: port $p belongs to the main dashboard" >&2; exit 2 ;; esac
done

MAIN_PROJECT=${DASHBOARD_COMPOSE_PROJECT_NAME:-m-s-simulation-runtime-stack}
PROJECT="tevv-dash-$NAME"
STATE=".mns/dashboard-instances/$NAME"
OVERRIDE="$STATE/network.yml"

# The same environment `make dashboard` composes with.
mk() { make -s --no-print-directory --eval 'print-%: ; @echo $($*)' "print-$1"; }
eval "$(make -s --no-print-directory print-channel-env)"
. ./tools/load-images-env.sh
if [ "$(mk IMAGE_MODE)" = production ]; then
  load_images_env "./$(mk CHANNEL_ENV)"; load_images_env ./images/platform-images.generated.env
else
  load_images_env "./$(mk CHANNEL_DEV_ENV)"; load_images_env ./images/development.generated.env
fi
export MNS_IMAGE_SET_FILE="$(eval "$(mk EFFECTIVE_IMAGE_SET)")"
export MSRS_ROOT=$PWD HOST_UID=$(id -u) HOST_GID=$(id -g)
export COMPOSE_PROJECT_NAME=$PROJECT DASHBOARD_CONTAINER_PREFIX="$NAME-"
export DASHBOARD_PORT=$PORT DASHBOARD_API_PORT=$API_PORT
# The main dashboard's ros2-tools sidecar (Foxglove :8764, bag replay): its
# backend creates it, and a second one would fight it for the port.
export DASHBOARD_ROS2_CONTAINER="${MAIN_CONTAINER_PREFIX:-}ros2-tools"
[ -n "$BACKEND_IMAGE" ] && export DASHBOARD_BACKEND_IMAGE=$BACKEND_IMAGE
[ -n "$FRONTEND_IMAGE" ] && export DASHBOARD_FRONTEND_IMAGE=$FRONTEND_IMAGE

compose() { docker compose -f docker-compose-dashboard.yml -f "$OVERRIDE" "$@"; }

case $ACTION in
  up)
    docker network inspect "${MAIN_PROJECT}_default" >/dev/null 2>&1 \
      || { echo "ERROR: the main dashboard is not up (no network ${MAIN_PROJECT}_default); run ./tevv up first" >&2; exit 1; }
    mkdir -p "$STATE"
    printf 'networks:\n  default:\n    name: %s_default\n    external: true\n' "$MAIN_PROJECT" > "$OVERRIDE"
    compose up -d --no-deps dashboard-backend dashboard-frontend
    echo "Dashboard '$NAME': http://localhost:$PORT (backend :$API_PORT)"
    echo "  backend  ${DASHBOARD_BACKEND_IMAGE}"
    echo "  frontend ${DASHBOARD_FRONTEND_IMAGE}  (must be built for http://localhost:$API_PORT)"
    ;;
  down)
    [ -f "$OVERRIDE" ] || { echo "no dashboard '$NAME'"; exit 0; }
    compose rm -sf dashboard-backend dashboard-frontend
    docker rm -f "$NAME-ros2-tools" >/dev/null 2>&1 || true   # from builds before ros2-tools was shared
    rm -rf "$STATE"
    ;;
  status)
    docker ps -a --filter "label=com.docker.compose.project=$PROJECT" --format '{{.Names}}\t{{.Status}}\t{{.Image}}'
    ;;
  *) usage ;;
esac
