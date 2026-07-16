#!/usr/bin/env bash
# End-to-end MAVROS-on-agent_internal-N test. Brings up four mavros_dN
# containers (one per drone) and runs a real arm→takeoff→setpoint→land
# mission inside each, verifying the FCU actually responds to commands
# sent from agent_internal-N.
#
# Prereq: ./launch.sh ardupilot-xfs is up (per-drone airsim_bridge_dN
# containers running).
#
# Usage:
#   ./test-per-drone-mavros.sh                # parallel (default), all 4
#   ./test-per-drone-mavros.sh --sequential   # one drone at a time
#   ./test-per-drone-mavros.sh --teardown     # stop the mavros containers
#
# Pass-through env (read by the per-drone python script):
#   TARGET_ALT          - takeoff altitude (default 5.0 m)
#   SETPOINT_DURATION   - seconds of velocity setpoint (default 8.0 s)
#   MOVEMENT_THRESHOLD  - minimum displacement to PASS (default 1.0 m)

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"   # so docker-compose.mavros-test.yml's ${PWD}-relative bind mount resolves

# Source root .env so NUM_DRONES (and any per-drone overrides) propagate
# to compose substitution and the loops below. Falls back to 4 if not set.
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
if [ -f "$REPO_ROOT/.env" ]; then
  set -a; source "$REPO_ROOT/.env"; set +a
fi
NUM_DRONES="${NUM_DRONES:-4}"

WAIT_SEC="${WAIT_SEC:-25}"
TARGET_ALT="${TARGET_ALT:-5.0}"
SETPOINT_DURATION="${SETPOINT_DURATION:-8.0}"
MOVEMENT_THRESHOLD="${MOVEMENT_THRESHOLD:-1.0}"

cmd="${1:-run}"
case "$cmd" in
  --teardown|teardown)
    docker compose -f docker-compose.mavros-test.yml down --remove-orphans 2>/dev/null || true
    echo "torn down"
    exit 0
    ;;
  --sequential)
    MODE="sequential"
    ;;
  *)
    MODE="parallel"
    ;;
esac

echo "=== pre-flight: airsim_bridge_d{1..${NUM_DRONES}} must be running ==="
for n in $(seq 1 "$NUM_DRONES"); do
  if ! docker ps --format '{{.Names}}' | grep -qx "airsim_bridge_d${n}"; then
    echo "ERROR: airsim_bridge_d${n} not running."
    echo "       Run: ./launch.sh ardupilot-xfs"
    exit 1
  fi
done
echo "  OK — all ${NUM_DRONES} per-drone bridges up"

echo
echo "=== bringing up mavros_d{1..4} ==="
docker compose -f docker-compose.mavros-test.yml up -d

echo
echo "=== waiting ${WAIT_SEC}s for MAVROS↔FCU connections ==="
sleep "$WAIT_SEC"

# Per-drone result files: subshells in parallel mode can't write back to a
# parent-shell array, so we drop a status file per drone and read those after
# `wait`. Cleaned up on exit.
RESULTS_DIR="$(mktemp -d -t mavros-test-XXXXXX)"
trap 'rm -rf "$RESULTS_DIR"' EXIT

run_one() {
  local n="$1"
  local outfile="${RESULTS_DIR}/d${n}.txt"
  local rc=0
  docker exec "mavros_d${n}" bash -lc \
    "source /opt/ros/humble/setup.bash && source /ws/install/setup.bash && \
     python3 /scripts/test_one_drone_mavros.py \
       --vehicle Copter${n} \
       --target-altitude ${TARGET_ALT} \
       --setpoint-duration ${SETPOINT_DURATION} \
       --movement-threshold ${MOVEMENT_THRESHOLD} 2>&1" > "$outfile" || rc=$?
  echo "$rc" > "${outfile}.rc"
  # Mirror the MOVE/FAIL line to stdout so users see live progress.
  grep -E '^(MOVE|FAIL):' "$outfile" | sed "s/^/  Copter${n}: /" || true
}

echo
echo "=== running missions (${MODE}) ==="
if [ "$MODE" = "sequential" ]; then
  for n in $(seq 1 "$NUM_DRONES"); do
    echo "--- Copter${n} ---"
    run_one "$n"
  done
else
  for n in $(seq 1 "$NUM_DRONES"); do
    run_one "$n" &
  done
  wait
fi

echo
echo "=== summary ==="
fail=0
for n in $(seq 1 "$NUM_DRONES"); do
  rc="$(cat "${RESULTS_DIR}/d${n}.txt.rc" 2>/dev/null || echo 99)"
  msg="$(grep -E '^(MOVE|FAIL):' "${RESULTS_DIR}/d${n}.txt" 2>/dev/null | head -1 || echo '(no output)')"
  case "$rc" in
    0) status="PASS" ;;
    1) status="FAIL (no move)" ;;
    2) status="FAIL (plumbing)" ;;
    *) status="FAIL (rc=$rc)" ;;
  esac
  echo "  Copter${n}: ${status} — ${msg}"
  [ "$rc" -ne 0 ] && fail=1
done

if [ "$fail" -eq 0 ]; then
  echo
  echo "=== ALL DRONES MOVED ==="
else
  echo
  echo "=== SOME DRONES FAILED — see logs above ==="
  echo "    docker logs mavros_dN  for the failing drone(s)"
fi
exit "$fail"
