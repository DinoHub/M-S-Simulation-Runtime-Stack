# casio-edge on the casio-siyi simulation

The casio-edge perception stack runs against a simulated SIYI A8 mini on a
PX4 quadrotor, in place of the Jetson's camera and flight controller. It
covers DA3 depth, the YOLO detector, the target localiser and tracker,
surveillance, the annotated stream, and the cloud relay.

What the simulation provides, under the names casio expects:

| casio needs | Comes from |
| --- | --- |
| `/camera/image_raw`, `/camera/camera_info` | the bridge, from the simulated A8 mini: 1280 x 720, 81 deg horizontal FOV, about 21 Hz, frame `siyi_optical` |
| `/tf`, `/tf_static` | the bridge: `map -> odom -> base_link -> siyi_body -> siyi_optical` |
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
  (TEVV-Airsim-ROS2-Bridge `fix/camera-tf-settings-degrees`).
- **No autonomous flight yet.** `localPlanner`, `pathFollower` and
  `mission_bt` are not in this compose, so nothing publishes
  `/mavros/setpoint_raw/local`. Fly the drone another way (QGroundControl,
  or MAVROS services) while testing perception.
- **Ideal lens.** The simulated camera is a distortion-free pinhole; its
  intrinsics are on `/camera/camera_info`. The real A8 mini's calibration
  applies to the drone, not to this camera.
- **`casio_description`** publishes the drone's URDF frames. If it names a
  frame the bridge already publishes (`base_link`, `siyi_optical`), the two
  TF trees conflict; check it with `ros2 run tf2_tools view_frames`.
