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

stop_local_planner() {
  local mode="${LOCAL_PLANNER_MODE:-disabled}"

  case "$mode" in
    disabled|""|external)
      ;;
    managed-script)
      echo "Stopping local planner via script..."
      echo "  LOCAL_PLANNER_DIR=${LOCAL_PLANNER_DIR:-}"
      echo "  LOCAL_PLANNER_STOP_CMD=${LOCAL_PLANNER_STOP_CMD:-./stop.sh}"

      if [ -n "${LOCAL_PLANNER_DIR:-}" ] && [ -d "${LOCAL_PLANNER_DIR}" ]; then
        (
          cd "$LOCAL_PLANNER_DIR"
          bash -lc "${LOCAL_PLANNER_STOP_CMD:-./stop.sh}"
        ) || true
      else
        echo "Skipping local planner stop: LOCAL_PLANNER_DIR missing."
      fi
      ;;
    managed-compose)
      echo "Stopping local planner via docker compose..."
      echo "  LOCAL_PLANNER_DIR=${LOCAL_PLANNER_DIR:-}"
      echo "  LOCAL_PLANNER_COMPOSE_FILE=${LOCAL_PLANNER_COMPOSE_FILE:-}"

      if [ -n "${LOCAL_PLANNER_DIR:-}" ] && [ -d "${LOCAL_PLANNER_DIR}" ] && [ -n "${LOCAL_PLANNER_COMPOSE_FILE:-}" ]; then
        if [ -n "${LOCAL_PLANNER_PROFILE:-}" ]; then
          docker compose \
            -f "${LOCAL_PLANNER_DIR}/${LOCAL_PLANNER_COMPOSE_FILE}" \
            --profile "${LOCAL_PLANNER_PROFILE}" \
            down --remove-orphans || true
        else
          docker compose \
            -f "${LOCAL_PLANNER_DIR}/${LOCAL_PLANNER_COMPOSE_FILE}" \
            down --remove-orphans || true
        fi
      else
        echo "Skipping local planner stop: compose settings incomplete."
      fi
      ;;
    *)
      echo "Unknown LOCAL_PLANNER_MODE: $mode"
      ;;
  esac
}

echo "Stopping metrics stack..."
docker compose -f docker-compose-metrics.yml --profile metrics down --remove-orphans || true

stop_local_planner

echo "Stopping simulation stack..."
docker compose -f docker-compose-sim.yml down --remove-orphans || true

echo "Stopping monitoring stack..."
docker compose -f docker-compose-monitoring.yml --profile monitoring down --remove-orphans || true

echo
echo "All stacks stopped."