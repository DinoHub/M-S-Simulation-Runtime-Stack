# casio-edge integration log

What it took to run the casio-edge stack (built for a Jetson Orin NX with a
SIYI A8 mini) against the simulation, in the order the problems were found.
Status is as of 30 September 2026.

## Fixes

| # | Problem | Fix | Where | Status |
| --- | --- | --- | --- | --- |
| 1 | No simulated SIYI camera; casio expects `/camera/image_raw` and `/camera/camera_info` | New PX4 scenario over SAFTI with one camera modelled on the A8 mini: 1280 x 720, 81 deg HFOV, 25 deg down | `scenarios/casio-siyi/ScenarioSpec.yaml` | done |
| 2 | The bridge names the camera `/camera/siyi/image_raw` | Topic renames `siyi_Scene/image -> camera/image_raw`, `siyi_Scene/camera_info -> camera/camera_info` in the generated `topic_names.yaml` | `casio/prepare-stack.sh` | done |
| 3 | casio runs CycloneDDS on domain 42; the stack ran FastDDS on domain 1 | `ros_domain_id: 42` in the spec; the stack switched to CycloneDDS through its `.env` and `config/dds/` | spec, `casio/prepare-stack.sh`, `casio/cyclonedds.xml` | done |
| 4 | casio's compose uses host networking, but the stack's ROS graph and PX4 MAVLink router live on a docker network | Every casio service joins the stack's `agent_internal-1` network; one `cyclonedds.xml` shared by stack and casio replaces the Jetson's | `casio/compose.sim.yml` | done |
| 5 | casio's MAVROS points at `udp://127.0.0.1:15550@`, where nothing listens in the sim | `fcu_url:=udp://:15550@px4-drone-1:14555` (PX4's mavlink-router MAVROS endpoint); the stack's own MAVROS is off so the two do not fight | `casio/compose.sim.yml`, spec `mavros: false` | done |
| 6 | `casio_pointcloud` and `casio_description` ran on wall time while everything is stamped from `/clock` | `use_sim_time:=true` on both | `casio/compose.sim.yml` | done |
| 7 | mediamtx and streaming talked over 127.0.0.1, which does not exist between containers | mediamtx binds all interfaces, publishes 8554 / 8889 / 8189/udp on the host's loopback; streaming publishes to `rtsp://mediamtx:8554/annotated` | `casio/compose.sim.yml` | done |
| 8 | TF showed the camera 7.6 deg **up** while the sim rendered it 25 deg down: the bridge read the settings `Pitch` (degrees) as radians | `degreesToRadians` before `toQuaternion` in the settings camera TF | TEVV-Airsim-ROS2-Bridge PR #82 (main); branch `fix/camera-tf-degrees-v1.0.0`, built locally as `tevv-airsim-ros2-bridge-humble-v1.0.0-camtilt.1` | PR open; local image in use |
| 9 | `base_link` had two parents: casio's `pose_to_tf` (`map -> base_link` from MAVROS) and the bridge (`map -> odom -> base_link`) | Bridge set to `localization_source:=external` and `suppress_bridge_odom_tf:=true`, so casio owns the pose as on the Jetson; the generator does not wire the second flag, so it is added to the bridge command | `casio/prepare-stack.sh` | done |

## Verified

On the live stack (PX4, SAFTI, RTX 5080), from containers on the stack network
using CycloneDDS on domain 42:

- `/camera/image_raw` 1280 x 720, `camera_info` fx 749.3, frame `siyi_optical`
- `/clock`, and a TF tree with one parent per frame; `map -> siyi_optical`
  pitched 25 deg down
- MAVROS connected to PX4 (sysid 1), `/mavros/global_position/local` in `map`
- casio images running: `casio-mavros`, `casio-description`,
  `casio-pointcloud`, `casio-streaming`, `casio-mediamtx`; the annotated stream
  online at `http://127.0.0.1:8889/annotated/` (H.264)

## Findings that are not bugs in this integration

- **Camera rate 12 to 21 Hz**, against the A8 mini's 25 fps. It follows the
  runtime host's GPU use (80 to 95 %), not host load, the bridge image, or an
  image pull. `bench/camera-rate.sh` and `bench/2026-09-30-camera-rate.csv`.
- **Pose is the PX4 EKF estimate**, because casio owns `map -> base_link`.
  Ground truth stays on the bridge's odometry topics.

## Still open

- **casio-config not on this machine.** `detection` needs
  `models/detection/yolo11s.onnx`, `depth_anything_v3` needs
  `models/depth_anything_v3/DA3METRIC-LARGE.onnx`, and `casio_range`,
  `casio_surveillance`, `cloud_relay` need
  `deployment/configs/localisation_pipeline_params.yaml` (topic, frame and RC3
  endpoint settings to review for the sim). The images carry no weights.
- **SIYI calibration to Kalibr**: waiting for `calibration.yaml`. The sim
  camera is an ideal pinhole, so this matters for the drone, not the sim.
- **No autonomous flight.** `localPlanner`, `pathFollower` and `mission_bt` are
  not in the x86 compose; nothing publishes `/mavros/setpoint_raw/local`.
- **Bridge fix not in a published image.** Regenerating the stack resets
  `ROS2_IMAGE` to v1.0.0, which brings the tilt bug back; set it again, or
  publish and pin the patched image.
- **`casio_description` gimbal joints** need `/joint_states`; only
  `base_link -> gimbal_mount` is published today.

## Commits

| Repo | Branch | Commits | Pushed |
| --- | --- | --- | --- |
| M-S-Simulation-Runtime-Stack | `feat/casio-sim-integration` | b1d2b1c scenario + casio overlay; 4038981 tilt image + camera-rate bench; 182775a TF ownership; this log | no |
| TEVV-Airsim-ROS2-Bridge | `fix/camera-tf-settings-degrees` | e0cec25 (PR #82) | yes |
| TEVV-Airsim-ROS2-Bridge | `fix/camera-tf-degrees-v1.0.0` | 3a3195c on `mns-v1.0.0-baseline` | yes |
