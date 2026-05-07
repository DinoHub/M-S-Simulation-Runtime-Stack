#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ -f ".env" ]; then
  set -a
  source .env
  set +a
fi

SCENARIO="${1:-${SCENARIO:-}}"

# If no scenario was explicitly passed, auto-detect the running one by
# probing each compose/*/docker-compose.yml. Falls back to px4-condo.
if [ -z "$SCENARIO" ]; then
  for candidate in compose/*/docker-compose.yml; do
    if docker compose --project-directory "$SCRIPT_DIR" -f "$candidate" ps -q 2>/dev/null | grep -q .; then
      SCENARIO="$(basename "$(dirname "$candidate")")"
      break
    fi
  done
  SCENARIO="${SCENARIO:-px4-condo}"
fi

SCENARIO_FILE="compose/${SCENARIO}/docker-compose.yml"

if [ ! -f "$SCENARIO_FILE" ]; then
  echo "ERROR: Unknown scenario: $SCENARIO"
  echo "Available scenarios:"
  ls compose/ | sed 's/^/  /'
  exit 1
fi

export UID
export GID="$(id -g)"

CONFIG_ROOT="${CONFIG_ROOT:-./config}"
export CONFIG_ROOT="$(cd "$CONFIG_ROOT" && pwd)"

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

echo "Stopping simulation stack ($SCENARIO)..."
# For ardupilot-xfs, activate ALL bridge-architecture profiles on `down`
# so containers from any path (default per-drone, --with-agent-external,
# or --legacy-bridge) get cleaned up regardless of which one was running.
if [ "$SCENARIO" = "ardupilot-xfs" ]; then
  docker compose --project-directory "$SCRIPT_DIR" -f "$SCENARIO_FILE" \
    --profile per-drone-bridge --profile agent-external --profile legacy-bridge \
    down --remove-orphans || true
else
  docker compose --project-directory "$SCRIPT_DIR" -f "$SCENARIO_FILE" \
    down --remove-orphans || true
fi

echo "Stopping monitoring stack..."
docker compose -f docker-compose-monitoring.yml --profile monitoring down --remove-orphans || true

echo
echo "All stacks stopped."
