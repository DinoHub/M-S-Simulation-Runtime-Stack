"""Record the run's evidence into a rosbag2.

Not `ros2 bag record`, for the same reason the lead does not use
`ros2 topic hz`: under a discovery server a plain CLIENT learns only
about endpoints it matches, and `ros2 bag record` resolves each
topic's type from the graph before subscribing. It therefore creates
the bag, subscribes to nothing, and exits cleanly with
`message_count: 0` -- a green task and an empty bag, which is the
worst possible failure because every downstream stage looks broken
instead.

Naming the types here removes the lookup. The subscriptions then
match through the discovery server exactly as the estimator's do.

Exits 42 if the estimator never publishes (the platform was not
ready) and 1 if the bag ends up empty anyway (something is wrong
that a longer wait will not fix).
"""
import os
import pathlib
import sys
import time

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, QoSDurabilityPolicy, qos_profile_sensor_data
from rclpy.serialization import serialize_message
import json
import rosbag2_py

from sensor_msgs.msg import CameraInfo
from nav_msgs.msg import Odometry
from std_msgs.msg import Int32
from rosgraph_msgs.msg import Clock
from tf2_msgs.msg import TFMessage

LATCHED = QoSProfile(depth=1, durability=QoSDurabilityPolicy.TRANSIENT_LOCAL)

# topic, python type, type string for the bag, QoS
DONE_GRACE_SEC = 4.0
TOPICS = [
    ("/ov_msckf/odomimu", Odometry, "nav_msgs/msg/Odometry", 10),
    ("/ground_truth/odom", Odometry, "nav_msgs/msg/Odometry", 10),
    ("/clock", Clock, "rosgraph_msgs/msg/Clock", qos_profile_sensor_data),
    ("/tf", TFMessage, "tf2_msgs/msg/TFMessage", 10),
    ("/tf_static", TFMessage, "tf2_msgs/msg/TFMessage", LATCHED),
    # One per frame, stamped like the frame, a few hundred bytes: the
    # camera rate the estimator was actually fed, without recording
    # the images. On XFS the estimator held for twenty seconds and
    # then ran away under OSMO but not under compose, and nothing in
    # the bag could say whether the frames had thinned out.
    ("/camera/front/camera_info", CameraInfo, "sensor_msgs/msg/CameraInfo", qos_profile_sensor_data),
    ("/camera/front_right/camera_info", CameraInfo, "sensor_msgs/msg/CameraInfo", qos_profile_sensor_data),
]

def main():
    run_dir = pathlib.Path(os.environ["OUT_DIR"])
    bag_dir = run_dir / "bag"
    run_dir.mkdir(parents=True, exist_ok=True)
    record_sec = float(os.environ.get("RECORD_SEC", 60))
    ready_wait = float(os.environ.get("READY_WAIT_SEC", 900))

    rclpy.init()
    node = Node("tevv_recorder")

    writer = rosbag2_py.SequentialWriter()
    writer.open(
        rosbag2_py.StorageOptions(uri=str(bag_dir), storage_id="mcap"),
        rosbag2_py.ConverterOptions("cdr", "cdr"))

    counts = {topic: 0 for topic, _, _, _ in TOPICS}

    def make_writer(topic):
        def callback(msg):
            writer.write(topic, serialize_message(msg),
                         node.get_clock().now().nanoseconds)
            counts[topic] += 1
        return callback

    for topic, msg_type, type_str, qos in TOPICS:
        writer.create_topic(rosbag2_py.TopicMetadata(
            name=topic, type=type_str, serialization_format="cdr"))
        node.create_subscription(msg_type, topic, make_writer(topic), qos)

    # The pilot announces the end of the flight; the bag closes a
    # few seconds later. RECORD_SEC is the cap for a pilot that
    # never gets there, and the whole window when nothing flies.
    done = {"at": None, "rc": None}
    def on_done(msg):
        if done["at"] is None:
            done["at"] = time.time(); done["rc"] = msg.data
            print("mission done (pilot exit %d); closing the bag in %.0fs"
                  % (msg.data, DONE_GRACE_SEC))
    node.create_subscription(Int32, "/mission/done", on_done, LATCHED)

    # Readiness is the bridge publishing ground truth: the simulator
    # has booted and the bridge has connected. The estimator is not
    # part of readiness -- OpenVINS holds the filter under ZUPT and
    # publishes nothing until the vehicle moves, so gating on its
    # odometry would start the bag at takeoff and lose the first
    # second of the flight. An estimator that never publishes is a
    # verdict for the evaluators, not a reason to reschedule.
    deadline = time.time() + ready_wait
    while counts["/ground_truth/odom"] == 0:
        if time.time() >= deadline:
            print("READINESS TIMEOUT: nothing on /ground_truth/odom "
                  "after %.0fs" % ready_wait, file=sys.stderr)
            return 42
        rclpy.spin_once(node, timeout_sec=0.5)
    print("bridge publishing; recording %.0fs into %s"
          % (record_sec, bag_dir))

    start = time.time()
    while time.time() - start < record_sec:
        rclpy.spin_once(node, timeout_sec=0.2)
        if done["at"] is not None and time.time() - done["at"] > DONE_GRACE_SEC:
            break
    print("recorded %.0fs" % (time.time() - start))
    (run_dir / "mission.json").write_text(json.dumps({
        "announced": done["at"] is not None,
        "pilot_exit": done["rc"],
        "recorded_sec": round(time.time() - start, 1),
        "messages": dict(counts)}, indent=2))

    del writer          # flush and close the bag
    total = sum(counts.values())
    for topic, _, _, _ in TOPICS:
        print("  %-22s %d msgs" % (topic, counts[topic]))
    if total == 0:
        print("FAIL: recorded nothing", file=sys.stderr)
        return 1
    if counts["/ov_msckf/odomimu"] == 0:
        print("NOTE: the estimator never published; the evaluators "
              "will say so", file=sys.stderr)
    print("evidence complete: %s (%d messages)" % (run_dir, total))
    # With a viewer attached, keep the gang up after the bag has
    # closed: the recorder is the lead, and the moment it exits the
    # simulator goes with it -- a couple of minutes after touchdown,
    # too short to look at anything. The evidence is already final.
    hold = float(os.environ.get("VIZ_HOLD_SEC") or 0)
    if hold > 0:
        # The recording node goes first: its writer is closed, and a callback
        # delivered now would write into it and kill the lead.
        node.destroy_node()
        hold_for_viewers(hold, float(os.environ.get("VIZ_IDLE_SEC") or 60))
    return 0


def hold_for_viewers(max_sec, idle_sec):
    """Keep the run up while someone is watching it, and not otherwise.

    The foxglove task publishes /viz/viewers (connections on its port). The
    run is released once nobody has been connected for idle_sec -- counted from
    now, so a viewer has that long to arrive -- and never later than max_sec.
    No counter at all (the foxglove task died) reads as nobody watching.
    """
    from std_msgs.msg import Int32
    watcher = Node("tevv_viz_hold")
    seen = {"n": 0}
    watcher.create_subscription(Int32, "/viz/viewers", lambda m: seen.update(n=m.data), LATCHED)
    start = last_viewer = time.time()
    print("holding the run for a viewer: released after %.0fs with nobody connected, "
          "%.0fs at most" % (idle_sec, max_sec), flush=True)
    shown = None
    while True:
        rclpy.spin_once(watcher, timeout_sec=0.5)
        now = time.time()
        if seen["n"] > 0:
            last_viewer = now
        if seen["n"] != shown:
            print("viewers connected: %d" % seen["n"], flush=True)
            shown = seen["n"]
        if now - last_viewer > idle_sec:
            print("nobody watching for %.0fs; releasing the run after %.0fs of hold"
                  % (idle_sec, now - start), flush=True)
            break
        if now - start > max_sec:
            print("hold cap of %.0fs reached; releasing the run" % max_sec, flush=True)
            break
    watcher.destroy_node()

sys.exit(main())
