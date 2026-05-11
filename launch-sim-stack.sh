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

# -----------------------------------------------------------------------
# Xvfb virtual display (workaround for hybrid-GPU / HiDPI display timing)
# Provides a stable 1920x1080 X display for AirSim/Unreal instead of the
# native screen. Override with XVFB_DISPLAY=:0 to skip and use real X11.
# -----------------------------------------------------------------------
XVFB_DISPLAY="${XVFB_DISPLAY:-:99}"
XVFB_PID_FILE="/tmp/.xvfb-sim.pid"
XVFB_AUTH="/tmp/.xauth-sim"

if [ "${XVFB_DISPLAY}" != ":0" ]; then
  if ! command -v Xvfb >/dev/null 2>&1; then
    echo "WARNING: Xvfb not found. Falling back to DISPLAY=:0"
    export DISPLAY="${DISPLAY:-:0}"
    export XAUTHORITY="${XAUTHORITY:-$HOME/.Xauthority}"
  else
    # Kill any stale Xvfb on this display
    if [ -f "${XVFB_PID_FILE}" ]; then
      kill "$(cat "${XVFB_PID_FILE}")" 2>/dev/null || true
      rm -f "${XVFB_PID_FILE}"
    fi

    echo "Starting Xvfb on ${XVFB_DISPLAY} (1920x1080x24)..."
    Xvfb "${XVFB_DISPLAY}" -screen 0 1920x1080x24 -ac &
    echo $! > "${XVFB_PID_FILE}"
    sleep 1  # wait for Xvfb to be ready

    touch "${XVFB_AUTH}"
    export DISPLAY="${XVFB_DISPLAY}"
    export XAUTHORITY="${XVFB_AUTH}"
    echo "Xvfb started (PID=$(cat "${XVFB_PID_FILE}"))"
  fi
else
  export DISPLAY="${DISPLAY:-:0}"
  export XAUTHORITY="${XAUTHORITY:-$HOME/.Xauthority}"
fi

xhost +local:docker >/dev/null 2>&1 || true

echo "========================================"
echo " Simulation Runtime Stack Launcher"
echo "========================================"

echo
echo "Using host settings:"
echo "  UID=$UID"
echo "  GID=$GID"
echo "  DISPLAY=$DISPLAY"
echo "  XAUTHORITY=$XAUTHORITY"
echo

echo "Starting simulation stack..."
docker compose -f docker-compose-sim.yml up -d

echo
echo "========================================"
echo " SIM stacks started"
echo "========================================"
echo
echo "Use ./logs.sh to view logs"
echo "Use ./stop.sh to stop everything"
echo