# mighty-demo

A bring-your-own container under test, attached to a stack with
`--component components/mighty-demo`. The container runs unmodified
(`dhdevspace/auto_mns:mighty_algo_only`); the platform's generic adaptor fits it to the stack from
`component.yaml` alone. The full walkthrough is the platform's guide,
`docs/guides/bring-your-own-container.md` (MnS-Integration-Platform).

## Fill-in steps

1. **Replace every `<fill in: ...>`.** `mns-stacks component check
   components/mighty-demo` lists the ones left; generation refuses a package that
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
   what else to record. Every field, its values and its default are in the
   field reference below; what kind planner can be judged on is under "Checks".

A container this cannot fit can keep its own adaptor: set
`adaptor.image` (and `adaptor.command`), or replace `container` and `adaptor`
with your own `services` (the platform form; see the guide).

## Check, then attach

```bash
mns-stacks component check components/mighty-demo --stack generated/<a stack>
mns-stacks generate scenarios/<a scenario> --component components/mighty-demo --out generated/<name>
```

## Checks your component can be judged on

With `scoring.builtin: planner`, a recorded run is scored on these. A check under
`scoring.checks` is judged by default; the dashboard's Test plan (Checks, Component
under test) turns each one on or off and changes its threshold per plan.

| `scoring.checks` key | Check | Unit | Judged as | Suggested threshold | On by default | Meaning |
| --- | --- | --- | --- | --- | --- | --- |
| `goal_success_rate` | Goals reached | ratio | at least | 1.0 | yes |  |
| `time_to_goal_s` | Time to goal | s | at most | 120 | no | Mean, over the goals reached. |
| `path_efficiency` | Path efficiency | ratio | at least | 0.5 | no | Straight-line distance over distance flown, per goal reached. 1 is a straight line. |
| `min_clearance_m` | Clearance to mapped obstacles | m | at least | 0.3 | yes | Closest approach to an obstacle more than 0.5 m above the local ground. |
| `jerk_rms` | Jerk | m/s^3 | at most | 50 | no | RMS jerk of the truth velocity; how smooth the commanded motion was. |
| `response_time_s` | Response time | s | at most | 5 | no | From the first goal to the vehicle moving 0.5 m. |
| `replan_interval_max_s` | Longest replan gap | s | at most | 2.0 | yes | The longest time without a new plan while there was a goal; the vehicle flies the old plan meanwhile. Needs the package's options.plan_topic. |
| `replan_interval_p95_s` | Replan interval | s | at most | 1.0 | no | Time between plans, 95th percentile. |
| `goal_to_plan_s` | Goal-to-plan latency | s | at most | 2.0 | no | From a new goal to the planner's first plan for it. |
| `plan_rate_hz` | Plan rate | Hz | at least | 2.0 | no |  |
| `command_rate_hz` | Command rate | Hz | at least | 10 | no | Setpoints per second while planning, from the topics the package records. |

For example:

```yaml
scoring:
  builtin: planner
  checks:
    goal_success_rate: {min: 1.0}
    min_clearance_m: {min: 0.3}
    replan_interval_max_s: {max: 2.0}
```

### Mission checks

Every run is also judged as a whole, from the simulator's own record. These need nothing
from the component; they are set in the dashboard's Test plan (Checks, Mission).

- Safety: Collisions (at most 0), Obstacle clearance (at least 0.5 m), Near misses (at most 0), Geofence violations (at most 0).
- Progress: Goal reached (required), Travel time (at most 300 s), Area coverage (at least 0.9), Zone visits, Path length (at most 500 m).
- Flight quality: Autopilot position error (at most 2.0 m), Path, speed and smoothness.
- Data quality: Complete record (required), Sensor health.

## Field reference

Every field component.yaml can hold, generated from the platform's schema
(`../_schema/components.schema.json`). Only `schema`, `id`, `kind` and
`container` are required; a field left out takes its default.

### Identity

| Field | Type | Values, default | What it is |
| --- | --- | --- | --- |
| `$schema` | string |  | Not needed in component.yaml: the yaml-language-server comment at its top names the schema. |
| `schema` | fixed | `mns.component.v1` | Always mns.component.v1. |
| `id` | string |  | Lower-case id; the compose services are &lt;id>_component, &lt;id>_adaptor and &lt;id>_config. |
| `name` | string |  | Human-readable name. |
| `version` | string |  | The component's version, recorded in the stack manifest. |
| `kind` | one of | `estimator`, `perception`, `planner`, `controller`, `other` | What the component is. An estimator usually produces `estimate`. |
| `vehicle` | integer | default `1` | The vehicle it runs beside (default 1). |

### The container

| Field | Type | Values, default | What it is |
| --- | --- | --- | --- |
| `container` | object |  | The component's own container, run unmodified. |
| `container.image` | string |  | Image reference: a tag or a digest. |
| `container.command` | list of strings |  | Overrides the image's CMD (the image's ENTRYPOINT still runs). init records the image's own CMD here. |
| `container.entrypoint` | list of strings |  | Overrides the image's ENTRYPOINT. |
| `container.working_dir` | string |  | Overrides the image's working directory. |
| `container.environment` | object |  | Environment variables for the container, as name: value. |
| `container.gpu` | boolean | default `false` | Give the container every GPU (compose gpus: all). |
| `container.run_as` | one of | `image`, `platform`; default `image` | image (default): the image's own user. platform: the host user with HOME=/tmp. |
| `container.config_mount` | string | default `/mns/config` | Where rendered config files appear in the container (default /mns/config). |
| `container.compose` | object |  | Extra compose keys for odd cases (shm_size, ulimits, healthcheck, ports bound to 127.0.0.1 ...). Networks, names and depends_on stay the platform's. |

### What it reads and writes

| Field | Type | Values, default | What it is |
| --- | --- | --- | --- |
| `consumes` | list |  | Stack contract roles the component needs. Generation fails when the scenario does not offer one. A role with no adaptor input is read by the container straight from the stack's topic. |
| `produces` | object |  | What the component produces. `estimate` is scored by the platform: nav_msgs/Odometry, world ENU, body FLU, on /mns/estimate/odom. Any other entry names a topic, which is recorded. |
| `produces.estimate` | object |  | The pose the platform scores (with scoring.builtin: estimate): nav_msgs/Odometry, world ENU, body FLU. |
| `produces.estimate.topic` | string |  | default (the contract's /mns/estimate/odom) or a topic. |
| `produces.<name>` | object |  | Any other output, under a name of your choice: a topic the component publishes, which is recorded (a planner's path, a detector's detections). |
| `produces.<name>.topic` | string |  | An absolute ROS topic name. |
| `produces.<name>.type` | string |  | Its message type, for the record. |
| `produces.<name>.description` | string |  | What it carries, for the record. |
| `record` | list |  | Extra topics kept when a campaign narrows its recording. |
| `interfaces` | list of objects |  | Your own ROS message packages, so the stack's recorder and viewer can record and show topics of those types (a planner's trajectory, say). Without this, a topic of a custom type is not recorded. |
| `interfaces[].image` | string | default `container` | The image that holds the packages: `container` (default), or an image reference. |
| `interfaces[].packages` | list of strings |  | The ROS packages that define the message types. |
| `interfaces[].setup` | list of strings |  | Absolute paths, inside that image, of the setup.bash files that make the packages visible. |

### When it is up

| Field | Type | Values, default | What it is |
| --- | --- | --- | --- |
| `ready` | object |  | When the component is up: the run director waits for these topics before it records and flies. Default: the estimate topic, for a component that produces one. |
| `ready.topics` | list |  | Topics that must publish before the run starts recording and flying. |
| `ready.timeout_s` | integer | default `240` | How long the run waits for the ready topics, in seconds (default 240). |
| `ready.after_takeoff` | list |  | Topics a goal mission waits for after take-off, before it sends the first goal: for planners that start only in the air. |

### What the stack must be

| Field | Type | Values, default | What it is |
| --- | --- | --- | --- |
| `requires` | object |  | What the stack must be for the component. A value the scenario leaves free is applied; one it contradicts fails generation. |
| `requires.gpu` | object |  | The GPU the component needs. |
| `requires.gpu.min_vram_gb` | number |  | Minimum GPU memory in GB, recorded with the stack. |
| `requires.autopilot` | list |  | Autopilots it works with (px4, ardupilot). Generation fails for a scenario that flies another. |
| `requires.rmw` | one of | `any`, `cyclonedds`, `fastrtps`; default `any` | The ROS 2 middleware it needs. Applied to the stack unless the scenario sets runtime.ros.rmw differently, which fails generation. |
| `requires.dds_config` | string |  | A CycloneDDS XML file in the package, used with rmw: cyclonedds. |
| `requires.localization_source` | one of | `any`, `sim`, `external`; default `any` | Where the stack's localization comes from: sim (truth) or external (an estimator). |
| `requires.point_cloud_frame` | one of | `any`, `map`, `odom`; default `any` | The frame the registered point cloud is published in: map or odom. |
| `requires.bridge_mavros` | one of | `any`, `true`, `false`; default `any` | Whether the bridge must run MAVROS (a planner that commands the vehicle through MAVROS needs it). |
| `requires.ros_domain_id` | integer or string | default `any` | The ROS domain id the vehicle must use. |

### How it is judged

| Field | Type | Values, default | What it is |
| --- | --- | --- | --- |
| `scoring` | object |  | How the platform scores the component after a run (as component.yaml has it). |
| `scoring.builtin` | one of | `estimate`, `planner` | A scorer the platform ships: estimate (an estimator against sim truth) or planner (a goal mission's legs, from the bag). |
| `scoring.image` | string |  | Your own scorer's image (a tag, or a key of images), run once after the stack stops. Needs command. |
| `scoring.command` | list of strings |  | Your scorer's command. It reads the run's bag and events and writes components/&lt;id>.json. |
| `scoring.needs` | list |  | Inputs the scorer needs: bag, events. When one is missing the result is skipped. Default: both for a built-in scorer. |
| `scoring.record` | list |  | Topics the scorer reads from the bag; they are kept when a test plan narrows its recording. |
| `scoring.options` | object |  | Options for the scorer, passed as MNS_SCORING_OPTIONS (JSON). estimate: rpe_delta_s, max_gap_s, max_diff_ms. planner: tolerance_m, plan_topic, voxel_m, max_clouds. |
| `scoring.checks` | object |  | Default thresholds, one per metric: {max: N} or {min: N}. The test plan can change them per run; see the checks section. |
| `scoring.timeout_s` | integer | default `600` | How long the scorer may run, in seconds (default 600). |
| `scoring.latency` | list of objects |  | Latencies the platform measures from the bag: the output's receive time minus the receive time of the input with the same stamp. |
| `scoring.latency[].name` | string |  | Name of the latency metric. |
| `scoring.latency[].input` | string |  | An absolute ROS topic name. |
| `scoring.latency[].output` | string |  | An absolute ROS topic name. |
| `scoring.latency[].label` | string |  | Label shown with it. |

### The generic adaptor

| Field | Type | Values, default | What it is |
| --- | --- | --- | --- |
| `adaptor` | object |  | How the platform's generic adaptor fits the container to the stack. Set `image` (and `command`) instead to run your own adaptor. |
| `adaptor.image` | string |  | A custom adaptor image to run instead of the generic one (inputs, outputs and pacing then do not apply; config templates still render). |
| `adaptor.command` | list of strings |  | The command for a custom adaptor image. |
| `adaptor.environment` | object |  | Environment variables for the adaptor, as name: value. |
| `adaptor.inputs` | list |  | For each consumed role, the topics your container subscribes to; the adaptor republishes the stack's topics under those names. |
| `adaptor.inputs[].role` | string |  | A stack contract role: clock, ground_truth (odometry truth), gnss, imu, lidar, point_cloud, camera_primary, camera:&lt;name>, stereo_primary, stereo:&lt;name>, autopilot. |
| `adaptor.inputs[].topic` | string |  | An absolute ROS topic name. |
| `adaptor.inputs[].image` | string |  | An absolute ROS topic name. |
| `adaptor.inputs[].info` | string |  | An absolute ROS topic name. |
| `adaptor.inputs[].left` | string |  | An absolute ROS topic name. |
| `adaptor.inputs[].right` | string |  | An absolute ROS topic name. |
| `adaptor.inputs[].left_info` | string |  | An absolute ROS topic name. |
| `adaptor.inputs[].right_info` | string |  | An absolute ROS topic name. |
| `adaptor.inputs[].encoding` | one of | `passthrough`, `rgb8`, `bgr8`, `rgba8`, `bgra8`, `mono8` | Image encoding the container gets (default passthrough). The stack's cameras are bgr8 (see the contract). |
| `adaptor.inputs[].max_hz` | number |  | Rate cap, on message stamps (sim time). |
| `adaptor.inputs[].pair_tolerance_s` | number |  | Stereo only: pair left and right whose stamps differ by at most this (default 0: identical stamps, which the bridge gives). |
| `adaptor.inputs[].camera_info` | one of | `relay`, `contract`, `none` | relay (default): republish the stack's CameraInfo. contract: build it from the contract's calibration (with the stereo baseline in the right camera's P), published with every image. none: no CameraInfo. |
| `adaptor.inputs[].frame_id` | string |  | Rewrite header.frame_id (camera and topic roles). |
| `adaptor.inputs[].left_frame_id` | string |  | Rewrites the left image's header frame id. |
| `adaptor.inputs[].right_frame_id` | string |  | Rewrites the right image's header frame id. |
| `adaptor.inputs[].qos` | one of | `reliable`, `best_effort` | Subscription reliability on the stack side (default reliable). |
| `adaptor.outputs` | list |  | What your container publishes: `to: estimate` makes a pose the scored estimate; `to: /topic` relays anything else. |
| `adaptor.outputs[].from` | string |  | An absolute ROS topic name. |
| `adaptor.outputs[].type` | string |  | The container's message type. For to: estimate one of geometry_msgs/msg/PoseStamped, geometry_msgs/msg/PoseWithCovarianceStamped, geometry_msgs/msg/TransformStamped, nav_msgs/msg/Odometry, tf2_msgs/msg/TFMessage. |
| `adaptor.outputs[].to` | string |  | estimate, or a stack topic to relay to. |
| `adaptor.outputs[].tf` | object |  | tf2_msgs/msg/TFMessage only: the transform to take. |
| `adaptor.outputs[].tf.parent` | string |  | The parent frame of the /tf transform to read. |
| `adaptor.outputs[].tf.child` | string |  | The child frame of the /tf transform to read. |
| `adaptor.outputs[].body` | object |  | What the container's pose is of, and in which axes (default base_link, FLU). |
| `adaptor.outputs[].body.frame` | string |  | The physical frame a pose is of: base_link (or imu, which sits on base_link), or a camera by role (a stereo role means its left camera unless .right is given). |
| `adaptor.outputs[].body.axes` | one of | `FLU`, `FRD`, `RDF` | Axis convention: FLU (x forward, y left, z up; REP 103), FRD (x forward, y right, z down; aerospace, TartanAir's camera 'NED'), RDF (x right, y down, z forward; the camera optical frame). |
| `adaptor.outputs[].world` | one of | `ENU`, `NED`, `FLU`, `FRD`, `RDF`, `first_pose` | The container's world frame: ENU (default) or NED, FLU/FRD/RDF axes at the stack's world origin, or first_pose (the world is the body frame at the first output, as most odometry has it). |
| `adaptor.outputs[].anchor` | one of | `none`, `first_position` | first_position moves the world origin to the first estimate's position (default none; the scorer aligns anyway). |
| `adaptor.outputs[].stamp` | one of | `header`, `restore_sec`, `receive` | header (default): the output's own stamp. restore_sec: the output keeps only the nanosecond field; take the seconds from the input it answers. receive: the sim time it arrived. |
| `adaptor.outputs[].frame_id` | string |  | Relays only: rewrite header.frame_id. |
| `adaptor.outputs[].qos` | one of | `reliable`, `best_effort` | Subscription reliability on the container's output (default best_effort: an arbitrary bring-your-own container's own QoS is unknown, and a best-effort subscriber matches either; reliable is stricter -- it only matches a reliably-publishing container, but then guarantees no silently dropped output, e.g. a container like MAC-VO that itself publishes reliable). |
| `adaptor.config` | list |  | Config files your container reads, rendered from the stack contract before it starts and mounted at container.config_mount. |
| `adaptor.config[].template` | string |  | A template in the package (files/...) with {{ placeholders }} filled from the stack contract. |
| `adaptor.config[].out` | string |  | Path under the container's config mount (default: the template's name). |
| `adaptor.pacing` | object |  | Feed the container only while it has fewer than max_inflight inputs it has not answered on answered_by (an output's `from`). Keeps a slow model from dropping frames on its own. |
| `adaptor.pacing.max_inflight` | integer |  | Feed the container only while fewer than this many inputs are unanswered. |
| `adaptor.pacing.answered_by` | string |  | An absolute ROS topic name. |
| `adaptor.pacing.timeout_s` | number |  | An unanswered input expires after this (default 1.0 s). |
| `adaptor.pacing.max_hz` | number |  | A wall-clock ceiling on paced inputs. |
| `adaptor.pacing.inputs` | list |  | Which inputs are paced (default every camera and stereo input). |

### How it is shown

| Field | Type | Values, default | What it is |
| --- | --- | --- | --- |
| `viz` | object |  | How the component is shown in the dashboard's Monitor. |
| `viz.layout` | string |  | A Lichtblick layout, relative to the component folder. |
| `viz.streams` | list of objects |  | Video feeds that are not ROS topics (an RTSP or WebRTC page), shown beside the viewer. |
| `viz.streams[].name` | string |  | The feed's name. |
| `viz.streams[].url` | string |  | Where the feed is served. |
