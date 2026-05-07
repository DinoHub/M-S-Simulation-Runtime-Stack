# Per-Drone AirSim ROS2 Bridge — Spec

> Audience: the team that owns the AirSim → ROS2 bridge process (the source
> behind `dhdevspace/auto_mns:tevv-airstack-ros2-multi-vehicle-gt` and
> `rpc_dynamic_vehicles.launch.py`).
>
> Author context: M-S-Simulation-Runtime-Stack maintainers. This is a
> handoff, not a code drop — what the bridge needs to do, not how.

## TL;DR

Today the bridge runs **once** per scenario and republishes all four
vehicles' AirSim data into a single ROS2 graph. The simulator runtime is
moving to **per-drone network isolation** (one `agent_internal-N` Docker
bridge network per drone), which means the bridge needs to run **once per
drone** so each instance can attach to its drone's network and stay out of
the others'. We are not asking for a rewrite. We are asking for the bridge
to be **parameterized to run as a single-vehicle process**, so the
simulator side can spin up N copies — one on each `agent_internal-N`.

The single bridge MUST NOT be attached to multiple `agent_internal-N`
networks at once. That re-creates the exact crosstalk we are isolating
against and is a non-starter for fault-injection experiments.

## Why this is changing now

The simulator runtime has split the network world into three lanes:

| Network            | Subnet         | Purpose                                                  |
|--------------------|----------------|----------------------------------------------------------|
| `agent_external`   | 172.28.0.0/24  | **Inter-drone Zenoh WAN.** Comms-under-test. Fault inject here. Carries ONLY `^/shared(/.*)?` topics via a Zenoh router. |
| `agent_management` | 172.30.0.0/24  | Foxglove / dashboards. Out of fault-injection blast radius. |
| `agent_internal-N` | 172.28.N.0/24  | **Per-drone LAN.** All sim-to-drone sensors/control for drone N live here. |

The Zenoh router for `^/shared` is already in place in the simulator (see
`compose/ardupilot-xfs/`'s `sim-router` service). Sensor / control / tf /
clock / AirSim RPC traffic is the **other half** of the integration and is
what this spec is about.

## Architecture target

```
                                                  agent_internal-1
                                          ┌──────────────────────┐
   ┌────────────────────┐                 │  airsim-ros-bridge-1 │
   │     AirSim         │  AirSim RPC     │  (Drone1 only)       │
   │ (host networking,  │◀────────────────┤                      │──── /drone_1/* (sensors, odom, tf, clock)
   │ Unreal on host)    │  host.docker.   │                      │     ROS_LOCALHOST_ONLY=0
   └────────────────────┘  internal:41451 └──────────────────────┘     ROS_DOMAIN_ID=1
                                                                          │
                                                                          ▼
                                                                   autonomy_stack-1
                                                                   (consumer)

                                                  agent_internal-2     ... same shape ...
                                                  agent_internal-3     ... same shape ...

   ┌────────────────────┐         ^/shared/sim/*           ┌──────────────────┐
   │  sim-shared-node   │────────────────────────────────▶ │   sim-router     │── agent_external ──▶ drones' ^/shared mesh
   │  (in sim-netns)    │  loopback DDS (LOCALHOST_ONLY=1) │ (zenoh-bridge)   │
   └────────────────────┘                                  └──────────────────┘
```

Two pipes, kept strictly separate:

- **Sensors / control** → per-drone bridge → `/drone_N/*` topics on
  `agent_internal-N`. NEVER touches `agent_external`. NEVER touches `^/shared/`.
- **`^/shared/*`** → `sim-router` only. NEVER carries sensor/control payloads.

## What the bridge instance MUST support

For each drone N, the simulator runtime will start one container of the
bridge image with the following knobs. The bridge needs to honor them all.
Most are env-var-driven (matches the existing
`dhdevspace/auto_mns:tevv-airstack-ros2-multi-vehicle-gt` style); CLI args
are equally fine if you'd rather plumb that way.

### Required interface

| Knob                       | Purpose                                                                 | Example                       |
|----------------------------|-------------------------------------------------------------------------|-------------------------------|
| `VEHICLES`                 | Single-element list now: `['Copter1']`. The bridge must handle len-1.   | `['Copter1']`                 |
| `ROS_NAMESPACE`            | Top-level namespace for THIS drone. Replaces the AirSim vehicle name.   | `/drone_1`                    |
| `AIRSIM_HOST_IP`           | Where AirSim RPC lives. From inside the bridge container, this is `host.docker.internal`. | `host.docker.internal` |
| `AIRSIM_HOST_PORT`         | AirSim RPC port. Single shared AirSim instance for all drones today.    | `41451`                       |
| `ROS_DOMAIN_ID`            | Per-drone DDS domain. Different domain per drone is the cleanest belt against accidental crosstalk. | `1` (drone 1), `2`, `3`       |
| `ROS_LOCALHOST_ONLY`       | **Must be 0 / unset.** This bridge is NOT in a locality jail; its job is to publish ON the bridge network so `autonomy_stack-N` can subscribe. | `0`                           |

### Currently honored knobs that remain valid

These already exist in the compose interface; please keep them working per-instance (i.e. each drone's bridge can have its own values):

- `ENABLE_LOCAL_OBS` (per-vehicle `pointcloud_registration_node`)
- `LOCAL_OBS_BUFFER_SEC`
- `LOCALIZATION_SOURCE` (`sim` / `external`)
- `ENABLE_LOCALIZATION`
- `USE_SIM_TIME`
- `ENABLE_STATIC_PCD`

### What the bridge MUST NOT publish

The bridge MUST NOT publish anything on the `^/shared/...` namespace. That namespace is owned by the autonomy stacks and the sim-side `sim-router` already takes care of it. If the bridge ever needs to surface ground-truth into the swarm fabric, route it through a separate sim-only ROS2 node (or ask runtime maintainers to add one) — keep the responsibilities split.

Forbidden topic categories on `agent_external` (i.e. forbidden anywhere the bridge could route them):

- `/clock`, `/tf`, `/tf_static`
- `cmd_vel` / control inputs
- camera / lidar / odom / IMU
- AirSim RPC traffic
- MAVROS topics

These all stay LAN-side on `agent_internal-N`.

## Topic ownership

The runtime side will only consume / forward the following from each
bridge instance:

```
/drone_N/...                       — per-drone sensors, odom, tf, control feedback (stays LAN-side)
```

The runtime side does NOT expect:

```
/shared/drone_N/...                — drones' autonomy stacks emit these themselves
/shared/swarm/...                  — drones' autonomy stacks emit these
/shared/sim/...                    — the sim runtime emits these from sim-shared-node
```

If your bridge currently emits ground-truth on a non-namespaced topic
(e.g. `/global_truth/poses`), please move it under `/drone_N/...` per
instance, or expose it as a single sim-only topic outside the bridge.

## Suggested implementation paths

Pick one:

### Option A — single-vehicle parameterization (preferred)

Modify `rpc_dynamic_vehicles.launch.py` (or its callers) so it can run with `VEHICLES=['Copter1']` plus a `ROS_NAMESPACE=/drone_1` remap, AND the process stays sane when N=1. This is the smallest delta and aligns with the existing env-var contract.

Concrete checklist:

- [ ] Confirm `VEHICLES` of length 1 doesn't trip any "expected ≥ 2" assumptions (e.g. multi-vehicle coordination logic, formation planners).
- [ ] Honor a `ROS_NAMESPACE` env var by remapping all topic publishers from `/Copter1/*` to `/drone_1/*`. Easiest path is a top-level `<group ns="${ROS_NAMESPACE}">` in the launch file.
- [ ] Confirm there are no singleton publishers (`/clock`, static TF root) that would conflict if the same launch fires three times on the same host. If any exist, gate them on a `--this-is-the-clock-master` flag so exactly one instance owns them.
- [ ] AirSim RPC client must tolerate sharing a single AirSim instance across N concurrent client connections (one per bridge process). If this isn't tested today, run three-way concurrent SubscribeImages and confirm no race conditions on the AirSim side.
- [ ] `pointcloud_registration_node` should also be one-per-process
      already (the comment in the runtime compose hints at this), but
      verify.

### Option B — central bridge + per-drone domain relay

Keep the central all-vehicle bridge as-is (running in some sim-internal
container, NOT on `agent_internal-N`), then add a thin relay container
per drone:

```
central-bridge (one process)              relay-drone-N (per drone)
  publishes /Copter{1..4}/...        ┌──▶ subscribes /CopterN/* (sim domain)
  on a sim-only DDS domain        ──┤    republishes /drone_N/* (drone-N domain)
                                    └──  attached to agent_internal-N
```

Costs more plumbing (you need a `domain_bridge`-style relay binary), but
unblocks the runtime side without modifying the bridge. We'll accept this
as an interim if Option A needs more time than we have.

### Option C — rejected

Attaching the existing all-vehicle bridge to ALL three `agent_internal-N`
networks. This is tempting and we will not accept it. Topic
namespacing ≠ network-level isolation, and it defeats the entire point of
the per-drone-LAN separation (fault-inject one drone's network → all
drones' sensors degrade simultaneously). Please don't ship this.

## How the runtime will instantiate Option A

For your reference — this is the compose snippet the runtime side will
add to `compose/ardupilot-xfs/docker-compose.yml`. Treat it as the
contract; if your env-var names differ, let us know which to use.

```yaml
x-airsim-bridge-base: &airsim-bridge-base
  image: ${AIRSIM_BRIDGE_IMAGE:-dhdevspace/auto_mns:airsim-ros2-bridge-single}
  pull_policy: if_not_present
  init: true
  restart: unless-stopped
  extra_hosts:
    - "host.docker.internal:host-gateway"
  environment: &airsim-bridge-env
    - ROS_LOCALHOST_ONLY=0
    - AIRSIM_HOST_IP=${AIRSIM_HOST_IP:-host.docker.internal}
    - AIRSIM_HOST_PORT=${AIRSIM_HOST_PORT:-41451}
    - ENABLE_LOCAL_OBS=${ENABLE_LOCAL_OBS:-true}
    - LOCAL_OBS_BUFFER_SEC=${LOCAL_OBS_BUFFER_SEC:-30.0}
    - LOCALIZATION_SOURCE=${LOCALIZATION_SOURCE:-sim}
    - ENABLE_LOCALIZATION=${ENABLE_LOCALIZATION:-true}
    - USE_SIM_TIME=${USE_SIM_TIME:-true}
    - ENABLE_STATIC_PCD=${ENABLE_STATIC_PCD:-true}

services:
  airsim-bridge-drone-1:
    <<: *airsim-bridge-base
    container_name: ardupilot-xfs-airsim-bridge-1
    hostname: airsim-bridge-drone-1
    environment:
      <<: *airsim-bridge-env
      - VEHICLES=['Copter1']
      - ROS_NAMESPACE=/drone_1
      - ROS_DOMAIN_ID=1
    networks:
      agent_internal-1: {}
    depends_on:
      ardupilot-drone-0:
        condition: service_healthy

  airsim-bridge-drone-2:
    <<: *airsim-bridge-base
    container_name: ardupilot-xfs-airsim-bridge-2
    hostname: airsim-bridge-drone-2
    environment:
      <<: *airsim-bridge-env
      - VEHICLES=['Copter2']
      - ROS_NAMESPACE=/drone_2
      - ROS_DOMAIN_ID=2
    networks:
      agent_internal-2: {}
    depends_on:
      ardupilot-drone-1:
        condition: service_healthy

  airsim-bridge-drone-3:
    <<: *airsim-bridge-base
    container_name: ardupilot-xfs-airsim-bridge-3
    hostname: airsim-bridge-drone-3
    environment:
      <<: *airsim-bridge-env
      - VEHICLES=['Copter3']
      - ROS_NAMESPACE=/drone_3
      - ROS_DOMAIN_ID=3
    networks:
      agent_internal-3: {}
    depends_on:
      ardupilot-drone-2:
        condition: service_healthy
```

Notes on this snippet:

- The `pointcloud_registration_node` runs INSIDE each bridge instance
  (`ENABLE_LOCAL_OBS=true`), publishing under that drone's namespace.
- `ROS_DOMAIN_ID` is set per drone so even if a bridge's DDS leaks across
  the bridge network, drones with different domain IDs won't see each
  other's traffic.
- `depends_on` is staggered against the ArduPilot SITL fleet so the
  bridge doesn't try to RPC AirSim before SITL has registered the
  vehicle. Adjust if the bridge handles RPC retries internally.

## Open questions for your team

Please answer these before we (runtime side) wire the per-drone services:

1. **Single-vehicle launch viability** — does
   `rpc_dynamic_vehicles.launch.py` work today with `VEHICLES=['Copter1']`,
   or are there singleton assumptions to fix?
2. **Namespace remap** — what's the cleanest way to make the bridge publish
   under `/drone_N` instead of `/Copter1`? Env-driven, CLI-driven, or
   launch-arg-driven? We're flexible.
3. **AirSim RPC fan-out** — is the AirSim Python/C++ client safe with N
   concurrent connections (one per bridge container) hitting the same
   `41451` socket?
4. **TF / clock ownership** — if N bridges run, who publishes `/clock`?
   Who roots the TF tree? Suggest gating those on a single
   `--clock-master` instance, or moving them out of the bridge entirely.
5. **Image artifact** — do we keep using
   `dhdevspace/auto_mns:tevv-airstack-ros2-multi-vehicle-gt`, or do you
   want to ship a separate `*-single-vehicle` tag once Option A lands?

## Verification gates the runtime side will run on integration

When your changes are ready, runtime side will run these checks before
merging the per-drone bridge into `ardupilot-xfs`:

- **Per-drone topic surface, no leak**: from inside `autonomy_stack-1`'s
  netns, `ros2 topic list` shows `/drone_1/...` topics but NOT `/drone_2`
  or `/drone_3`. Repeat from drone-2's and drone-3's perspective.
- **Sensor delivery**: `ros2 topic hz /drone_1/lidar/points` (or whatever
  the lidar topic is) reports a non-zero rate inside `autonomy_stack-1`'s
  container.
- **MAVROS unaffected**: `/mavros/state.connected: true` per drone (the
  MAVROS↔SITL UDP path is independent of the bridge but lives in the same
  netns; we want to confirm the per-drone netns split doesn't break it).
- **No `^/shared` leak from the bridge**: `ros2 topic list` from inside
  the bridge container shows zero topics under `/shared/`. The bridge
  must not be a `^/shared` participant.
- **Fault injection sanity**: `tc netem` on `agent_internal-1` only
  degrades drone-1's sensor topics; drone-2 and drone-3 remain unaffected.
  Symmetric tests for `agent_external` (Zenoh) — degrading there must
  NOT affect any sensor topic.

## Out of scope for this spec

- The Zenoh `^/shared` router on the sim side (already implemented in
  `compose/ardupilot-xfs/`'s `sim-router` service).
- The autonomy stacks themselves.
- Multi-AirSim setups (one AirSim per drone). Today's design assumes one
  shared AirSim instance.
- Migrating ArduPilot SITL containers off host networking — they stay on
  host because their MAVLink UDP setup needs it.
