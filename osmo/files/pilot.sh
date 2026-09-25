#!/bin/bash
set -o pipefail
source /opt/ros/humble/setup.bash
source /ws/install/setup.bash
DS_IP=$(getent hosts "$DS_HOST" | awk '{print $1; exit}')
[ -n "$DS_IP" ] || { echo "cannot resolve $DS_HOST" >&2; exit 42; }
export ROS_DISCOVERY_SERVER="${DS_IP}:11811"

# The pilot needs MAVROS connected to the flight controller, which is
# slower than the graph appearing: SITL boots, then MAVLink handshakes.
python3 - <<'GATE' || exit 42
import os, sys, time
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from mavros_msgs.msg import State
from geometry_msgs.msg import PoseStamped
rclpy.init()
node = Node("tevv_pilot_gate")
seen = {"connected": False, "z": []}
def on_state(msg):
    seen["connected"] = bool(msg.connected)
def on_pose(msg):
    seen["z"].append((time.time(), msg.pose.position.z))
    seen["z"] = [s for s in seen["z"] if s[0] > time.time() - 6.0]
ns = os.environ["VEHICLE"]
node.create_subscription(State, f"/{ns}/mavros/state", on_state, 10)
node.create_subscription(PoseStamped, f"/{ns}/mavros/local_position/pose",
                         on_pose, qos_profile_sensor_data)
said = False
last_note = time.time()
deadline = time.time() + 600
while time.time() < deadline:
    rclpy.spin_once(node, timeout_sec=0.5)
    if not seen["connected"]:
        continue
    if not said:
        print("MAVROS connected to the flight controller"); said = True
        connected_at = time.time()
    # PX4 arms only once its EKF height is settled. Run 26 read the
    # local height as -3.36 m on the ground, spent 12 s in "height
    # estimate not stable", and then refused every arm command for
    # three minutes; run 24 read 0.04 m and armed at once. So: six
    # seconds of samples, all within a metre of zero, spanning less
    # than 15 cm.
    zs = [z for _, z in seen["z"]]
    span = (max(zs) - min(zs)) if zs else None
    # Stillness only: run 28 sat at a rock-steady 37.70 m, the
    # EKF's local origin being wherever PX4 put it, and a bound on
    # the value itself held the pilot for the full two minutes.
    if len(zs) >= 20 and seen["z"][0][0] < time.time() - 5.0 and span < 0.5:
        print("EKF height settled at %.2f m (span %.2f m over %.0fs)"
              % (zs[-1], span, time.time() - seen["z"][0][0]))
        sys.exit(0)
    if time.time() - last_note > 10.0:
        last_note = time.time()
        print("waiting for the EKF height to settle: %d samples, z=%s span=%s"
              % (len(zs), ("%.2f" % zs[-1]) if zs else "none",
                 ("%.2f" % span) if span is not None else "none"))
    if said and time.time() - connected_at > 120.0:
        print("EKF height did not settle in 120s (z=%s span=%s); flying anyway, "
              "the pilot retries a refused arming"
              % (("%.2f" % zs[-1]) if zs else "none", ("%.2f" % span) if span is not None else "none"))
        sys.exit(0)
if not seen["connected"]:
    print("READINESS TIMEOUT: MAVROS never reported connected", file=sys.stderr)
else:
    print("READINESS TIMEOUT: EKF height never settled (last %s)" % (seen["z"][-1:] or "none"), file=sys.stderr)
sys.exit(1)
GATE

# A declared route -- the CampaignSpec's `mission.trajectory`, compiled
# to base64 JSON exactly as the platform's runner hands it to this
# same script -- flies itself and lands where it took off. Without
# one, the corridor mission: climb, traverse, land where it ends up.
fly() {
  if [ -n "$ROUTE_B64" ]; then
    python3 /tmp/fly_mission_mavros.py \
      --vehicle "$VEHICLE" \
      --autopilot "$AUTOPILOT" \
      --trajectory-b64 "$ROUTE_B64"
  else
    python3 /tmp/fly_mission_mavros.py \
      --vehicle "$VEHICLE" \
      --autopilot "$AUTOPILOT" \
      --distance "$FLY_DISTANCE_M" \
      --altitude "$FLY_ALTITUDE_M" \
      --speed "$FLY_SPEED_MS"
  fi
}
fly 2>&1 | tee /tmp/fly.log; rc=${PIPESTATUS[0]}
# A refused arming is the one failure that is about the autopilot's
# moment, not the mission: give it one more go after a pause.
if [ "$rc" -ne 0 ] && grep -q "arming refused" /tmp/fly.log; then
  echo "[pilot] arming refused; retrying once in 20s"; sleep 20
  fly 2>&1 | tee /tmp/fly.log; rc=${PIPESTATUS[0]}
fi
# Tell the recorder the flight is over, however it ended. Under
# compose the runner stops the bag when this script exits; here the
# recorder is its own task and has to be told. The estimator this
# stack ships holds well in flight and runs away once the vehicle is
# parked (2 m/s^2, no ZUPT after the first frame), so a bag that
# keeps rolling after touchdown scores the parking, not the flight.
python3 /tmp/announce_done.py "$rc"
exit $rc
