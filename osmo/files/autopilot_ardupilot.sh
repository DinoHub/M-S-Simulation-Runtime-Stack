#!/bin/bash
set -e
deadline=$(( $(date +%s) + SIM_DIAL_WAIT_SEC ))
until (exec 3<>"/dev/tcp/${SIM_HOST}/${SIM_PORT}") 2>/dev/null; do
  if [ "$(date +%s)" -ge "$deadline" ]; then
    echo "READINESS TIMEOUT: ${SIM_HOST}:${SIM_PORT}" >&2
    exit 42
  fi
  sleep 5
done
# An address, not a name: the same reason PX4 needs one here.
ARDUPILOT_SIM_HOSTNAME=$(getent hosts "$SIM_HOST" | awk '{print $1; exit}')
[ -n "$ARDUPILOT_SIM_HOSTNAME" ] || { echo "cannot resolve $SIM_HOST" >&2; exit 42; }
export ARDUPILOT_SIM_HOSTNAME
echo "sim $SIM_HOST -> $ARDUPILOT_SIM_HOSTNAME; starting ArduPilot SITL instance ${INSTANCE_NUM}"
cd /ardupilot_workspace
# The launch script directly, not docker-entrypoint.sh.
#
# That entrypoint drops to uid 1000 with gosu whenever it starts as
# root, which is what a pod does and what compose never does -- the
# generated compose sets `user: ${HOST_UID}:${GID}`, so the check
# never fires there. Under OSMO the dropped process loads its
# parameters and exits 1 with nothing on stderr; the same binary,
# same arguments, same pod, run as root, flies (FPS 333, sensor
# data flowing from the sim pod). Isolated by running both inside a
# held task container.
#
# The drop exists so a bind-mounted instances directory ends up
# owned by the invoking user. A pod bind-mounts nothing here, so
# there is nothing for it to protect.
set +e
stdbuf -oL -eL /usr/local/bin/run_ardupilot_airsim.sh "${INSTANCE_NUM}" 2>&1
rc=$?
echo "[autopilot] ArduPilot SITL exited ${rc}"
exit "$rc"
