#!/usr/bin/env bash
# Safe bringup: wait for AirSim's 4 vehicles to be alive on RPC before
# starting the SITL containers. Solves the "SITL came up before AirSim
# was warm, both stuck waiting for first packet" deadlock.
#
# Usage:
#   1. Open Unreal Editor and press Play (PIE) so AirSim spawns the 4 vehicles
#   2. Run this script from anywhere on the host
#
# Bridge variant:
#   COMPOSE_FILE=docker-compose.bridge.yml ENV_FILE=.env.bridge bash bringup_safe.sh

set -euo pipefail

COMPOSE_DIR="$(dirname "$(readlink -f "$0")")"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"
ENV_FILE="${ENV_FILE:-.env}"
RPC_HOST="${RPC_HOST:-127.0.0.1}"
RPC_PORT="${RPC_PORT:-41451}"
EXPECTED_VEHICLES=("Copter1" "Copter2" "Copter3" "Copter4")
TIMEOUT_SEC="${TIMEOUT_SEC:-120}"

cd "$COMPOSE_DIR"

echo "[bringup_safe] stopping any leftover containers..."
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" down --remove-orphans 2>&1 | tail -5 || true

# Check AirSim has bound the 4 per-vehicle UDP listeners on the host.
# We DON'T use listVehicles RPC — it gets starved by AirSim's busy-loop in
# recvRotorControl() until the first SITL handshake completes.
EXPECTED_UDP_PORTS=(9002 9012 9022 9032)
echo "[bringup_safe] waiting up to ${TIMEOUT_SEC}s for AirSim to bind UDP ${EXPECTED_UDP_PORTS[*]} (means PIE is playing)..."
deadline=$(( $(date +%s) + TIMEOUT_SEC ))

while [ "$(date +%s)" -lt "$deadline" ]; do
  bound=0
  for p in "${EXPECTED_UDP_PORTS[@]}"; do
    ss -ulnp 2>/dev/null | grep -q "127\.0\.0\.1:${p} " && bound=$((bound + 1))
  done
  if [ "$bound" -eq "${#EXPECTED_UDP_PORTS[@]}" ]; then
    echo "[bringup_safe] AirSim bound all 4 UDP listeners — PIE is playing."
    break
  fi
  echo "  (waiting... ${bound}/${#EXPECTED_UDP_PORTS[@]} UDP listeners bound)"
  sleep 2
done

if [ "$bound" -ne "${#EXPECTED_UDP_PORTS[@]}" ]; then
  echo "[bringup_safe] timed out — only ${bound}/${#EXPECTED_UDP_PORTS[@]} listeners. Press Play in Unreal Editor and re-run."
  exit 1
fi

echo "[bringup_safe] starting SITL + ros2 + qgc containers..."
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d

echo
echo "[bringup_safe] sleeping 8s for SITLs to handshake..."
sleep 8

echo
echo "[bringup_safe] sensor handshake check (drone-0 last 3 lines):"
docker logs --tail 3 ardupilot-xfs-drone-0 2>&1 | tail -3

echo
echo "[bringup_safe] AirSim recv error rate (last 200 log lines):"
ERR_COUNT=$(tail -200 /home/mnsuser/Cosys_Airsim_Exploration/projects/xfs/Saved/Logs/Xfs.log 2>/dev/null | grep -c "Error while receiving rotor" || true)
echo "  ${ERR_COUNT}/200 lines are recv errors (0 means handshake succeeded)"

echo
echo "[bringup_safe] done. Use 'docker compose -f $COMPOSE_FILE logs -f' to tail."
