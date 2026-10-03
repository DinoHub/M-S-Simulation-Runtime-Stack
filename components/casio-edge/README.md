# casio-edge: a bring-your-own component

The casio-edge perception stack runs against a simulated SIYI A8 mini on a
PX4 quadrotor, in place of the Jetson's camera and flight controller. It
covers DA3 depth, the YOLO detector, the target localiser and tracker,
surveillance, the annotated stream, and the cloud relay.

casio is a component package (`component.yaml` in this folder): the
scenario describes only the world, the drone and its camera, and casio
attaches to it from outside. Nothing in a ScenarioSpec names casio; the same
scenario generated without it is a plain sim stack.

| Run | Attach |
| --- | --- |
| casio on the ideal camera | `scenarios/casio-siyi` + `--component components/casio-edge` |
| casio on the A8-like camera | `scenarios/casio-siyi-realism` + `--component components/siyi-a8-realism --component components/casio-edge` (order matters: the realism stage first) |

What the platform provides, under the names casio expects:

| casio needs | Comes from |
| --- | --- |
| `/camera/image_raw`, `/camera/camera_info` | the scenario's camera (the contract's `camera_primary`), renamed to casio's names because `component.yaml` consumes it `as:` them. Behind `siyi-a8-realism`, that component publishes them (it `provides` the camera) and the bridge's frames go to it as `/camera/image_sim`. |
| `/tf`, `/tf_static` | casio's own `pose_to_tf` (in `casio_pointcloud`) publishes `map -> base_link` from `/mavros/global_position/local`, as on the Jetson; `requires.localization_source: external` stops the bridge publishing anything above `base_link` |
| `/clock` | the bridge (every casio node runs with `use_sim_time:=true`) |
| a flight controller for MAVROS | the autopilot's MAVLink peer, given to every component container as `MNS_FCU_URL` (PX4's mavlink-router endpoint, `udp://:14560@px4-drone-1:14555`); `requires.bridge_mavros: false` turns the bridge's own MAVROS off |
| ROS 2 over CycloneDDS | `requires.rmw: cyclonedds` with `dds_config: cyclonedds.xml`, which every container in the stack then reads; the stack's ROS domain (1), not the Jetson's 42 (casio's config uses `Domain Id="any"`) |

A `requires` that contradicts what the scenario sets explicitly (say
`runtime.features.mavros: true`) fails generation with both named.

The platform puts casio's containers on Drone1's network as
`casio_edge_<service>` (reachable as `casio-edge-<service>`), mounts this
folder's `files` at `/cfg/component/` and the stack contract at
`/cfg/stack/contract.json`, and starts them after the bridge.

## On a new machine

1. Product setup, once: `./setup.sh`, then `./download-packs.sh --safti` (the
   SAFTI level, `safti-level` 1.0.3).
2. casio-config next to this file, not committed: hard-link or copy it so
   that `components/casio-edge/casio-config/models/` and
   `components/casio-edge/casio-config/deployment/configs/` exist
   (`cp -al ~/casio-config components/casio-edge/casio-config`). The
   generator stages it into each stack with hard links.
3. Images: `docker pull` `dhdevspace/auto_mns:casio-node`, `casio-da3`,
   `casio-detection` and
   `dhdevspace/auto_mns:tevv-airsim-ros2-bridge-humble-v1.0.0-camtilt.1`
   (the scenarios' `runtime.images.ros2_bridge`).
4. Disk: the casio images unpack to about 80 GB (`casio-da3` 39 GB,
   `casio-detection` 39 GB, `casio-node` 5 GB), plus about 1 GB per generated
   stack and two TensorRT engines on first start.

The Jetson's `cyclonedds.xml` is not used here; this folder's replaces it.

## Run

Components need an `mns-stacks` image that attaches them
(MnS-Integration-Platform `feat/components`). Until a release carries it,
export it before any `make` target below:
`export MNS_STACKS_IMAGE=dhdevspace/auto_mns:mns-stacks-v1.0.0-rc.services.8`.
The pinned image ignores `--component` and generates a plain sim stack.

```bash
make fly SCENARIO=casio-siyi COMPONENTS=components/casio-edge KEEP=1
make fly SCENARIO=casio-siyi-realism \
  COMPONENTS="components/siyi-a8-realism components/casio-edge" KEEP=1
```

`KEEP=1` brings the stack up and leaves it up; fly it with the bench below.
The first start of `detection` builds its TensorRT engine into the
`casio_edge_detection_x86_models` volume, which takes a few minutes. Later
starts reuse it. A campaign waits for `/detections` (the component's
`ready.topics`) before it flies.

The annotated stream is at `http://127.0.0.1:8889/annotated` (WebRTC) and
`rtsp://127.0.0.1:8554/annotated`. Only one casio stack can run at a time:
both publish those ports.

Stop with `make stop` (the last `make fly`), or `make stop STACK=<stack dir>`.

## Known limits

- **Camera tilt in TF.** Bridge images up to v1.0.0 publish a settings camera's
  pitch as radians, so the 25 deg down mount appears 7.6 deg up in TF while
  the image is rendered correctly. The scenarios pin
  `dhdevspace/auto_mns:tevv-airsim-ros2-bridge-humble-v1.0.0-camtilt.1`
  (TEVV-Airsim-ROS2-Bridge PR #82 on the v1.0.0 source) through
  `runtime.images.ros2_bridge`; drop that once the v1 image set pins a
  bridge with the fix.
- **Camera rate is 12 to 21 Hz, set by Unreal's GPU time.** The A8 mini
  delivers 25 fps. `bench/camera-rate.sh` (written for the stack before
  casio became a component; its paths are that machine's) recreates the bridge and measures
  `/camera/image_raw` beside load, CPU and GPU use;
  `bench/2026-09-30-camera-rate.csv` is the run that established it on an
  RTX 5080 / 24 cores. Both bridge images gave the same rates, host load
  stayed at 4 to 7, and the rate fell as the runtime host's GPU use rose from
  about 80 to 95 %, with or without an image pull running.
- **No autonomous flight yet.** `localPlanner`, `pathFollower` and
  `mission_bt` are not in any casio image, so nothing publishes
  `/mavros/setpoint_raw/local`. Fly the drone another way (QGroundControl,
  or MAVROS services) while testing perception, or let PX4 fly a mission:
  `STACK=generated/<stack> bench/square-flight.sh <label> --center-n N --center-e E --side 36 --alt 12`
  takes off, flies laps of a square with the nose on its centre, lands, and
  scores casio against sim truth (`summary.md`, `map.png`, the annotated
  video and cloud_relay's posts, under the stack's `outputs/flights/`).
  There is no obstacle avoidance: a tower taller than 36 m stands just south-west of
  home (about E -25..-8, N -19..-2), and something about 12 m tall sits near
  E 35, N 13. The ground-level mannequins near E 15..18, N 14..17 are a good
  target: 12 m altitude, 36 m square centred on E 17, N 15.5.
  `--line-n N [--line-e E]` flies out to that point and back instead of a
  square.
- **Landing at home can fall through the ground.** On 2 Oct a drone that
  had flown 100 m out and back started its landing over home and sank
  through the level (sim truth 156 m below ground and still falling, PX4
  stuck in "landing"); only a stack restart recovers it. Until that is
  understood, add `--hover-at-end`: the mission ends holding over home at
  `--alt`, and the run ends once the drone is back over home.
- **Ideal lens in casio-siyi.** There the camera is a distortion-free
  pinhole with its intrinsics on `/camera/camera_info`. the siyi-a8-realism
  component applies the real A8's calibration (K and plumb_bob D), but its compression
  is a JPEG round trip standing in for the A8's H.264 stream.
- **Pose is the PX4 estimate, not ground truth.** Because casio owns `map -> base_link`, TF carries the EKF pose that MAVROS reports; the sim's truth stays on the bridge's odometry topics.
- **`casio_description`** publishes the drone's URDF frames (today only `base_link -> gimbal_mount`; its moving joints need `/joint_states`). If it names a
  frame the bridge already publishes (`base_link`, `siyi_optical`), the two
  TF trees conflict; check it with `ros2 run tf2_tools view_frames`.
