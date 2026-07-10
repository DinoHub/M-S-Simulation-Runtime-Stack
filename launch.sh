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
Usage: ./launch.sh [scenario] [--editor] [--headless] [--with-agent-external] [--with-pixel-streaming] [--with-monitoring] [--with-metrics] [--all]

Scenarios (compose/<scenario>/docker-compose.yml):
$(ls compose/ | sed 's/^/  /')

By default only the simulation stack is started.
Pass --with-monitoring and/or --with-metrics to add cross-cutting stacks,
or --all as a shortcut for both. These can also be enabled in .env via
START_MONITORING=true / START_METRICS=true.

--editor skips the containerized AirSim entirely (the `sim` compose profile)
so you can run AirSim from the Unreal editor on the host instead. Everything
else (autopilot SITL, per-drone bridges, mavros, QGroundControl) still starts
and connects to the editor's RPC on localhost:41451 (host networking). The
launcher blocks until that RPC is listening (Play the level in the editor)
before starting the bridges, since they query RPC once with no retry — a
closed port would leave sensor topics with 0 publishers. Wait window is
EDITOR_RPC_TIMEOUT seconds (default 180; set 0 to wait forever).
Equivalent .env knob: EDITOR_MODE=true.

--headless runs the containerized AirSim with -RenderOffScreen (no window,
GPU still renders for cameras and PixelStreaming). Currently only consumed
by the ardupilot-xfs scenario; ignored by others. Equivalent .env knob:
AIRSIM_HEADLESS=true.

--with-agent-external (ardupilot-xfs default flow only) also starts the
per-drone zenoh bridges (zenoh-bridge-{1..N}) onto agent_external for
/shared/* topic routing. Without this flag, the per-drone airsim_bridge_dN
containers run DDS-only on agent_internal-N.
Equivalent .env knob: WITH_AGENT_EXTERNAL=true.

--with-pixel-streaming (ardupilot-xfs, px4-condo) starts the
pixel-streaming-signalling sidecar AND tells UE5 to dial it
(-PixelStreamingURL). Default off — browser viewer disabled. View at
http://localhost:\${PS_HTTP_PORT:-80} when enabled.
Equivalent .env knob: ENABLE_PIXEL_STREAMING=true.

Scenarios with a compose/<scenario>/templates/ dir are generated from Jinja
templates via tools/generate_scenario.py (drone count via NUM_DRONES in .env,
default 4); the launcher regenerates them automatically on drift.
EOF
}

SCENARIO="${SCENARIO:-}"
START_MONITORING="${START_MONITORING:-false}"
START_METRICS="${START_METRICS:-false}"
AIRSIM_HEADLESS="${AIRSIM_HEADLESS:-false}"
WITH_AGENT_EXTERNAL="${WITH_AGENT_EXTERNAL:-false}"
ENABLE_PIXEL_STREAMING="${ENABLE_PIXEL_STREAMING:-false}"
EDITOR_MODE="${EDITOR_MODE:-false}"

for arg in "$@"; do
  case "$arg" in
    --editor)              EDITOR_MODE=true ;;
    --with-monitoring)     START_MONITORING=true ;;
    --with-metrics)        START_METRICS=true ;;
    --all)                 START_MONITORING=true; START_METRICS=true ;;
    --headless)            AIRSIM_HEADLESS=true ;;
    --with-agent-external) WITH_AGENT_EXTERNAL=true ;;
    --with-pixel-streaming) ENABLE_PIXEL_STREAMING=true ;;
    -h|--help)             usage; exit 0 ;;
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

# Bridge-architecture toggle (consumed in the COMPOSE_PROFILE_ARGS block below).
export WITH_AGENT_EXTERNAL

# PixelStreaming toggle (gates both the sidecar profile and the
# -PixelStreamingURL flag inside the airsim container's bash wrapper).
export ENABLE_PIXEL_STREAMING

# ardupilot-xfs default flow needs N agent_internal-N docker networks
# pre-created (the per-drone airsim_bridge_dN + zenoh-bridge-N services
# attach to them). They're declared in the compose file but with
# `name:` set, so creating them up front is idempotent and avoids a
# project-prefix mismatch when the autonomy team's compose attaches
# autonomy_stack-N to the same networks. NUM_DRONES comes from .env;
# AGENT_INTERNAL_SUBNET_BASE controls the /24 prefix.
ensure_agent_internal_networks() {
  local count="${NUM_DRONES:-4}"
  local base="${AGENT_INTERNAL_SUBNET_BASE:-172.28}"
  for n in $(seq 1 "$count"); do
    local expected="${base}.${n}.0/24"
    local existing
    if existing=$(docker network inspect "agent_internal-${n}" \
        --format '{{range .IPAM.Config}}{{.Subnet}}{{end}}' 2>/dev/null); then
      # Network exists. Surface its actual subnet so a mismatch with
      # AGENT_INTERNAL_SUBNET_BASE (likely autonomy-owned) is visible.
      if [ -z "$existing" ]; then
        echo "  agent_internal-${n}: exists (no IPAM subnet reported)"
      elif [ "$existing" = "$expected" ]; then
        echo "  agent_internal-${n}: ${existing} (already exists)"
      else
        echo "  agent_internal-${n}: ${existing} (expected ${expected}) — using existing (likely autonomy-owned)"
      fi
    else
      docker network create \
        --subnet="${expected}" \
        --gateway="${base}.${n}.254" \
        "agent_internal-${n}" >/dev/null
      echo "  agent_internal-${n}: created at ${expected}"
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
echo "  WITH_AGENT_EXTERNAL=$WITH_AGENT_EXTERNAL"
echo "  ENABLE_PIXEL_STREAMING=$ENABLE_PIXEL_STREAMING"
echo "  NUM_DRONES=${NUM_DRONES:-4}"
echo

if [ "$START_MONITORING" = "true" ]; then
  echo "Starting monitoring + logs stack..."
  # docker-compose-logs.yml is an OVERLAY: it adds Loki + Alloy (profile logs)
  # and merges a Loki datasource onto monitoring's grafana. It must be passed
  # alongside the monitoring file with both profiles, or grafana's depends_on
  # loki resolves to an undefined service.
  docker compose \
    -f docker-compose-monitoring.yml \
    -f docker-compose-logs.yml \
    --profile monitoring --profile logs up -d
fi

# Regenerate scenario files from Jinja templates if needed.
# Generic: any scenario with a compose/<scenario>/templates/ dir is
# generator-managed. Idempotent: --check exits 0 when outputs match
# templates and .env, so the generator only writes on drift (e.g.,
# NUM_DRONES changed).
if [ -d "compose/${SCENARIO}/templates" ] && [ -f "$SCRIPT_DIR/tools/generate_scenario.py" ]; then
  if ! python3 "$SCRIPT_DIR/tools/generate_scenario.py" --scenario "$SCENARIO" --check >/dev/null 2>&1; then
    echo "Regenerating ${SCENARIO} scenario files (drift detected)..."
    python3 "$SCRIPT_DIR/tools/generate_scenario.py" --scenario "$SCENARIO"
  fi
fi

# Pick profiles for ardupilot-xfs and px4-condo. Other scenarios don't have
# profiles wired up yet — pass through unchanged.
COMPOSE_PROFILE_ARGS=()

# The containerized AirSim sim service is gated behind the `sim` profile so
# --editor can drop it (run AirSim from the Unreal editor on the host). Default
# flow always enables `sim`. Every scenario's sim service carries
# `profiles: ["sim"]` and every bridge depends_on it with `required: false`, so
# omitting the profile cleanly skips the container without breaking startup.
if [ "$EDITOR_MODE" = "true" ]; then
  echo "AirSim source: Unreal editor on host (sim container skipped — launcher will wait for editor RPC before bridges)."
else
  COMPOSE_PROFILE_ARGS+=(--profile sim)
fi

if [ "$SCENARIO" = "ardupilot-xfs" ]; then
  ensure_agent_internal_networks
  COMPOSE_PROFILE_ARGS+=(--profile per-drone-bridge)
  if [ "$WITH_AGENT_EXTERNAL" = "true" ]; then
    COMPOSE_PROFILE_ARGS+=(--profile agent-external)
    echo "Bridge architecture: per-drone + agent_external zenoh bridges"
  else
    echo "Bridge architecture: per-drone (no agent_external bridge — pass --with-agent-external to add it)"
  fi
fi

# Pixel-streaming profile applies to scenarios that ship the
# pixel-streaming-signalling sidecar (ardupilot-xfs, px4-condo).
case "$SCENARIO" in
  ardupilot-xfs|px4-condo)
    if [ "$ENABLE_PIXEL_STREAMING" = "true" ]; then
      COMPOSE_PROFILE_ARGS+=(--profile pixel-streaming)
      echo "Pixel streaming: enabled (signalling sidecar + UE5 dials it)"
    else
      echo "Pixel streaming: disabled (pass --with-pixel-streaming to enable)"
    fi
    ;;
esac

# Editor-mode guard: the per-drone bridges query AirSim's RPC port exactly
# once with no retry (a closed port leaves sensor topics with 0 publishers).
# In --editor mode there's no sim container whose service_healthy we can gate
# on, so block here until the editor's RPC (Play-in-editor) is actually
# listening before bringing the bridges up. Skipped in default mode (the
# containerized sim's healthcheck handles ordering).
rpc_open() {  # $1 host $2 port — true when a TCP connect succeeds
  if command -v nc >/dev/null 2>&1; then
    nc -z -w1 "$1" "$2" >/dev/null 2>&1
  else
    timeout 1 bash -c "exec 3<>/dev/tcp/$1/$2" >/dev/null 2>&1
  fi
}
if [ "$EDITOR_MODE" = "true" ]; then
  RPC_HOST="${AIRSIM_HOST_IP:-127.0.0.1}"
  RPC_PORT="${AIRSIM_RPC_PORT:-41451}"
  RPC_TIMEOUT="${EDITOR_RPC_TIMEOUT:-180}"
  echo "Editor mode: waiting for AirSim RPC at ${RPC_HOST}:${RPC_PORT} — Play the level in the Unreal editor."
  echo "  (timeout ${RPC_TIMEOUT}s, override with EDITOR_RPC_TIMEOUT=0 to wait forever; Ctrl-C aborts)"
  waited=0
  until rpc_open "$RPC_HOST" "$RPC_PORT"; do
    if [ "$RPC_TIMEOUT" -ne 0 ] && [ "$waited" -ge "$RPC_TIMEOUT" ]; then
      echo "ERROR: AirSim RPC at ${RPC_HOST}:${RPC_PORT} not reachable after ${RPC_TIMEOUT}s."
      echo "       Is the level playing in the Unreal editor? Aborting before bridges start."
      exit 1
    fi
    sleep 2; waited=$((waited + 2))
  done
  echo "AirSim RPC up at ${RPC_HOST}:${RPC_PORT} — starting the rest of the stack."
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
