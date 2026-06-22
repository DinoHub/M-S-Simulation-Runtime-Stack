#!/usr/bin/env python3
"""run_state_bridge — drive metrics /run_state from simple_exploration status.

Bridges the autonomy_stack exploration planner to the metrics-collector's
planner-agnostic trigger so exploration benchmark runs are hands-off:

    GetExplorationStatus (poll)  ->  /run_state (RunState)

State machine (per run):
    WAITING_START --[any agent TRANSIT/BUSY]-->  publish RUNNING   --> RUNNING
    RUNNING       --[all agents IDLE]--------->  publish COMPLETED --> DONE
    RUNNING       --[any agent CANCELLED]----->  publish ABORTED   --> DONE
    RUNNING       --[any TARGET_DETECTED]*----->  publish COMPLETED --> DONE
        * only if target_detected_completes (search missions)

Requires both airsim_interfaces (RunState) and exploration_interfaces
(ExplorationStatus / GetExplorationStatus) on the ROS path — source the
metrics-collector overlay and the autonomy_stack install, e.g.:

    source /opt/ros/airsim/setup.bash
    source /home/mnsuser/integration/autonomy_stack/install/setup.bash
    ros2 run ... / python3 tools/run_state_bridge.py --ros-args \
        -p run_id:=expl-001 -p scenario_id:=airsim-condo

Same ROS_DOMAIN_ID as the collector. Exits after the terminal publish unless
-p oneshot:=false (then it resets and waits for the next exploration to start).
"""

import sys

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy, HistoryPolicy

from airsim_interfaces.msg import RunState
from exploration_interfaces.msg import ExplorationStatus
from exploration_interfaces.srv import GetExplorationStatus

# Local phase enum (not the wire enums)
WAITING_START = 0
RUNNING = 1
DONE = 2

ACTIVE = {ExplorationStatus.STATUS_TRANSIT, ExplorationStatus.STATUS_BUSY}


class RunStateBridge(Node):
    def __init__(self):
        super().__init__("run_state_bridge")

        self.declare_parameter("status_service", "/exploration/get_exploration_status")
        self.declare_parameter("run_state_topic", "/run_state")
        self.declare_parameter("poll_period_sec", 1.0)
        self.declare_parameter("run_id", "expl-run")
        self.declare_parameter("scenario_id", "airsim-condo")
        # TARGET_DETECTED ends the run as COMPLETED (search-and-find missions).
        self.declare_parameter("target_detected_completes", True)
        # Republish the transition message N times so a late/lossy subscriber
        # still catches it (also latched via transient_local below).
        self.declare_parameter("publish_repeat", 3)
        # After the terminal publish: exit (true) or reset and wait again (false).
        self.declare_parameter("oneshot", True)

        g = self.get_parameter
        self.status_service = g("status_service").value
        self.run_id = g("run_id").value
        self.scenario_id = g("scenario_id").value
        self.target_completes = g("target_detected_completes").value
        self.publish_repeat = int(g("publish_repeat").value)
        self.oneshot = g("oneshot").value
        period = float(g("poll_period_sec").value)

        # Latched + reliable so the collector reliably gets each transition even
        # if it (re)subscribes between polls.
        qos = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
        )
        self.pub = self.create_publisher(RunState, g("run_state_topic").value, qos)
        self.cli = self.create_client(GetExplorationStatus, self.status_service)

        self.phase = WAITING_START
        self._inflight = False  # guard: one outstanding service call at a time
        self.create_timer(period, self._tick)
        self.get_logger().info(
            f"run_state_bridge up. polling {self.status_service} every {period}s "
            f"(run_id={self.run_id}, scenario_id={self.scenario_id})"
        )

    # --- helpers -------------------------------------------------------------
    def _publish(self, state, label):
        msg = RunState()
        msg.state = state
        msg.state_change_time = self.get_clock().now().to_msg()
        msg.run_id = self.run_id
        msg.scenario_id = self.scenario_id
        for _ in range(max(1, self.publish_repeat)):
            self.pub.publish(msg)
        self.get_logger().info(f"/run_state -> {label} (state={state}, run_id={self.run_id})")

    # --- main loop -----------------------------------------------------------
    def _tick(self):
        # Async, non-blocking: never nest-spin inside the executor. One call at a
        # time; the done-callback runs the state machine.
        if self.phase == DONE or self._inflight:
            return
        if not self.cli.service_is_ready():
            self.get_logger().warn(f"waiting for {self.status_service} ...", throttle_duration_sec=10.0)
            return
        req = GetExplorationStatus.Request()
        req.agent_ids = []  # empty = all agents
        self._inflight = True
        self.cli.call_async(req).add_done_callback(self._on_status)

    def _on_status(self, future):
        self._inflight = False
        if self.phase == DONE:
            return
        result = future.result() if future.done() else None
        if result is None:
            self.get_logger().warn("status query failed", throttle_duration_sec=10.0)
            return
        statuses = list(result.statuses)
        if not statuses:
            return  # no agents reporting yet

        codes = [s.status for s in statuses]

        if self.phase == WAITING_START:
            if any(c in ACTIVE for c in codes):
                self._publish(RunState.RUNNING, "RUNNING")
                self.phase = RUNNING
            return

        # phase == RUNNING
        if any(c == ExplorationStatus.STATUS_CANCELLED for c in codes):
            self._terminal(RunState.ABORTED, "ABORTED")
        elif self.target_completes and any(c == ExplorationStatus.STATUS_TARGET_DETECTED for c in codes):
            self._terminal(RunState.COMPLETED, "COMPLETED (target detected)")
        elif all(c == ExplorationStatus.STATUS_IDLE for c in codes):
            self._terminal(RunState.COMPLETED, "COMPLETED (all idle — area covered)")

    def _terminal(self, state, label):
        self._publish(state, label)
        if self.oneshot:
            self.phase = DONE
            self.get_logger().info("terminal state reached; shutting down (oneshot).")
            rclpy.shutdown()  # spin() returns; clean exit
            return
        # reset for the next exploration
        self.phase = WAITING_START
        self.get_logger().info("terminal state reached; reset, waiting for next run.")


def main():
    rclpy.init()
    node = RunStateBridge()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
