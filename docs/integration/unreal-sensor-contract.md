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

### 3. The camera delivers ~3.5 new frames per second, not 30 — corrected

An earlier revision reported the image rate as 30.57 Hz. **That was 8x
inflated.** Recording header stamps with a content hash per message
(`tevv_ws/testing/stamp_capture.py`) showed 606 messages in 20 s carrying only
74 distinct frames: the bridge polls AirSim at `POLL_RATE_HZ=30` and re-emits
the last frame under its original stamp. Real new-frame rate: median ~6 Hz,
mean ~3.5 Hz, gaps up to 0.95 s. The `median_hz: null` in the raw JSON was the
tell. `camera_info` — bytes, no transport cost — shows the same 68-74 distinct
stamps, so the bridge itself received that few captures: capture-bound, not
transport-bound.

Eliminated by experiment, each a sim restart: fisheye resolution (1344 → 672,
no change), main viewport (1920x1080 → 640x360, no change), and
`FisheyeMotionAdaptive` (applies only when face-slicing is on; `slicing=0`
here). The scheduler in `FisheyeCubeCaptureComponent::TickComponent` renders
once per game tick when the tick is slower than the target interval, so the
capture rate simply IS the game tick rate. The sim ticks at ~3.5-6 Hz on this
level with the GPU at 96-97%, and the log shows 545 pipeline-state objects
compiled on first use (`r.PSOPrecaching=0`, no binary cache), which is where
the ~0.9 s stalls come from. The capture-perf keys (`FisheyeCaptureFPS`,
`FisheyeMaxStalenessMs`, `FisheyeMotionAdaptive`,
`FisheyeMotionThresholdRadS`) are read from **`CaptureSettings[i]`**
(`AirSimSettings.hpp:1990`), not the camera object, and the stack generator
does not expose any of them. Raising them cannot beat the tick rate anyway.
Owner: TEVV-Airsim (level cost, PSO precaching) and MnS-Integration-Platform
(expose the keys).

One real stamp defect on top: 1-2 of ~75 stamps carry two DIFFERENT frames.
Rare; noted for the bridge.

### 4. Exposure is spec-controllable, and -1.5 was not enough

`exposure_compensation` on the camera's capture settings reaches
`ExposureCompensation` in settings.json. At the default the frame centre was
255; at -1.5, median 163 with 6.6% saturated; at **-11**, median 62, p95 193,
0.0% saturated, 5.2% black, with ground, buildings and cloud edges all
textured. -11 is the value the VIO work below used.

### 5. What OpenVINS did on the corrected inputs

With mask, calibration and exposure right, OpenVINS runs and, on the
climb-and-yaw variant of the square (`square_yawkick.yaml`), initialises —
inconsistently. Across otherwise identical flights: static init once (at rest,
velocity 0, final position 1.70, -0.19, -0.04 m — plausible for the 1 m
square), dynamic init once (bad initial velocity, 2 m/s constant drift, ATE
63 m), and no init twice. Static init cannot see the still-to-moving
transition at 3.5 frames/s (each 1 s half-window holds ~3 frames), and
dynamic init sits at its feature floor (32-37 tracked; gate is
`0.75 * init_max_features`).

Even when initialised, every update reports **`MSCKF update (0 feats)`** —
the filter is dead-reckoning on a noise-free IMU, not doing VIO. At this frame
rate on an open level the per-frame baseline is ~6 cm and features are tens of
metres away (parallax f*b/D: 4 px at 50 m), so triangulation has nothing to
work with. That is a property of frame rate x scene scale, and tuning will not
recover it. **B should not start until the capture rate is fixed at the
source.**

### 6. Operational: log rotation, or this stack fills a disk

The generated stack's services use Docker's `json-file` driver with **no
rotation**. `px4-drone-1`'s mavlink-router emits `TCP dynamic: Error sending
tcp packet (Invalid argument)` plus an ANSI-redrawn `pxh>` prompt at
~1.6 GB/min; over one afternoon that log reached **239 GB** and filled a
1.8 TB root filesystem to the ext4 reserve. `tevv_ws/results/diag/
compose.logrotate.yaml` is the override used since (`max-size: 50m`); stackgen
should emit it. Two side effects worth knowing: Docker creates a missing
bind-mount SOURCE as a root-owned directory, so a launch attempted with an
incomplete config copy leaves `settings.json` as a directory that a later
launch mounts silently; and every Unreal segfault under `restart:
unless-stopped` is a core dump plus a relaunch.

### 7. Timestamps on the truth stream

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

## MAVLink: reachable, and MAVROS works — corrected

An earlier revision of this document reported MAVLink unreachable. **That was
wrong**: it probed port 14555, taken from the LEGACY `compose/px4-xfs` stack.
The generated stack's router listens elsewhere. Measured both:

```
px4-drone-1:14555   reachable=false  timeout          <- wrong port
px4-drone-1:14580   reachable=true   73 bytes back    <- AirSim_Inbound
```

The router config (`/tmp/mavlink-router/mavlink-router-0.conf`) also carries a
dedicated endpoint for MAVROS:

```
[UdpEndpoint MAVROS]  Mode=Normal  Address=127.0.0.1  Port=14560
```

`Mode=Normal` means the router actively STREAMS there, rather than waiting to
be spoken to. `127.0.0.1` is why this worked in the legacy stack, where PX4 and
`mavros_d1` were both `network_mode: host`, and why it fails for a MAVROS
container on `agent_internal-N`: the address resolves inside px4-drone-1 only.

MAVROS therefore has to share PX4's network namespace. Verified working:

```
docker run --network container:<px4-drone-1> ... \
  ros2 launch airsim_mavros_bringup mavros_bringup.launch.py \
    vehicle:=Drone1 fcu_url:=udp://:14560@127.0.0.1:14580 \
    target_system_id:=1 mavros_config:=mavros_px4.yaml

  /Drone1/mavros/state -> connected: true, mode: OFFBOARD
```

Two things this settles for D. The MAVROS namespace is `/Drone1/mavros`, not
the `/mavros` that `tevv_ws/testing/README.md` defaults to. And the service
must be emitted with `network_mode: service:px4-drone-N` rather than attached
to `agent_internal-N` — the same netns-sharing shape the stack already uses
elsewhere. Attempts that do NOT work, for the record: UDP direct to 14580 from
another container completes a version exchange but never receives HEARTBEAT, so
`connected` stays false; TCP to the router's `0.0.0.0:5760` QGroundControl
endpoint times out entirely, and PX4 logs `TCP dynamic: Error sending tcp
packet (Invalid argument)` continuously.

The probe sends a well-formed MAVLink v1 HEARTBEAT (CRC computed, not
hardcoded) and waits for any datagram, because PX4's router in udp-client mode
answers only once it has heard from a peer. A timeout here is a real negative,
not a malformed-frame artifact.

## What this obliges

- **B** builds against `/imu/data`, `/front_Scene/image`, `/front_Scene/camera_info`,
  `/ground_truth/odom` — a **flat** namespace with renames, not `/<vehicle>/…` —
  and does not start until the camera delivers real frames at a VIO-usable rate (section 3).
- **C** pins whatever B publishes, and must pin a runtime host ≥ `.3`.
- **D** emits the bench services on `agent_internal-N` at the drone's domain and
  sets `ENABLE_VIO=false`; MAVROS is the exception and must be emitted with
  `network_mode: service:px4-drone-N`, `fcu_url: udp://:14560@127.0.0.1:14580`,
  under the `/Drone1/mavros` namespace.

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
