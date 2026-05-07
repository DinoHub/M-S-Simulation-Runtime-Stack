#!/usr/bin/env python3
"""Generate docker-compose.multi-agent.yml for an arbitrary number of drones.

The multi-agent compose has three near-identical service blocks per drone
(airsim_bridge_dN, zenoh_bridge_dN, autonomy_stack_N) plus a per-drone
network. Hand-editing scales poorly past 3 drones — one typo in a
ROS_DOMAIN_ID or network attachment silently breaks the per-drone
isolation guarantee. This script is the canonical way to (re)build the
file.

Usage:
    python generate_compose.py --drones 3                # writes sibling docker-compose.multi-agent.yml
    python generate_compose.py --drones 8 --stdout       # print to stdout
    python generate_compose.py --drones 5 --output X.yml # custom output path

Idempotency: same --drones produces byte-identical output across runs.
"""

# -----------------------------------------------------------------------------
# VENDORED COPY — DO NOT EDIT INDEPENDENTLY.
# Upstream:    Cosys-AirSim @ feat/multi-agent-ros2-zenoh-architecture
#              integrations/ros2/docker/Linux/multi-agent/generate_compose.py
# Vendored at: e98bb4a15f5133fa06743cfdfeded85efb2dfa13
# Refresh:     git -C <cosys-airsim> show feat/multi-agent-ros2-zenoh-architecture:integrations/ros2/docker/Linux/multi-agent/generate_compose.py > tools/generate_compose.py
# -----------------------------------------------------------------------------

import argparse
import sys
from pathlib import Path

MIN_DRONES = 1
MAX_DRONES = 16  # bumpable; see README "Scaling beyond 3 drones"
SUBNET_BASE = 40  # 172.40.0.0/24 = sim_net; see plan / README


def header() -> str:
    return f"""\
# =============================================================================
# Multi-agent ROS 2 + Zenoh stack — GENERATED FILE
#
#   DO NOT EDIT BY HAND.
#   Regenerate with:  python generate_compose.py --drones N
#
# Architecture: docs/integrations/ros2-docs/architecture/multi-agent-zenoh.md
#
# Two pipes — never crossed:
#
#   1. AirSim -> ROS 2 bridges -> agent_internal-N
#      (per-drone sensors / odom / TF / clock / control)
#
#   2. ros2-x11-node -> sim-router -> agent_external Zenoh mesh
#      (only /shared/sim/**)
#
#   3. Per-drone autonomy_stack <-> zenoh_bridge_dN <-> agent_external
#      (only /shared/**)
#
# Bring up with:
#   cp .env.example .env
#   docker compose -f docker-compose.multi-agent.yml up
#
# Smaller swarms — pass an explicit service list:
#   docker compose -f docker-compose.multi-agent.yml up \\
#     ros2-x11-node sim-router sim-router-egress \\
#     airsim_bridge_d1 zenoh_bridge_d1 autonomy_stack_1
# =============================================================================
"""


def anchors() -> str:
    return """\

x-zenoh-bridge: &zenoh-bridge-defaults
  image: eclipse/zenoh-bridge-ros2dds:latest
  init: true
  restart: unless-stopped

x-airsim-bridge: &airsim-bridge-defaults
  # Reuses the existing X11-bridge image (workspace already built inside).
  # X11 isn't needed for these instances — they just run the launch headless.
  image: dhdevspace/auto_mns:tevv-airstack-ros2-x11-node-development
  init: true
  restart: unless-stopped
  ipc: host                           # iceoryx2 zero-copy transport
  volumes:
    - /dev/shm:/dev/shm:rw
    - /tmp/iceoryx2:/tmp/iceoryx2:rw

x-autonomy-stub: &autonomy-stub-defaults
  # Placeholder autonomy stack — drop in your planner / VIO / mission node here.
  # Today: bare ROS 2 image with `sleep infinity` so you can `docker exec` in
  # and run `ros2 topic list` to verify isolation.
  image: ros:humble-ros-base
  init: true
  command: ["sleep", "infinity"]
  stdin_open: true
  tty: true
"""


def sim_side_services() -> str:
    return """\

services:
  # =========================================================================
  # SIM SIDE — ros2-x11-node + sim-router share a netns (loopback DDS).
  # ros2-x11-node hosts scenario / UI / event-marker nodes.
  # sim-router is the only sim-side container with an agent_external link
  # (via the sim-router-egress sidecar — see README).
  # =========================================================================
  ros2-x11-node:
    image: dhdevspace/auto_mns:tevv-airstack-ros2-x11-node-development
    container_name: multi_agent_ros2_x11_node
    hostname: ros2-x11-node
    init: true
    restart: unless-stopped
    ipc: host

    environment:
      - DISPLAY=${DISPLAY:-:0}
      - XAUTHORITY=/tmp/.Xauthority
      - QT_X11_NO_MITSHM=1
      - ROS_DOMAIN_ID=${SIM_ROS_DOMAIN_ID:-0}
      - ROS_LOCALHOST_ONLY=1                  # Trapped on loopback w/ sim-router
      - AIRSIM_HOST_IP=${AIRSIM_HOST_IP:-host.docker.internal}
      - AIRSIM_HOST_PORT=${AIRSIM_HOST_PORT:-41451}
      - LAUNCH_MODE=${SIM_LAUNCH_MODE:-scenario_only}
      - LAUNCH_RVIZ=${LAUNCH_RVIZ:-false}

    networks:
      - sim_net                                # Reach AirSim on host

    volumes:
      - /tmp/.X11-unix:/tmp/.X11-unix:rw
      - ${XAUTHORITY:-$HOME/.Xauthority}:/tmp/.Xauthority:ro
      - /dev/shm:/dev/shm:rw

    extra_hosts:
      - "host.docker.internal:host-gateway"

  sim-router:
    <<: *zenoh-bridge-defaults
    container_name: multi_agent_sim_router
    # Share netns with ros2-x11-node so DDS lives on a private loopback.
    network_mode: "service:ros2-x11-node"
    environment:
      - RUST_LOG=zenoh=info,zenoh_plugin_ros2dds=info
      - ROS_DOMAIN_ID=${SIM_ROS_DOMAIN_ID:-0}
      - ROS_LOCALHOST_ONLY=1
    volumes:
      - ./sim-router.json5:/etc/zenoh-bridge-ros2dds.json5:ro
    command:
      - "-c"
      - "/etc/zenoh-bridge-ros2dds.json5"
    depends_on:
      - ros2-x11-node

  sim-router-egress:
    <<: *zenoh-bridge-defaults
    container_name: multi_agent_sim_router_egress
    networks:
      - agent_external
    environment:
      - RUST_LOG=zenoh=info
    command:
      - "-l"
      - "tcp/0.0.0.0:7447"
      - "--no-multicast-scouting"   # explicit endpoint config only
    # Acts as a Zenoh peer on agent_external; sim-router (inside ros2-x11-node
    # netns) connects to this peer via host gateway. See README for details.
"""


def drone_block(n: int, vehicle_prefix: str = "Drone") -> str:
    """Render the airsim_bridge / zenoh_bridge / autonomy_stack trio for drone N.

    The first drone (n == 1) is the "clock master": its bridge sets
    enable_coordination:=true so coordination_node spawns there and publishes
    the singleton topics (/clock @ 50Hz, world_ned static TF, /origin_geo_point,
    fleet services). Other drones must keep enable_coordination:=false to
    avoid /clock and TF root collisions even with per-drone DDS domains.
    """
    enable_coordination = "true" if n == 1 else "false"
    if n == 1:
        coord_role_comment = (
            "    # CLOCK MASTER: this bridge owns /clock, world_ned static TF,\n"
            "    # /origin_geo_point, and fleet services. Exactly one bridge in\n"
            "    # the swarm enables coordination (see launch arg description).\n"
        )
    else:
        coord_role_comment = (
            "    # Non-clock-master: relies on drone-1 for /clock + TF root.\n"
            "    # Per-drone DDS domain isolation is the secondary belt against crosstalk.\n"
        )
    return f"""\

  # =========================================================================
  # DRONE {n}
  # =========================================================================
  airsim_bridge_d{n}:
    <<: *airsim-bridge-defaults
    container_name: multi_agent_airsim_bridge_d{n}
{coord_role_comment}    environment:
      - ROS_DOMAIN_ID=${{DRONE_{n}_DOMAIN_ID:-{n}}}
      - ROS_LOCALHOST_ONLY=0
      - AIRSIM_HOST_IP=${{AIRSIM_HOST_IP:-host.docker.internal}}
      - AIRSIM_HOST_PORT=${{AIRSIM_HOST_PORT:-41451}}
      - VEHICLE_NAME=${{VEHICLE_{n}_NAME:-{vehicle_prefix}{n}}}
    networks:
      - sim_net
      - agent_internal-{n}
    extra_hosts:
      - "host.docker.internal:host-gateway"
    command:
      - "bash"
      - "-lc"
      - >
        source /airsim_ros2_ws/install/setup.bash &&
        ros2 launch airsim_ros_pkgs airsim_bringup.launch.py
        host_ip:=${{AIRSIM_HOST_IP:-host.docker.internal}}
        host_port:=${{AIRSIM_HOST_PORT:-41451}}
        vehicles:="['${{VEHICLE_{n}_NAME:-{vehicle_prefix}{n}}}']"
        enable_localization:=true
        enable_coordination:={enable_coordination}
        local_obs_target_frame:=${{LOCAL_OBS_TARGET_FRAME:-map}}

  zenoh_bridge_d{n}:
    <<: *zenoh-bridge-defaults
    container_name: multi_agent_zenoh_bridge_d{n}
    environment:
      - RUST_LOG=zenoh=info,zenoh_plugin_ros2dds=info
      - ROS_DOMAIN_ID=${{DRONE_{n}_DOMAIN_ID:-{n}}}
    networks:
      - agent_internal-{n}
      - agent_external
    volumes:
      - ./zenoh-bridge-drone.json5:/etc/zenoh-bridge-ros2dds.json5:ro
    command:
      - "-c"
      - "/etc/zenoh-bridge-ros2dds.json5"
      - "-d"
      - "${{DRONE_{n}_DOMAIN_ID:-{n}}}"
    depends_on:
      - airsim_bridge_d{n}

  autonomy_stack_{n}:
    <<: *autonomy-stub-defaults
    container_name: multi_agent_autonomy_stack_{n}
    environment:
      - ROS_DOMAIN_ID=${{DRONE_{n}_DOMAIN_ID:-{n}}}
      - ROS_LOCALHOST_ONLY=0
    networks:
      - agent_internal-{n}

  # ----------------------------------------------------------------------
  # mavros_d{n}: TEST FIXTURE for the MAVROS↔ArduPilot-SITL path.
  # ----------------------------------------------------------------------
  # NOT a production service. In production the runtime-side autonomy_stack
  # container owns MAVROS — this stub exists only so sim-side maintainers
  # can verify, without the runtime stack present, that:
  #   1. agent_internal-{n} can reach ArduPilot SITL on the host, and
  #   2. mavros_node successfully completes its MAVLink handshake.
  #
  # Opt-in: `docker compose --profile test up`. Reaches ArduPilot SITL on
  # the host via host-gateway. SITL itself is NOT in this compose — start
  # it from docker/ardupilot_airsim_docker/docker-compose-ardupilot.yml.
  mavros_d{n}:
    image: dhdevspace/auto_mns:tevv-airstack-ros2-x11-node-development
    container_name: multi_agent_mavros_d{n}
    init: true
    restart: unless-stopped
    profiles: ["test"]
    environment:
      - ROS_DOMAIN_ID=${{DRONE_{n}_DOMAIN_ID:-{n}}}
      - ROS_LOCALHOST_ONLY=0
    networks:
      - agent_internal-{n}
    extra_hosts:
      - "host.docker.internal:host-gateway"
    command:
      - "bash"
      - "-lc"
      - >
        source /airsim_ros2_ws/install/setup.bash &&
        ros2 launch airsim_ros_pkgs mavros_bringup.launch.py
        vehicle:='{vehicle_prefix}{n}'
        fcu_url:='tcp://host.docker.internal:{5760 + 10 * (n - 1)}'
        target_system_id:={n}
    depends_on:
      airsim_bridge_d{n}:
        condition: service_started
"""


def networks_block(num_drones: int) -> str:
    """Render the networks: section. agent_external sits past the drone range."""
    sim_net_subnet = f"172.{SUBNET_BASE}.0.0/24"
    external_subnet = f"172.{SUBNET_BASE + num_drones + 1}.0.0/24"

    out = f"""\

# =============================================================================
# NETWORKS
#
# The whole boundary contract is enforced by which container attaches to which
# network. Don't add agent_external attachments to anything except zenoh
# bridges.
# =============================================================================
networks:
  # AirSim RPC traffic. Reachable by all bridge containers + sim-router so they
  # can hit AirSim on the host. Not reachable from autonomy stacks.
  sim_net:
    name: multi_agent_sim_net
    driver: bridge
    ipam:
      config:
        - subnet: {sim_net_subnet}

"""

    out += "  # Per-drone DDS islands. Each one carries one drone's full ROS 2 graph.\n"
    out += "  # Members: airsim_bridge_dN, zenoh_bridge_dN, autonomy_stack_N.\n"
    for n in range(1, num_drones + 1):
        subnet = f"172.{SUBNET_BASE + n}.0.0/24"
        out += f"""\
  agent_internal-{n}:
    name: multi_agent_internal_{n}
    driver: bridge
    ipam:
      config:
        - subnet: {subnet}

"""

    out += f"""\
  # Zenoh swarm mesh. Members: zenoh_bridge_d1..N, sim-router-egress.
  # ONLY /shared/** traffic flows here.
  agent_external:
    name: multi_agent_external
    driver: bridge
    ipam:
      config:
        - subnet: {external_subnet}
"""

    return out


def _render_standalone(num_drones: int, vehicle_prefix: str = "Drone") -> str:
    parts = [
        header(),
        anchors(),
        sim_side_services(),
    ]
    for n in range(1, num_drones + 1):
        parts.append(drone_block(n, vehicle_prefix))
    parts.append(networks_block(num_drones))
    return "".join(parts)


def _integration_header() -> str:
    return """\
# =============================================================================
# AirSim ROS 2 bridges — INTEGRATION MODE — GENERATED FILE
#
#   DO NOT EDIT BY HAND.
#   Regenerate with:  python generate_compose.py --drones N --mode integration
#
# Bridges-only compose. Pairs with an EXTERNAL autonomy-stack compose that
# already creates the per-drone agent_internal-N networks. Bring-up order:
#
#   1. Start the autonomy-stack first (creates agent_internal-1..N).
#   2. docker compose -f docker-compose.bridges.yml up
#
# Each airsim_bridge_dN attaches to its drone's agent_internal-N network and
# reaches AirSim RPC on the host via host-gateway. The autonomy-stack's
# autonomy node (or MAVROS, planner, etc.) lives on the same agent_internal-N
# and consumes /DroneN/* topics over DDS.
#
# See ../README.md for verification + the optional --profile test fixture.
# =============================================================================
"""


def _integration_anchors() -> str:
    return """\

x-airsim-bridge: &airsim-bridge-defaults
  image: dhdevspace/auto_mns:tevv-airstack-ros2-x11-node-development
  init: true
  restart: unless-stopped
  ipc: host
  volumes:
    - /dev/shm:/dev/shm:rw
    - /tmp/iceoryx2:/tmp/iceoryx2:rw
  extra_hosts:
    - "host.docker.internal:host-gateway"
"""


def _integration_drone_block(n: int, vehicle_prefix: str = "Drone") -> str:
    enable_coordination = "true" if n == 1 else "false"
    if n == 1:
        coord_role = (
            "    # CLOCK MASTER: this bridge owns /clock, world_ned static TF,\n"
            "    # /origin_geo_point, and fleet services. Exactly one bridge in\n"
            "    # the swarm enables coordination (see launch arg description).\n"
        )
    else:
        coord_role = (
            "    # Non-clock-master: relies on drone-1 for /clock + TF root.\n"
            "    # Per-drone DDS domain isolation is the secondary belt against crosstalk.\n"
        )
    mavlink_port = 5760 + 10 * (n - 1)
    return f"""\

  # =========================================================================
  # DRONE {n}
  # =========================================================================
  airsim_bridge_d{n}:
    <<: *airsim-bridge-defaults
    container_name: airsim_bridge_d{n}
{coord_role}    environment:
      - ROS_DOMAIN_ID=${{DRONE_{n}_DOMAIN_ID:-{n}}}
      - ROS_LOCALHOST_ONLY=0
      - AIRSIM_HOST_IP=${{AIRSIM_HOST_IP:-host.docker.internal}}
      - AIRSIM_HOST_PORT=${{AIRSIM_HOST_PORT:-41451}}
      - VEHICLE_NAME=${{VEHICLE_{n}_NAME:-{vehicle_prefix}{n}}}
    networks:
      - agent_internal-{n}
    command:
      - "bash"
      - "-lc"
      - >
        source /airsim_ros2_ws/install/setup.bash &&
        ros2 launch airsim_ros_pkgs airsim_bringup.launch.py
        host_ip:=${{AIRSIM_HOST_IP:-host.docker.internal}}
        host_port:=${{AIRSIM_HOST_PORT:-41451}}
        vehicles:="['${{VEHICLE_{n}_NAME:-{vehicle_prefix}{n}}}']"
        enable_localization:=true
        enable_coordination:={enable_coordination}
        local_obs_target_frame:=${{LOCAL_OBS_TARGET_FRAME:-map}}

  # ----------------------------------------------------------------------
  # mavros_d{n}: TEST FIXTURE (--profile test) for the MAVROS↔SITL path.
  # NOT a production service — autonomy_stack_{n} owns MAVROS in production.
  # DO NOT enable --profile test alongside the real autonomy stack: the two
  # MAVROSes will collide on /{vehicle_prefix}{n}/mavros/*.
  # ----------------------------------------------------------------------
  mavros_d{n}:
    image: dhdevspace/auto_mns:tevv-airstack-ros2-x11-node-development
    container_name: airsim_mavros_test_d{n}
    init: true
    restart: unless-stopped
    profiles: ["test"]
    environment:
      - ROS_DOMAIN_ID=${{DRONE_{n}_DOMAIN_ID:-{n}}}
      - ROS_LOCALHOST_ONLY=0
    networks:
      - agent_internal-{n}
    extra_hosts:
      - "host.docker.internal:host-gateway"
    command:
      - "bash"
      - "-lc"
      - >
        source /airsim_ros2_ws/install/setup.bash &&
        ros2 launch airsim_ros_pkgs mavros_bringup.launch.py
        vehicle:='{vehicle_prefix}{n}'
        fcu_url:='tcp://host.docker.internal:{mavlink_port}'
        target_system_id:={n}
    depends_on:
      airsim_bridge_d{n}:
        condition: service_started
"""


def _integration_networks_block(num_drones: int) -> str:
    out = """\

# =============================================================================
# NETWORKS — declared external because the autonomy-stack owns them.
#
# Bring up the autonomy-stack first; it creates these networks. This compose
# only attaches to them. If a network is missing, `docker compose up` errors
# out — that's the prereq check.
# =============================================================================
networks:
"""
    for n in range(1, num_drones + 1):
        out += f"""\
  agent_internal-{n}:
    external: true
"""
    return out


def _render_integration(num_drones: int, vehicle_prefix: str = "Drone") -> str:
    """Render a slim bridges-only compose that pairs with an external autonomy-stack.

    Networks are declared external: true with the runtime-spec names
    (agent_internal-N). The autonomy-stack compose must be up first so docker
    can find those networks.
    """
    parts = [_integration_header(), _integration_anchors(), "\nservices:\n"]
    for n in range(1, num_drones + 1):
        parts.append(_integration_drone_block(n, vehicle_prefix))
    parts.append(_integration_networks_block(num_drones))
    return "".join(parts)


def render(num_drones: int, mode: str = "standalone", vehicle_prefix: str = "Drone") -> str:
    if mode == "integration":
        return _render_integration(num_drones, vehicle_prefix)
    return _render_standalone(num_drones, vehicle_prefix)


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__.split("\n", 1)[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--drones",
        type=int,
        required=True,
        help=f"number of drones to generate ({MIN_DRONES}..{MAX_DRONES})",
    )
    p.add_argument(
        "--mode",
        choices=["standalone", "integration"],
        default="standalone",
        help=(
            "standalone (default): self-contained stack with sim-router, zenoh, "
            "and autonomy stubs on dedicated networks. integration: bridges-only "
            "compose to pair with an external autonomy-stack that already created "
            "the agent_internal-N networks."
        ),
    )
    p.add_argument(
        "--vehicle-prefix",
        default="Drone",
        help=(
            "prefix for default vehicle names (default 'Drone' -> Drone1,Drone2,...; "
            "use 'Copter' to pair with the runtime-side compose/ardupilot-xfs stack "
            "which uses Copter1..N naming). Only affects default values; env vars "
            "VEHICLE_{N}_NAME still override at run time."
        ),
    )
    p.add_argument(
        "--output",
        type=Path,
        default=None,
        help=(
            "output file path (default: docker-compose.multi-agent.yml for standalone, "
            "docker-compose.bridges.yml for integration, next to this script)"
        ),
    )
    p.add_argument(
        "--stdout",
        action="store_true",
        help="write to stdout instead of a file (overrides --output)",
    )
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    if args.drones < MIN_DRONES or args.drones > MAX_DRONES:
        print(
            f"error: --drones must be in [{MIN_DRONES}, {MAX_DRONES}]; got {args.drones}",
            file=sys.stderr,
        )
        return 2

    content = render(args.drones, mode=args.mode, vehicle_prefix=args.vehicle_prefix)

    if args.stdout:
        sys.stdout.write(content)
        return 0

    default_name = (
        "docker-compose.bridges.yml" if args.mode == "integration"
        else "docker-compose.multi-agent.yml"
    )
    out_path = args.output or (Path(__file__).resolve().parent / default_name)
    out_path.write_text(content, encoding="utf-8", newline="\n")
    print(
        f"wrote {out_path} ({args.drones} drone{'s' if args.drones != 1 else ''}, "
        f"mode={args.mode}, vehicle_prefix={args.vehicle_prefix})",
        file=sys.stderr,
    )
    return 0


def _self_test() -> None:
    """Smoke test the renderer. Run with `python -c 'import generate_compose; generate_compose._self_test()'`."""
    a = render(3)
    b = render(3)
    assert a == b, "render(3) is not idempotent"

    d1_block = a.split("# DRONE 1")[1].split("# DRONE 2")[0]
    d2_block = a.split("# DRONE 2")[1].split("# DRONE 3")[0]
    d3_block = a.split("# DRONE 3")[1]
    assert "enable_coordination:=true" in d1_block, "drone-1 must be the clock master"
    assert "enable_coordination:=false" in d2_block, "drone-2 must not be the clock master"
    assert "enable_coordination:=false" in d3_block, "drone-3 must not be the clock master"
    assert a.count("enable_coordination:=true") == 1, "exactly one clock master allowed"

    assert "mavros_d1:" in a and "mavros_d3:" in a, "MAVROS sidecars missing"
    assert 'profiles: ["test"]' in a, "MAVROS test sidecars must be opt-in via the 'test' profile"
    assert 'profiles: ["mavros"]' not in a, "old 'mavros' profile name should be gone — use 'test'"
    assert "tcp://host.docker.internal:5760" in a, "drone-1 MAVLink port should be 5760"
    assert "tcp://host.docker.internal:5770" in a, "drone-2 MAVLink port should be 5770"
    assert "tcp://host.docker.internal:5780" in a, "drone-3 MAVLink port should be 5780"
    assert "target_system_id:=1" in a and "target_system_id:=3" in a, "target_system_id must be 1-indexed per drone"

    # Integration mode
    out_int = render(3, mode="integration")
    out_int_b = render(3, mode="integration")
    assert out_int == out_int_b, "integration render(3) is not idempotent"
    assert out_int != render(3), "integration mode should differ from standalone"

    assert "external: true" in out_int, "integration mode must use external networks"
    assert "agent_internal-1:" in out_int and "agent_internal-3:" in out_int, \
        "integration mode must declare agent_internal-N networks"

    # Forbidden service definitions in integration mode (autonomy stack provides them).
    # Match the "<name>:" service header pattern under "services:" so the assertion
    # only trips on a real service block, not on a comment that mentions the name.
    for forbidden in ["sim-router:", "sim-router-egress:", "zenoh_bridge_d1:", "autonomy_stack_1:", "ros2-x11-node:"]:
        assert f"\n  {forbidden}" not in out_int, \
            f"integration mode must not include {forbidden} (autonomy stack owns it)"

    # Required services
    assert "airsim_bridge_d1:" in out_int and "airsim_bridge_d3:" in out_int, \
        "integration mode must include all N drone bridges"
    assert 'profiles: ["test"]' in out_int, "mavros test fixture must remain opt-in in integration mode"

    # Clock master invariant carried over to integration mode
    d1_int = out_int.split("# DRONE 1")[1].split("# DRONE 2")[0]
    assert "enable_coordination:=true" in d1_int, "drone-1 must be clock master in integration too"
    assert out_int.count("enable_coordination:=true") == 1, "exactly one clock master in integration mode"

    # --vehicle-prefix knob — default unchanged across both modes
    assert "VEHICLE_1_NAME:-Drone1" in a, "default prefix must remain 'Drone' in standalone"
    assert "VEHICLE_1_NAME:-Drone1" in out_int, "default prefix must remain 'Drone' in integration"

    # Copter prefix flips defaults in both modes
    copter_std = render(4, vehicle_prefix="Copter")
    assert "VEHICLE_1_NAME:-Copter1" in copter_std
    assert "VEHICLE_4_NAME:-Copter4" in copter_std
    assert "Drone1" not in copter_std.split("# DRONE 1")[1].split("# DRONE 2")[0], \
        "Copter-prefix render must not contain Drone-prefix defaults in drone-1 block"

    copter_int = render(4, mode="integration", vehicle_prefix="Copter")
    assert "VEHICLE_1_NAME:-Copter1" in copter_int
    assert "VEHICLE_4_NAME:-Copter4" in copter_int
    assert "vehicle:='Copter1'" in copter_int and "vehicle:='Copter4'" in copter_int, \
        "mavros test fixture must use the Copter prefix in integration mode"
    assert "agent_internal-4:" in copter_int, "integration mode must declare agent_internal-N for all 4 drones"

    # local_obs_target_frame env var is plumbed through bridge command in both modes.
    # Default value 'map' preserves existing behavior; users flip via .env to enable
    # per-drone base_link mode without editing the compose.
    assert "local_obs_target_frame:=${LOCAL_OBS_TARGET_FRAME:-map}" in a, \
        "standalone bridges must pass LOCAL_OBS_TARGET_FRAME env var to launch"
    assert "local_obs_target_frame:=${LOCAL_OBS_TARGET_FRAME:-map}" in out_int, \
        "integration bridges must pass LOCAL_OBS_TARGET_FRAME env var to launch"
    assert a.count("local_obs_target_frame:=${LOCAL_OBS_TARGET_FRAME:-map}") == 3, \
        "expected one local_obs_target_frame line per drone in standalone N=3"
    assert out_int.count("local_obs_target_frame:=${LOCAL_OBS_TARGET_FRAME:-map}") == 3, \
        "expected one local_obs_target_frame line per drone in integration N=3"

    print("self_test: OK")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
