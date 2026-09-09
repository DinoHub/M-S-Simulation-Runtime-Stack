# Unreal sensor contract, measured

Sub-project A of the tevv_ws integration. Design:
`docs/superpowers/specs/2026-09-09-tevv-ws-sensor-contract-design.md`.
Raw data: `unreal-sensor-contract.json`.

Measured 2026-09-09 against a generated UE 5.8.2 stack:

| | |
| --- | --- |
| level pack | `safti-level@1.0.1` `sha256:601f1cf4…` |
| runtime host | `tevv-runtime-host-20260909.3` — **override**, the catalog still pins `.2` |
| ros2 bridge | `tevv-airsim-ros2-bridge-humble-20260826@sha256:e661e37f…` |
| scenario | `scenarios/contract-probe-safti`, one drone, ROS domain 1 |
| launch | `ENABLE_VIO=false` (mandatory, see below) |
| probe window | 60 s |

## Verdict: GO

Monocular VIO is viable on this stream. Every no-go threshold passes, with
margin on the one that mattered most.

| Measurement | Gazebo control | Unreal 5.8.2 | Threshold | |
| --- | --- | --- | --- | --- |
| IMU rate (mean) | 200.00 Hz | **200.00 Hz** | ≥ 100 Hz | pass |
| IMU ≥ 5× image | 10× | 6.5× | ≥ 5× | pass |
| Image rate (mean) | 17.49 Hz | 30.57 Hz | present | pass |
| Camera QoS | RELIABLE | **RELIABLE** | must be RELIABLE | pass |
| `/clock` | monotonic, 1.0000× | monotonic, **1.0000×** | monotonic | pass |
| Truth rate | 50.00 Hz | 49.998 Hz | — | — |
| Truth/IMU tilt | 0.363° | **0.000°** | ≤ 5° | pass |
| Gravity magnitude | 9.879 | **9.8067** | ~9.81 | pass |

The Gazebo column is the control: `tevv_ws`'s README states 200 Hz IMU and
20 Hz camera, and the probe reproduced both at 0.00% error before it was
pointed at Unreal. That is what makes this column trustworthy.

The two headline risks in the design document are retired. IMU runs at exactly
200 Hz — the design flagged this as "the single measurement most likely to sink
the project", because the legacy `settings-px4.json` declared no IMU sensor at
all. The generated 5.8.2 spec declares one explicitly. And the camera publisher
is RELIABLE, which OpenVINS requires.

Truth frames are `map` → `base_link`, gravity-aligned to 0.000°: ROS convention,
usable as a metrics reference without transformation.

## Three caveats that constrain B, C and D

### 1. `ENABLE_VIO=false` is mandatory, and non-obvious

Generated stacks set `ENABLE_VIO=${ENABLE_VIO:-true}`. With VIO on, a fisheye
camera rides the iceoryx shared-memory path and publishes **no ROS camera topic
at all** — `tools/preview_topics.py` states it: "NOT on ROS — camera rides the
iceoryx SHM fisheye path". OpenVINS and this probe both subscribe to a ROS
`Image`, so both see nothing.

The flag exists for AirSim's own VIO path. Turning it on blinds an external VIO
consumer. Either D emits `ENABLE_VIO=false` when `vio_bench` is selected, or
`tevv_ws` learns to read iceoryx.

### 2. The published `camera_info` does not describe the camera

```
width/height    1344 x 1344
K               fx 405, fy 405, cx 672, cy 672
distortion_model "plumb_bob"
D               [0, 0, 0, 0, 0]
```

The ScenarioSpec configures a **190° fisheye** (`fisheye_model: 0`,
Kannala-Brandt). The bridge publishes `plumb_bob` with **zero distortion**. A
190° lens is not a pinhole and its distortion is not zero, so these intrinsics
cannot be used for VIO as published: OpenVINS would need the Kannala-Brandt
model and real coefficients.

`tevv_ws/testing/README.md` already warns that intrinsics must describe the
actual sensor and that Gazebo's must not be copied. This is that warning, made
concrete: the calibration must be authored, and the bridge should publish the
fisheye model rather than a pinhole placeholder.

### 3. Timestamps are bursty, and duplicated

`min_dt` is exactly **0.0** on all three streams — IMU, image and truth — so
multiple messages share a header stamp.

For the image stream more than half the intervals are zero, which is why
`median_hz` is `null`: the median gap is 0 and a rate cannot be derived from
it. The mean says 30.57 Hz, but `max_dt` is 0.75 s and jitter 121 ms, so frames
arrive in bursts rather than at 30 Hz.

The mean rate satisfies the thresholds and the verdict stands, but a VIO
front end cares about the arrival pattern, not just the average. Whether this
is sim-clock quantisation, bridge batching, or a genuine stall needs a look
before OpenVINS is tuned. It is not a probe artifact: the Gazebo control on the
same code produced clean non-zero intervals.

## MAVLink: NOT reachable

```
px4-drone-1:14555   reachable=false   error=timeout   bytes_received=0
```

`MAVLINK_MODE=router` does not answer on `agent_internal-1`. This was pulled
into A specifically because it was the one thing that could still make D
expensive, and it has: D's MAVROS service is not the one-line `fcu_url` change
the design predicted. Either `px4-drone-1` must expose 14555 on the container
network, or MAVROS must reach PX4 another way.

The probe sends a well-formed MAVLink v1 HEARTBEAT (CRC computed, not
hardcoded) and waits for any datagram, because PX4's router in udp-client mode
answers only once it has heard from a peer. A timeout here is a real negative,
not a malformed-frame artifact.

## What this obliges

- **B** builds against `/imu/data`, `/front_Scene/image`, `/front_Scene/camera_info`,
  `/ground_truth/odom` — a **flat** namespace with renames, not `/<vehicle>/…`.
- **C** pins whatever B publishes, and must pin a runtime host ≥ `.3`.
- **D** emits services on `agent_internal-N` at the drone's domain, sets
  `ENABLE_VIO=false`, and solves MAVLink reachability before any trajectory work.

## Blockers outside this sub-project

- **The catalog still pins `tevv-runtime-host-20260909.2`**, which cannot load
  any pack: it ships no UltraDynamicSky and no `MPC_Landscape`. `.3` fixes both.
  Repin with `tools/images.sh bump --only tevv_runtime_host_ue582 && sync`.
- **Five of six levels lack a `PlayerStart` tagged `MnSScenarioOrigin`** and abort
  with `Generic pack host requires exactly one … found 0`. Only `safti-level`
  (and the VIO gauntlet) carry it — which is why this measurement used Safti.
  `packs/README.md` documents this for `condo-level` only; it is broader. Fix is
  at pack build time, with republishes.

Re-measure when the runtime host or ros2_bridge pin moves.
