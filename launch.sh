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
Usage: ./launch.sh [scenario] [--headless] [--legacy-bridge] [--with-agent-external] [--with-monitoring] [--with-metrics] [--all]

Scenarios (compose/<scenario>/docker-compose.yml):
$(ls compose/ | sed 's/^/  /')

By default only the simulation stack is started.
Pass --with-monitoring and/or --with-metrics to add cross-cutting stacks,
or --all as a shortcut for both. These can also be enabled in .env via
START_MONITORING=true / START_METRICS=true.

--headless runs the containerized AirSim with -RenderOffScreen (no window,
GPU still renders for cameras and PixelStreaming). Currently only consumed
by the ardupilot-xfs scenario; ignored by others. Equivalent .env knob:
AIRSIM_HEADLESS=true.

--legacy-bridge (ardupilot-xfs only) brings up the legacy single-container
ros2-x11-node + sim-router stack instead of the per-drone airsim_bridge_dN
path. Equivalent .env knob: LEGACY_BRIDGE=true. See
compose/ardupilot-xfs/README.md for the architectural difference.

--with-agent-external (ardupilot-xfs default flow only) also starts the
four per-drone zenoh bridges (zenoh-bridge-{1..4}) onto agent_external for
/shared/* topic routing. Without this flag, the per-drone airsim_bridge_dN
containers run DDS-only on agent_internal-N. Incompatible with
--legacy-bridge (legacy path already brings sim-router on agent_external).
Equivalent .env knob: WITH_AGENT_EXTERNAL=true.
EOF
}

SCENARIO=""
START_MONITORING="${START_MONITORING:-false}"
START_METRICS="${START_METRICS:-false}"
AIRSIM_HEADLESS="${AIRSIM_HEADLESS:-false}"
LEGACY_BRIDGE="${LEGACY_BRIDGE:-false}"
WITH_AGENT_EXTERNAL="${WITH_AGENT_EXTERNAL:-false}"

for arg in "$@"; do
  case "$arg" in
    --with-monitoring)    START_MONITORING=true ;;
    --with-metrics)       START_METRICS=true ;;
    --all)                START_MONITORING=true; START_METRICS=true ;;
    --headless)           AIRSIM_HEADLESS=true ;;
    --legacy-bridge)      LEGACY_BRIDGE=true ;;
    --with-agent-external) WITH_AGENT_EXTERNAL=true ;;
    -h|--help)            usage; exit 0 ;;
    --*)                  echo "ERROR: Unknown flag: $arg"; usage; exit 1 ;;
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

# Display-mode toggle for ardupilot-xfs's airsim-xfs container.
export AIRSIM_HEADLESS

# Bridge-architecture toggles (consumed in the COMPOSE_PROFILE_ARGS block below).
export LEGACY_BRIDGE
export WITH_AGENT_EXTERNAL

# ardupilot-xfs default flow needs four agent_internal-N docker networks
# pre-created (the per-drone airsim_bridge_dN + zenoh-bridge-N services
# attach to them). They're declared in the compose file but with
# `name:` set, so creating them up front is idempotent and avoids a
# project-prefix mismatch when the autonomy team's compose attaches
# autonomy_stack-N to the same networks.
ensure_agent_internal_networks() {
  for n in 1 2 3 4; do
    if ! docker network inspect "agent_internal-${n}" >/dev/null 2>&1; then
      docker network create \
        --subnet="172.28.${n}.0/24" \
        --gateway="172.28.${n}.254" \
        "agent_internal-${n}" >/dev/null
      echo "  created agent_internal-${n}"
    fi
  done
}

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
echo "  AIRSIM_HEADLESS=$AIRSIM_HEADLESS"
echo "  LEGACY_BRIDGE=$LEGACY_BRIDGE"
echo "  WITH_AGENT_EXTERNAL=$WITH_AGENT_EXTERNAL"
echo

# Reject incoherent combination: --legacy-bridge already brings sim-router
# (legacy zenoh on agent_external). Adding the per-drone zenoh bridges on
# top would attach two parallel sets of zenoh peers to the same mesh.
if [ "$LEGACY_BRIDGE" = "true" ] && [ "$WITH_AGENT_EXTERNAL" = "true" ]; then
  echo "ERROR: --legacy-bridge and --with-agent-external are incompatible." >&2
  echo "       Legacy already includes sim-router on agent_external." >&2
  exit 1
fi

if [ "$START_MONITORING" = "true" ]; then
  echo "Starting monitoring stack..."
  docker compose -f docker-compose-monitoring.yml --profile monitoring up -d
fi

# Pick profiles for ardupilot-xfs. Other scenarios don't have profiles wired
# up yet — pass through unchanged.
COMPOSE_PROFILE_ARGS=()
if [ "$SCENARIO" = "ardupilot-xfs" ]; then
  if [ "$LEGACY_BRIDGE" = "true" ]; then
    COMPOSE_PROFILE_ARGS=(--profile legacy-bridge)
    echo "Bridge architecture: LEGACY (ros2-x11-node + sim-router)"
  else
    ensure_agent_internal_networks
    COMPOSE_PROFILE_ARGS=(--profile per-drone-bridge)
    if [ "$WITH_AGENT_EXTERNAL" = "true" ]; then
      COMPOSE_PROFILE_ARGS+=(--profile agent-external)
      echo "Bridge architecture: per-drone + agent_external zenoh bridges"
    else
      echo "Bridge architecture: per-drone (no agent_external bridge — pass --with-agent-external to add it)"
    fi
  fi
fi

echo "Starting simulation stack ($SCENARIO)..."
docker compose --project-directory "$SCRIPT_DIR" -f "$SCENARIO_FILE" \
  "${COMPOSE_PROFILE_ARGS[@]}" up -d

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
