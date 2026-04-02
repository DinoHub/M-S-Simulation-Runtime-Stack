#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ -f ".env" ]; then
  set -a
  source .env
  set +a
fi

export UID
export GID="$(id -g)"

# X11 setup (needed for AirSim / GUI containers)
export DISPLAY="${DISPLAY:-:0}"
export XAUTHORITY="${XAUTHORITY:-$HOME/.Xauthority}"

xhost +local:docker >/dev/null 2>&1 || true

start_local_planner() {
  local mode="${LOCAL_PLANNER_MODE:-disabled}"

  case "$mode" in
    disabled|"")
      echo "Local planner startup disabled."
      ;;
    external)
      echo "Using externally managed local planner."
      ;;
    managed-script)
      echo "Starting local planner via script..."
      echo "  LOCAL_PLANNER_DIR=${LOCAL_PLANNER_DIR:-}"
      echo "  LOCAL_PLANNER_START_CMD=${LOCAL_PLANNER_START_CMD:-./start.sh}"

      if [ -z "${LOCAL_PLANNER_DIR:-}" ]; then
        echo "ERROR: LOCAL_PLANNER_DIR is not set."
        exit 1
      fi
      if [ ! -d "$LOCAL_PLANNER_DIR" ]; then
        echo "ERROR: Local planner directory not found: $LOCAL_PLANNER_DIR"
        exit 1
      fi
      (
        cd "$LOCAL_PLANNER_DIR"
        bash -lc "${LOCAL_PLANNER_START_CMD:-./start.sh}"
      )
      ;;
    managed-compose)
      echo "Starting local planner via docker compose..."
      echo "  LOCAL_PLANNER_DIR=${LOCAL_PLANNER_DIR:-}"
      echo "  LOCAL_PLANNER_COMPOSE_FILE=${LOCAL_PLANNER_COMPOSE_FILE:-}"

      if [ -z "${LOCAL_PLANNER_DIR:-}" ]; then
        echo "ERROR: LOCAL_PLANNER_DIR is not set."
        exit 1
      fi
      if [ ! -d "$LOCAL_PLANNER_DIR" ]; then
        echo "ERROR: Local planner directory not found: $LOCAL_PLANNER_DIR"
        exit 1
      fi
      if [ -z "${LOCAL_PLANNER_COMPOSE_FILE:-}" ]; then
        echo "ERROR: LOCAL_PLANNER_COMPOSE_FILE is not set."
        exit 1
      fi

      if [ -n "${LOCAL_PLANNER_PROFILE:-}" ]; then
        docker compose \
          -f "${LOCAL_PLANNER_DIR}/${LOCAL_PLANNER_COMPOSE_FILE}" \
          --profile "${LOCAL_PLANNER_PROFILE}" \
          up -d
      else
        docker compose \
          -f "${LOCAL_PLANNER_DIR}/${LOCAL_PLANNER_COMPOSE_FILE}" \
          up -d
      fi
      ;;
    *)
      echo "ERROR: Unknown LOCAL_PLANNER_MODE: $mode"
      exit 1
      ;;
  esac
}

echo "========================================"
echo " Simulation Runtime Stack Launcher"
echo "========================================"

echo
echo "Using host settings:"
echo "  UID=$UID"
echo "  GID=$GID"
echo "  DISPLAY=$DISPLAY"
echo "  XAUTHORITY=$XAUTHORITY"
echo "  LOCAL_PLANNER_MODE=${LOCAL_PLANNER_MODE:-disabled}"
echo

echo "Starting monitoring stack..."./
docker compose -f docker-compose-monitoring.yml --profile monitoring up -d

echo "Starting simulation stack..."
docker compose -f docker-compose-sim.yml up -d

start_local_planner

echo "Starting metrics stack..."
docker compose -f docker-compose-metrics.yml --profile metrics up -d

echo
echo "========================================"
echo " All stacks started"
echo "========================================"
echo
echo "Grafana:      http://localhost:3000"
echo "Prometheus:   http://localhost:9090"
echo
echo "Use ./logs.sh to view logs"
echo "Use ./stop.sh to stop everything"
echo