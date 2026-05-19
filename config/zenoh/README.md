# Sim-side zenoh router

`sim_router.json5` configures one `zenoh-bridge-ros2dds` container that joins
the autonomy team's inter-drone mesh on the externally-owned
`agent_external` network (172.28.0.0/24). The bridge forwards **only**
`^/shared(/.*)?` topics. Nothing else.

## Scope

This router lets the sim **participate in the inter-drone `^/shared` comms
fabric** as one additional ROS2/Zenoh node. It is NOT the transport for
sim→drone sensors, `/clock`, `/tf`, control, AirSim RPC, or MAVROS. Those
must stay on the sim's internal DDS / host paths.

`agent_external` is the comms-under-test network. Anything routed through it
becomes part of fault-injection experiments. Keep it minimal.

## `^/shared` namespace ownership

| Owner                            | Prefix                    |
|----------------------------------|---------------------------|
| Drone N's autonomy stack         | `/shared/drone_N/...`     |
| Swarm-level (drones write)       | `/shared/swarm/...`       |
| Sim-owned (scenario / experiment)| `/shared/sim/...`         |

Examples of sim-owned topics: `/shared/sim/scenario_phase`,
`/shared/sim/network_fault_state`, `/shared/sim/experiment_id`,
`/shared/sim/event_marker`.

The sim bridge enforces this split at the allow-list level (see
`sim_router.json5`):
- **publishers**: only `^/shared/sim(/.*)?$` is bridged outbound.
- **subscribers**: only `^/shared/drone_[0-9]+(/.*)?$` and
  `^/shared/swarm(/.*)?$` are bridged inbound.

A sim ROS2 node trying to publish on `/shared/swarm/...` or
`/shared/drone_2/...` will succeed locally but its messages will NOT reach
the mesh, by design.

## Autonomy-side requirements

Two things the autonomy team's compose stack MUST do for this to work:

1. **The Docker network must be literally named `agent_external`** — not
   `<project>_agent_external`. Use `name: agent_external` in their networks
   block, OR pre-create the network with `docker network create
   agent_external --subnet=172.28.0.0/24 --gateway=172.28.0.254`.

2. **Drone services must declare DNS aliases** on `agent_external`:
   ```yaml
   services:
     autonomy_stack-1:
       networks:
         agent_external:
           aliases: [autonomy_stack-1]
     # …repeat for -2, -3
   ```

Without these, the sim router will start cleanly but log DNS failures
connecting to peers (`Failed to resolve autonomy_stack-N`).

## Topology

- The autonomy team's compose creates `agent_external` and runs per-drone
  routers `autonomy_stack-{1..N}` on it.
- This sim attaches a single router (`sim-router`, id `2700`) to the same
  network via a small `sim-netns` holder container. The seed
  `connect.endpoints` point at `autonomy_stack-1..3:7448`.
- Gossip auto-connect (`autoconnect.router: ["router"]`) heals the mesh as
  drones come and go.

## Why a `sim-netns` holder?

`ros2-x11-node` and `sim-router` need to share a network namespace so
`ros_localhost_only: true` can put DDS on a shared loopback. If we made
either container the netns owner, restarting it would destroy the other
container's network world. `sim-netns` is a `sleep infinity` alpine that
doesn't need restarts; both real services use `network_mode:
service:sim-netns`. The sim-side network alias `sim-router` lives on
`sim-netns` (since that's the service actually attached to the network).

`extra_hosts` is also redeclared on every consumer, not just `sim-netns` —
`network_mode: service:X` shares the netns but NOT `/etc/hosts`.

## Overriding peers

Static for this milestone. To change the seed peer list, edit
`connect.endpoints` in `sim_router.json5`. Dynamic env-driven override is a
future enhancement; gossip auto-connect already handles peer churn after
initial mesh formation.
