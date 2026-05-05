#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ -f ".env" ]; then
  set -a
  source .env
  set +a
fi

usage() {
  cat <<EOF
Usage: ./launch.sh [scenario] [--with-monitoring] [--with-metrics] [--all]

Scenarios (compose/<scenario>/docker-compose.yml):
$(ls compose/ | sed 's/^/  /')

By default only the simulation stack is started.
Pass --with-monitoring and/or --with-metrics to add cross-cutting stacks,
or --all as a shortcut for both. These can also be enabled in .env via
START_MONITORING=true / START_METRICS=true.
EOF
}

SCENARIO=""
START_MONITORING="${START_MONITORING:-false}"
START_METRICS="${START_METRICS:-false}"

for arg in "$@"; do
  case "$arg" in
    --with-monitoring) START_MONITORING=true ;;
    --with-metrics)    START_METRICS=true ;;
    --all)             START_MONITORING=true; START_METRICS=true ;;
    -h|--help)         usage; exit 0 ;;
    --*)               echo "ERROR: Unknown flag: $arg"; usage; exit 1 ;;
    *)
      if [ -z "$SCENARIO" ]; then
        SCENARIO="$arg"
      else
        echo "ERROR: Unexpected positional arg: $arg"
        usage
        exit 1
      fi
      ;;
  esac
done

SCENARIO="${SCENARIO:-${SCENARIO_DEFAULT:-${SCENARIO:-px4-condo}}}"
SCENARIO_FILE="compose/${SCENARIO}/docker-compose.yml"

if [ ! -f "$SCENARIO_FILE" ]; then
  echo "ERROR: Unknown scenario: $SCENARIO"
  usage
  exit 1
fi

export UID
export GID="$(id -g)"

# X11 setup (needed for AirSim / GUI containers)
export DISPLAY="${DISPLAY:-:0}"
export XAUTHORITY="${XAUTHORITY:-$HOME/.Xauthority}"

# Resolve CONFIG_ROOT to an absolute path so subdirectory compose files
# resolve mounts correctly regardless of working directory.
CONFIG_ROOT="${CONFIG_ROOT:-./config}"
export CONFIG_ROOT="$(cd "$CONFIG_ROOT" && pwd)"

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
echo "  SCENARIO=$SCENARIO"
echo "  CONFIG_ROOT=$CONFIG_ROOT"
echo "  UID=$UID"
echo "  GID=$GID"
echo "  DISPLAY=$DISPLAY"
echo "  XAUTHORITY=$XAUTHORITY"
echo "  LOCAL_PLANNER_MODE=${LOCAL_PLANNER_MODE:-disabled}"
echo "  START_MONITORING=$START_MONITORING"
echo "  START_METRICS=$START_METRICS"
echo

if [ "$START_MONITORING" = "true" ]; then
  echo "Starting monitoring stack..."
  docker compose -f docker-compose-monitoring.yml --profile monitoring up -d
fi

echo "Starting simulation stack ($SCENARIO)..."
docker compose --project-directory "$SCRIPT_DIR" -f "$SCENARIO_FILE" up -d

start_local_planner

if [ "$START_METRICS" = "true" ]; then
  echo "Starting metrics stack..."
  docker compose -f docker-compose-metrics.yml --profile metrics up -d
fi

echo
echo "========================================"
echo " Stacks started"
echo "========================================"
echo
if [ "$START_MONITORING" = "true" ]; then
  echo "Grafana:      http://localhost:3000"
  echo "Prometheus:   http://localhost:9090"
fi
echo "Use ./logs.sh to view logs"
echo "Use ./stop.sh to stop everything"
echo
