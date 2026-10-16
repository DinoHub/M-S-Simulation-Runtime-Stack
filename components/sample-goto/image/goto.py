#!/usr/bin/env python3
"""sample-goto: the smallest planner TEVV can judge. It flies straight at its
goal with a velocity command (geometry_msgs/Twist on /cmd_vel, world ENU) and
says it is done (std_msgs/Bool on /goto/done) once there. No obstacle
avoidance: a worked example of onboarding, not a planner to rely on."""
import math

import rclpy
from geometry_msgs.msg import PoseStamped, Twist
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import Bool

MAX_SPEED = 1.5   # m/s
GAIN = 0.8        # 1/s
ARRIVED_M = 0.5


class Goto(Node):
    def __init__(self):
        super().__init__("sample_goto")
        self.odom = None
        self.goal = None
        latched = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                             durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.create_subscription(Odometry, "/odom", lambda m: setattr(self, "odom", m), 10)
        self.create_subscription(PoseStamped, "/goal_pose", self.on_goal, latched)
        self.cmd = self.create_publisher(Twist, "/cmd_vel", 10)
        self.done = self.create_publisher(Bool, "/goto/done", 10)
        self.create_timer(0.05, self.step)

    def on_goal(self, msg):
        self.goal = msg
        self.get_logger().info(f"goal ({msg.pose.position.x:.1f}, {msg.pose.position.y:.1f}, "
                               f"{msg.pose.position.z:.1f})")

    def step(self):
        if self.odom is None or self.goal is None:
            return
        p, g = self.odom.pose.pose.position, self.goal.pose.position
        d = (g.x - p.x, g.y - p.y, g.z - p.z)
        dist = math.sqrt(sum(v * v for v in d))
        speed = min(MAX_SPEED, GAIN * dist)
        out = Twist()
        if dist > 1e-3:
            out.linear.x, out.linear.y, out.linear.z = (v / dist * speed for v in d)
        self.cmd.publish(out)
        self.done.publish(Bool(data=dist < ARRIVED_M))


def main():
    rclpy.init()
    rclpy.spin(Goto())


if __name__ == "__main__":
    main()
