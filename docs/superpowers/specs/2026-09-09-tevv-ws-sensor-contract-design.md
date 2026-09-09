# tevv_ws / runtime-stack integration, sub-project A: measured sensor contract

Date: 2026-09-09
Status: design approved, not yet implemented

## Why this exists

`/home/mnsuser/tevv_ws` is a ROS 2 Humble VIO bench: Gazebo Fortress plus
OpenVINS, with a `testing/` component written for "External Unreal / PX4 /
MAVROS testing" whose Unreal topic names are still placeholders. The goal is
the full loop from its `testing/README.md`: a stored trajectory drives the
drone through MAVROS, OpenVINS estimates pose from the Unreal sensor streams,
and a metrics component scores the estimate against Unreal ground truth over a
repeatable commanded path.

That goal is agreed. This document is not that goal. It is the measurement
that has to happen before any of it can be built, and it exists as its own
sub-project because three other sub-projects depend on numbers nobody has
taken yet.

## The whole project, decomposed

The integration is four sub-projects, not one.

| | Sub-project | Repo | Blocked by |
| --- | --- | --- | --- |
| **A** | Measured sensor contract | investigation only | — |
| **B** | Image production: home repo for the three `tevv_ws` Dockerfiles, CI build, traced tags, push | new / TBD | A |
| **C** | Catalog rows and a `vio_bench` image-set role | M-S-Simulation-Runtime-Stack | A, B |
| **D** | stackgen emission: a `vio_bench` feature flag, services on `agent_internal-N`, and a MAVROS service generated stacks do not currently have | MnS-Integration-Platform (possibly MnS-ScenarioSpec) | A, C |

Each gets its own spec. This one covers A.

### Why A comes first

The agreed end state for the images is a proper CI-built, digest-pinned
publish. Sequencing B before A would commit a build pipeline and a public tag
line to an interface that has not been validated. This repository already has
two live examples of what that costs:

- `standalone_v2_ue582`'s generator and product shell were built on a
  developer machine and pushed by hand, because no published build accepted
  the 5.8.2 packs' whole-level contract. The catalog still carries the
  follow-up.
- The authored sim image (`tevv-airsim-authored-latest`) has a fully specified
  build contract in the catalog and cannot be built, because the source
  content is not on any machine here.

A is cheap in a way B is not. The bridge is reachable, `/clock` exists, and
most of the probe already exists. It is a measurement exercise, and it can
invalidate B and D before either is paid for.

## What is already known

Established by reading the stack, not assumed:

- **Topics line up.** For `px4-xfs` drone 1, the bridge publishes
  `/Copter1/imu`, `/Copter1/Camera1_Scene/image` and its `camera_info`, and
  `/Copter1/ground_truth/odom` — matching `OPENVINS_IMU_TOPIC`,
  `OPENVINS_CAMERA_TOPIC` and `METRICS_TRUTH_TOPIC` respectively.
- **`/clock` exists.** `compose/px4-xfs/docker-compose.yml:647`: each per-drone
  bridge is its own `/clock` publisher inside its own domain, and N bridges on
  one domain would race `/clock` and reset tf2 buffers. `USE_SIM_TIME=true` is
  therefore viable, and the per-drone-domain split is load-bearing.
- **Domains differ.** The stack uses `ROS_DOMAIN_ID=${DRONE_1_DOMAIN_ID:-1}`,
  so 1. `tevv_ws` defaults to 42 and to `ROS_LOCALHOST_ONLY=1`.
- **The two runtime paths are not equivalent.** Legacy `compose/px4-xfs` has a
  `mavros_d1` service on `network_mode: host`. Generated stackgen output
  (`generated/xfs-scenario`) has no MAVROS service at all, and its bridge sits
  on an internal `agent_internal-1` bridge network. The target is the
  generated path, so D has to add MAVROS; A does not need it.
- **There is no precedent for a live ROS consumer in a generated stack.**
  `sim-real-eval-worker` is the only evaluation container, and it attaches to
  `sim_network` with no ROS environment at all — it consumes bags from
  `/data/runs`, not live DDS.
- **`probe.py` already covers part of A.** `tevv_ws/testing/probe.py`
  subscribes camera, IMU and truth; asserts the camera publisher is
  `RELIABLE`, which is OpenVINS' stated requirement; confirms the sim clock is
  running; and checks all three timestamps share a base within 0.5 s.

### The gaps that make A necessary

- `settings-px4.json` declares `Camera1` at 1280x720 with `FOV_Degrees` unset
  and `ClockType: None`. The committed ScenarioSpec edits under `scenarios/`
  use different resolutions again. Nothing states the true intrinsics.
- The declared sensor list is `[Barometer, Gps, LidarSensor1]`. There is **no
  IMU entry**, yet the bridge publishes `/Copter1/imu` unconditionally — so
  IMU comes from vehicle state and its rate is tied to the sim update rate,
  not to a sensor specification. OpenVINS monocular wants roughly 200 Hz.
  This is the single measurement most likely to sink the project.
- Image publication rate and durability are unmeasured. `probe.py` checks
  reliability but not rate.
- `/clock` monotonicity under load is unmeasured. Unreal can run slower than
  wall time.
- Ground-truth frame conventions are undocumented: whether
  `/Copter1/ground_truth/odom` is gravity-aligned metres on the same body
  origin as the IMU, in ROS pose quaternions. `testing/README.md` requires all
  of that and warns that Unreal coordinates and units need conversion.

## Design

### Component 1: attach

A compose overlay in `tevv_ws` that joins a running generated stack rather
than starting one. The stack's networks are named by Compose project, so the
overlay references the network as `external` and takes the name as a variable.

```yaml
networks:
  stack:
    external: true
    name: ${MNS_STACK_AGENT_NETWORK:?e.g. px4-xfs-xfs-scenario-single_agent_internal-1}
```

The probe service joins `stack` with `ROS_DOMAIN_ID` matching the drone under
test, `ROS_LOCALHOST_ONLY=0`, and `USE_SIM_TIME=true`.

One drone's domain, one probe. Straddling two domains would put two `/clock`
publishers in scope, which the bridge comment says resets tf2 buffers.

The brittleness of the generated network name is accepted here and recorded as
an input to D: if the integration becomes a product feature, stackgen should
emit a stable attach point rather than leaving consumers to guess a
project-prefixed name.

### Component 2: measure

`tevv_ws/testing/contract_probe.py`, beside `probe.py` rather than replacing
it. `probe.py` is a flight precondition — a fast boolean gate before
commanding motion — and stays that. `contract_probe.py` is a measurement tool
that runs longer and reports numbers.

It reuses `probe.py`'s subscription set and adds:

| Measurement | Why it matters |
| --- | --- |
| IMU rate (Hz), mean and jitter | OpenVINS monocular initialisation |
| Image rate (Hz), mean and jitter | tracking stability |
| Camera QoS reliability and durability | OpenVINS' subscriber needs `RELIABLE` |
| `/clock` rate, monotonicity, wall-clock ratio | `USE_SIM_TIME=true` correctness |
| `camera_info` K, D, and actual resolution | calibration cannot be copied from Gazebo |
| Truth `frame_id` / `child_frame_id` | frame contract |
| Truth gravity alignment while stationary | metres, ROS quaternions, IMU body origin |
| Timestamp skew across all three | shared time base (already in `probe.py`) |

Output is JSON, so C and D can consume it, plus a human-readable summary.

### Component 3: record

Results land in a committed contract document that B, C and D cite instead of
re-deriving. Raw JSON is committed alongside it, with the stack image digests
and the scenario it was measured against, so a later re-measurement can be
compared rather than argued about.

## Testing

The probe is measured against a known-good source before it is trusted against
an unknown one.

`tevv_ws`'s own Gazebo stack has a stated validated baseline in its README:
200 Hz IMU and 20 Hz camera, with OpenVINS initialising and holding through
the 48-second exercise. `contract_probe.py` runs there first. If it reports
those numbers, the tool measures correctly.

Only then does it run against the generated Unreal stack. Without this
control, a 30 Hz IMU reading is ambiguous between "Unreal publishes slowly"
and "the probe counts wrongly", and the difference is days of work in the
wrong repository.

## Success criteria

A succeeds when the contract document exists, is backed by JSON from both the
Gazebo control run and the Unreal run, and states a go or no-go against
thresholds fixed in advance.

### No-go thresholds

Fixed here so the result is not renegotiated after the numbers arrive:

- IMU below 100 Hz sustained, or IMU rate below 5x the image rate
- image publisher `BEST_EFFORT` with no reliable option
- `/clock` absent, non-monotonic, or unrelated to published timestamps
- ground truth not gravity-aligned metres on the IMU body origin

Any of these means the fix is in the AirSim ROS 2 bridge — a fourth repository
— and B, C and D do not start until it lands. A no-go is a successful outcome
for A; it is the cheap discovery this sub-project exists to make.

## Out of scope

No MAVROS, offboard or arming — the generated path has no MAVROS service and
adding one is D. No stackgen changes. No images published. No catalog rows. No
OpenVINS tuning or calibration authoring: A measures what the intrinsics *are*,
it does not produce a calibrated `estimator_config.yaml`.

## Open question carried into B

The three `tevv_ws` images (`tevv-openvins`, `tevv-metrics`, `tevv-testing`;
`tevv-gazebo` is replaced by Unreal) are built locally and published nowhere,
and `tevv_ws` is not a git repository. B has to decide where they live before
it can build them. A does not need that answer — it runs locally built images
— but B cannot start without it.
