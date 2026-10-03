# casio-edge on the casio-siyi simulation

The casio-edge perception stack runs against a simulated SIYI A8 mini on a
PX4 quadrotor, in place of the Jetson's camera and flight controller. It
covers DA3 depth, the YOLO detector, the target localiser and tracker,
surveillance, the annotated stream, and the cloud relay.

Everything casio needs is in the ScenarioSpec, so generating the scenario
(from the dashboard or the CLI) gives a stack that runs casio with no further
step. Two scenarios share this folder:

- `scenarios/casio-siyi`: the camera as an ideal pinhole.
- `scenarios/casio-siyi-realism`: the same scenario with the camera made to
  look like the real A8's (Unreal motion blur, slower auto-exposure,
  chromatic aberration, lens flare; then the A8's Kalibr lens model, sensor
  noise and compression in the `camera_realism` service).

What the simulation provides, under the names casio expects:

| casio needs | Comes from |
| --- | --- |
| `/camera/image_raw`, `/camera/camera_info` | the bridge, from the simulated A8 mini: 1280 x 720, 82.1 deg horizontal FOV (the real A8 mini's calibrated fx), 11 to 21 Hz, frame `siyi_optical`; renamed by `mns.topics` in `extensions.yaml`. In casio-siyi-realism, `camera_realism` publishes them from `/camera/image_sim`. |
| `/tf`, `/tf_static` | casio's own `pose_to_tf` (in `casio_pointcloud`) publishes `map -> base_link` from `/mavros/global_position/local`, as on the Jetson; the bridge publishes the static `base_link -> siyi_body -> siyi_optical` and nothing above `base_link` (`runtime.ros.localization_source: external`) |
| `/clock` | the bridge (every casio node runs with `use_sim_time:=true`) |
| a flight controller for MAVROS | PX4 SITL, through its mavlink-router MAVROS endpoint `px4-drone-1:14555`, sysid 1 |
| ROS 2 over CycloneDDS, domain 42 | `runtime.ros` (`rmw: cyclonedds`, `dds_config: casio/cyclonedds.xml`) and `ros_domain_id: 42`; every container in the stack shares this folder's `cyclonedds.xml` |

The casio containers are `mns.services` in `extensions.yaml`. The generator
puts them on Drone1's `agent_internal-1` network with the stack's ROS domain,
middleware and DDS config, stages the files they mount into the stack
(`config/services/`, mounted at `/cfg/services/`), and starts them after the
bridge. They do not use host networking: the stack's ROS graph and MAVLink
router are not on the host.

This needs a stack generator with `runtime.ros`, `runtime.images` and the
`mns.topics` / `mns.services` extensions (MnS-Integration-Platform branch
`feat/stackgen-scenario-services`, built locally as
`dhdevspace/auto_mns:mns-stack-generator-v1.0.0-services.1`). The v1.0.0
generator rejects nothing here but ignores those blocks, so its stack runs
without casio.

## On a new machine

1. Product setup, once: `./setup.sh`, then `./download-packs.sh --safti` (the
   SAFTI level, `safti-level` 1.0.3).
2. This branch: `git fetch && git checkout feat/casio-sim-integration`.
3. casio-config next to the spec, not committed: hard-link or copy it so
   that `scenarios/casio-siyi/casio-config/models/` and
   `scenarios/casio-siyi/casio-config/deployment/configs/` exist
   (`cp -al ~/casio-config scenarios/casio-siyi/casio-config`). The
   generator stages it into each stack with hard links.
4. Images: `docker pull` `dhdevspace/auto_mns:casio-node`, `casio-da3`,
   `casio-detection` and
   `dhdevspace/auto_mns:tevv-airsim-ros2-bridge-humble-v1.0.0-camtilt.1`
   (the spec's `runtime.images.ros2_bridge`).
5. The generator image: build it from MnS-Integration-Platform
   `feat/stackgen-scenario-services` (`docker/stack-generator.Dockerfile`),
   tagged as above.
6. Disk: the casio images unpack to about 80 GB (`casio-da3` 39 GB,
   `casio-detection` 39 GB, `casio-node` 5 GB), plus about 1 GB per generated
   stack and two TensorRT engines on first start.

The Jetson's `cyclonedds.xml` is not used here; this folder's replaces it.

## Run

casio-siyi needs an `mns-stacks` image whose generator understands
`runtime.ros`, `runtime.images`, `mns.topics`, `mns.services`, `mns.interfaces`
and `mns.viz`
(MnS-Integration-Platform `feat/stackgen-scenario-services-v1`). Until a
release carries them, export it before any `make` target below:
`export MNS_STACKS_IMAGE=dhdevspace/auto_mns:mns-stacks-v1.0.0-rc.services.2`.

From the dashboard: `make dashboard`, pick casio-siyi (or
casio-siyi-realism), Generate, Run. SAFTI is a runtime-only level, so the
scenario is the hand-written spec in this folder, not a ScenarioLab export.

From the command line:

```bash
make fly SCENARIO=casio-siyi KEEP=1
```

`KEEP=1` brings the stack up and leaves it up; fly it with the bench below.
The first start of `detection` builds its TensorRT engine into the
`detection_x86_models` volume, which takes a few minutes. Later starts reuse it.

The annotated stream is at `http://127.0.0.1:8889/annotated` (WebRTC) and
`rtsp://127.0.0.1:8554/annotated`. Only one casio stack can run at a time:
both publish those ports.

Stop with `make stop` (the last `make fly`), or
`make stop STACK=generated/casio-siyi`.

## Known limits

- **Camera tilt in TF.** Bridge images up to v1.0.0 publish a settings camera's
  pitch as radians, so the 25 deg down mount appears 7.6 deg up in TF while
  the image is rendered correctly. The spec pins
  `dhdevspace/auto_mns:tevv-airsim-ros2-bridge-humble-v1.0.0-camtilt.1`
  (TEVV-Airsim-ROS2-Bridge PR #82 on the v1.0.0 source) through
  `runtime.images.ros2_bridge`; drop that once the v1 image set pins a
  bridge with the fix.
- **Camera rate is 12 to 21 Hz, set by Unreal's GPU time.** The A8 mini
  delivers 25 fps. `bench/camera-rate.sh` recreates the bridge and measures
  `/camera/image_raw` beside load, CPU and GPU use;
  `bench/2026-09-30-camera-rate.csv` is the run that established it on an
  RTX 5080 / 24 cores. Both bridge images gave the same rates, host load
  stayed at 4 to 7, and the rate fell as the runtime host's GPU use rose from
  about 80 to 95 %, with or without an image pull running.
- **No autonomous flight yet.** `localPlanner`, `pathFollower` and
  `mission_bt` are not in any casio image, so nothing publishes
  `/mavros/setpoint_raw/local`. Fly the drone another way (QGroundControl,
  or MAVROS services) while testing perception, or let PX4 fly a mission:
  `bench/square-flight.sh <label> --center-n N --center-e E --side 36 --alt 12`
  takes off, flies laps of a square with the nose on its centre, lands, and
  scores casio against sim truth (`summary.md`, `map.png`, the annotated
  video and cloud_relay's posts, under the stack's `outputs/flights/`;
  `STACK=generated/casio-siyi-realism` for the realism stack).
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
  pinhole with its intrinsics on `/camera/camera_info`. casio-siyi-realism
  applies the real A8's calibration (K and plumb_bob D), but its compression
  is a JPEG round trip standing in for the A8's H.264 stream.
- **Pose is the PX4 estimate, not ground truth.** Because casio owns `map -> base_link`, TF carries the EKF pose that MAVROS reports; the sim's truth stays on the bridge's odometry topics.
- **`casio_description`** publishes the drone's URDF frames (today only `base_link -> gimbal_mount`; its moving joints need `/joint_states`). If it names a
  frame the bridge already publishes (`base_link`, `siyi_optical`), the two
  TF trees conflict; check it with `ros2 run tf2_tools view_frames`.
