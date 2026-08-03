#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ -f ".env" ]; then
  set -a
  source .env
  set +a
fi

if [[ "${1:-}" == "--stack" ]]; then
  if [[ -z "${2:-}" ]]; then
    echo "ERROR: --stack requires a generated stack directory"
    exit 1
  fi
  stack="$2"
  shift 2
  exec "$SCRIPT_DIR/tools/stack-generator-image/run_image.sh" stop --stack "$stack" "$@"
fi

# Explicit target comes ONLY from the CLI arg ($1, e.g. `make stop SCENARIO=x`).
# We deliberately do NOT fall back to $SCENARIO from the sourced .env: .env pins
# the scenario to *launch*, but `make stop` with no arg must stop EVERY running
# scenario (below), not just the one .env happens to name.
SCENARIO="${1:-}"

# When a scenario IS passed explicitly, validate it up front. When it isn't,
# teardown auto-detects and stops EVERY running scenario below (no default
# fallback — nothing running means nothing to stop).
if [ -n "$SCENARIO" ] && [ ! -f "compose/${SCENARIO}/docker-compose.yml" ]; then
  echo "ERROR: Unknown scenario: $SCENARIO"
  echo "Available scenarios:"
  ls compose/ | sed 's/^/  /'
  exit 1
fi

export UID
export GID="$(id -g)"

CONFIG_ROOT="${CONFIG_ROOT:-./config}"
export CONFIG_ROOT="$(cd "$CONFIG_ROOT" && pwd)"

# Full profile superset. `docker compose down` only removes services in the
# active profiles (+ profileless services), so every optional profile must be
# named or its containers orphan: sim (AirSim, all scenarios), per-drone-bridge
# / agent-external (ardupilot-xfs bridges + zenoh), pixel-streaming (condo
# sidecar). Profiles a given scenario doesn't define are no-ops.
ALL_PROFILE_ARGS=(--profile sim --profile per-drone-bridge \
  --profile agent-external --profile pixel-streaming)

# Tear down one scenario's compose project with every optional profile active.
down_scenario() {
  local scenario_file="compose/$1/docker-compose.yml"
  [ -f "$scenario_file" ] || return 0
  echo "Stopping simulation stack ($1)..."
  docker compose --project-directory "$SCRIPT_DIR" -f "$scenario_file" \
    "${ALL_PROFILE_ARGS[@]}" down --remove-orphans || true
}

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

# Explicit scenario → stop just that one. No scenario → auto-detect and stop
# EVERY running scenario (probe with the full profile set so a scenario running
# only its profiled sim container is still seen; do NOT break on first match).
if [ -n "$SCENARIO" ]; then
  down_scenario "$SCENARIO"
else
  stopped_any=false
  for candidate in compose/*/docker-compose.yml; do
    name="$(basename "$(dirname "$candidate")")"
    if docker compose --project-directory "$SCRIPT_DIR" -f "$candidate" \
         "${ALL_PROFILE_ARGS[@]}" ps -q 2>/dev/null | grep -q .; then
      down_scenario "$name"
      stopped_any=true
    fi
  done
  [ "$stopped_any" = true ] || echo "No running simulation scenario detected."
fi

echo "Stopping monitoring + logs stack..."
# Mirror launch.sh: tear down the logs overlay (Loki + Alloy, profile logs)
# alongside monitoring so they don't orphan.
docker compose \
  -f docker-compose-monitoring.yml \
  -f docker-compose-logs.yml \
  --profile monitoring --profile logs down --remove-orphans || true

echo
echo "All stacks stopped."
