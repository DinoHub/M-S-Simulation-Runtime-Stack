# super-demo

A bring-your-own container under test, attached to a stack with
`--component components/super-demo`. The container runs unmodified
(`dhdevspace/auto_mns:super_runtime-ms.1`); the platform's generic adaptor fits it to the stack from
`component.yaml` alone. The full walkthrough is the platform's guide,
`docs/guides/bring-your-own-container.md` (MnS-Integration-Platform).

## Fill-in steps

1. **Replace every `<fill in: ...>`.** `mns-stacks component check
   components/super-demo` lists the ones left; generation refuses a package that
   still has any.
2. **`container`**: the image, and the command that starts your node.
   The image's entrypoint `/ros_entrypoint.sh` still runs; `command` replaces only its CMD. init copied the image's CMD (`/bin/bash`) as a starting point.
   `gpu: true` gives it every GPU. `run_as: image` keeps the image's own user;
   `platform` runs it as the host user with `HOME=/tmp`.
3. **`consumes`**: the stack roles your container needs: `clock`,
   `ground_truth` (odometry truth), `gnss`, `imu`, `lidar`, `point_cloud`,
   `camera_primary` or `camera:<name>`, `stereo_primary` or `stereo:<name>`,
   `autopilot`. A scenario that lacks one fails generation, naming what it has.
4. **`adaptor.inputs`**: for each role, the topic names your container
   subscribes to. A role you list in `consumes` but not here is read by the
   container from the stack's own topic (see `check --stack` for its name).
   - cameras: `image`, `info`; stereo: `left`, `right`, `left_info`,
     `right_info`; anything else: `topic`;
   - `encoding`: what your container wants (the stack's cameras are `bgr8`):
     `rgb8`, `bgr8`, `rgba8`, `bgra8`, `mono8` or `passthrough`;
   - `max_hz`: a rate cap; `pair_tolerance_s`: stereo pairing (default 0,
     identical stamps); `camera_info`: `relay`, `contract` (built from the
     calibration, the baseline in the right camera's P) or `none`;
     `frame_id` / `left_frame_id` / `right_frame_id`: header rewrites.
5. **`adaptor.outputs`**: what your container publishes.
   - `to: estimate` makes a pose (`geometry_msgs/msg/PoseStamped`,
     `PoseWithCovarianceStamped`, `TransformStamped`, `nav_msgs/msg/Odometry`
     or a `/tf` transform with `tf: {parent, child}`) the scored estimate:
     `nav_msgs/Odometry`, odom ENU to base_link FLU, on /mns/estimate/odom.
     Say what your pose is of and in which convention:
     - `body.frame`: `base_link`, or the camera (`stereo_primary` is its left
       camera, `camera_primary`, `camera:<name>`);
     - `body.axes`: `FLU`, `FRD` or `RDF` (camera optical);
     - `world`: `ENU`, `NED`, or `first_pose` (your world is your body frame
       at the first output, as visual odometry usually has it);
     - `stamp`: `header`, `restore_sec` (your node keeps only the nanosecond
       field) or `receive`.
   - `to: /some/topic` relays anything else (detections, a path).
6. **`adaptor.config`**: config files your container reads, as templates in
   `files/`, rendered from the stack contract before it starts and mounted at
   `/mns/config` (or `container.config_mount`). Placeholders look like
   `{{ stereo_primary.left.fx }}`, `{{ stereo_primary.baseline }}`,
   `{{ imu.rate_hz }}`, `{{ camera_primary.T_cam_imu }}`;
   `check --stack` lists every one with its value.
7. **`adaptor.pacing`** (optional): `{"max_inflight": 2, "answered_by":
   "<an output's from>"}` feeds your container only while it has fewer than
   that many inputs unanswered, for a model slower than the camera.
8. **`ready`, `requires`, `scoring`, `record`**: when it is up, what the
   stack must be (`gpu.min_vram_gb`, `autopilot`), how it is scored, and
   what else to record.

A container this cannot fit can keep its own adaptor: set
`adaptor.image` (and `adaptor.command`), or replace `container` and `adaptor`
with your own `services` (the platform form; see the guide).

## Check, then attach

```bash
mns-stacks component check components/super-demo --stack generated/<a stack>
mns-stacks generate scenarios/<a scenario> --component components/super-demo --out generated/<name>
```
