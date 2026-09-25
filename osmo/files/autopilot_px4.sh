#!/bin/bash
set -e
# The generated compose sleeps 20 s here. A fixed sleep is a guess
# that is either wasted time or not enough; wait for the port.
deadline=$(( $(date +%s) + SIM_DIAL_WAIT_SEC ))
until (exec 3<>"/dev/tcp/${SIM_HOST}/${SIM_PORT}") 2>/dev/null; do
  if [ "$(date +%s)" -ge "$deadline" ]; then
    echo "READINESS TIMEOUT: ${SIM_HOST}:${SIM_PORT}" >&2
    exit 42
  fi
  sleep 5
done
PX4_SIM_HOSTNAME=$(getent hosts "$SIM_HOST" | awk '{print $1; exit}')
[ -n "$PX4_SIM_HOSTNAME" ] || { echo "cannot resolve $SIM_HOST" >&2; exit 42; }
export PX4_SIM_HOSTNAME
echo "sim $SIM_HOST -> $PX4_SIM_HOSTNAME; starting PX4 SITL"
cd /px4_workspace/PX4-Autopilot
exec ./Scripts/run_airsim_sitl.sh 0
