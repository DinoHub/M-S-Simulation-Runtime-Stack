# casio-edge on the casio-siyi simulation

The casio-edge perception stack runs against a simulated SIYI A8 mini on a
PX4 quadrotor, in place of the Jetson's camera and flight controller. It
covers DA3 depth, the YOLO detector, the target localiser and tracker,
surveillance, the annotated stream, and the cloud relay.

What the simulation provides, under the names casio expects:

| casio needs | Comes from |
| --- | --- |
| `/camera/image_raw`, `/camera/camera_info` | the bridge, from the simulated A8 mini: 1280 x 720, 81 deg horizontal FOV, about 21 Hz, frame `siyi_optical` |
| `/tf`, `/tf_static` | casio's own `pose_to_tf` (in `casio_pointcloud`) publishes `map -> base_link` from `/mavros/global_position/local`, as on the Jetson; the bridge publishes the static `base_link -> siyi_body -> siyi_optical` and nothing above `base_link` (`prepare-stack.sh` sets `localization_source:=external` and `suppress_bridge_odom_tf:=true`) |
| `/clock` | the bridge (every casio node runs with `use_sim_time:=true`) |
| a flight controller for MAVROS | PX4 SITL, through its mavlink-router MAVROS endpoint `px4-drone-1:14555`, sysid 1 |
| ROS 2 over CycloneDDS, domain 42 | the stack is switched to CycloneDDS by `prepare-stack.sh`; everything shares `cyclonedds.xml` |

The casio containers join the generated stack's `agent_internal-1` docker
network, the documented way to attach an autonomy stack
(see "Connecting your autonomy stack" in the User Guide). They do not use host
networking: the stack's ROS graph and MAVLink router are not on the host.

## Before the first run

- Pull the images: `dhdevspace/auto_mns:casio-node`, `casio-da3`,
  `casio-detection`.
- Unpack casio-config so that these exist, or point `CASIO_DEPLOYMENT_DIR`
  and `CASIO_MODELS_DIR` at your copies:
  - `$CASIO_DEPLOYMENT_DIR/configs/localisation_pipeline_params.yaml`
  - `$CASIO_MODELS_DIR/detection/yolo11s.onnx`
  - `$CASIO_MODELS_DIR/depth_anything_v3/DA3METRIC-LARGE.onnx`

The Jetson's `cyclonedds.xml` is not used here; this folder's replaces it.

## Run

```bash
./product.sh cli runtime --scenario /workspace/scenarios/casio-siyi \
  --out /workspace/generated/casio-siyi --no-run
scenarios/casio-siyi/casio/prepare-stack.sh          # after every regeneration
./product.sh cli run-stack --stack /workspace/generated/casio-siyi --detach
docker compose -f scenarios/casio-siyi/casio/compose.sim.yml up -d
```

The first start of `detection` builds its TensorRT engine into the
`detection_x86_models` volume, which takes a few minutes. Later starts reuse it.

The annotated stream is at `http://127.0.0.1:8889/annotated` (WebRTC) and
`rtsp://127.0.0.1:8554/annotated`.

Stop with `docker compose -f scenarios/casio-siyi/casio/compose.sim.yml down`,
then `./product.sh cli stop --stack /workspace/generated/casio-siyi`.

## Known limits

- **Camera tilt in TF.** Bridge images up to v1.0.0 publish a settings camera's
  pitch as radians, so the 25 deg down mount appears 7.6 deg up in TF while
  the image is rendered correctly. Projections into `map` (target localiser,
  point cloud) are wrong until the bridge carries the fix
  (TEVV-Airsim-ROS2-Bridge `fix/camera-tf-settings-degrees`). A local image
  with the fix on the v1.0.0 source is
  `dhdevspace/auto_mns:tevv-airsim-ros2-bridge-humble-v1.0.0-camtilt.1`
  (branch `fix/camera-tf-degrees-v1.0.0`); set it as `ROS2_IMAGE` in the
  generated stack's `.env`.
- **Camera rate is 12 to 21 Hz, set by Unreal's GPU time.** The A8 mini
  delivers 25 fps. `bench/camera-rate.sh` recreates the bridge and measures
  `/camera/image_raw` beside load, CPU and GPU use;
  `bench/2026-09-30-camera-rate.csv` is the run that established it on an
  RTX 5080 / 24 cores. Both bridge images gave the same rates, host load
  stayed at 4 to 7, and the rate fell as the runtime host's GPU use rose from
  about 80 to 95 %, with or without an image pull running.
- **No autonomous flight yet.** `localPlanner`, `pathFollower` and
  `mission_bt` are not in this compose, so nothing publishes
  `/mavros/setpoint_raw/local`. Fly the drone another way (QGroundControl,
  or MAVROS services) while testing perception.
- **Ideal lens.** The simulated camera is a distortion-free pinhole; its
  intrinsics are on `/camera/camera_info`. The real A8 mini's calibration
  applies to the drone, not to this camera.
- **Pose is the PX4 estimate, not ground truth.** Because casio owns `map -> base_link`, TF carries the EKF pose that MAVROS reports; the sim's truth stays on the bridge's odometry topics.
- **`casio_description`** publishes the drone's URDF frames (today only `base_link -> gimbal_mount`; its moving joints need `/joint_states`). If it names a
  frame the bridge already publishes (`base_link`, `siyi_optical`), the two
  TF trees conflict; check it with `ros2 run tf2_tools view_frames`.
