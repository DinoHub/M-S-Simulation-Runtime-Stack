# Component onboarding demos

Four recorded demos of bringing your own container into MnS as a component under test, from
`mns-stacks component init` to a judged run in the dashboard. Each demo has three parts:
- a component package (`components/<id>-demo/`);
- a scenario;
- a test plan (`CampaignSpec.<plan>.yaml`) that attaches the component by id.

**Draft branch, not for merging into v1.1.** SUPER and MIGHTY are held out of v1.1 until their
licence review, and these packages exist to show onboarding, not to ship.

| Demo | Component (kind, image) | Scenario | Test plan | Result when recorded |
|---|---|---|---|---|
| SUPER planner | `super-demo` (planner, `dhdevspace/auto_mns:super_runtime-ms.1`) | `planner-xfs-ardupilot` | `CampaignSpec.super-first-flight.yaml` | FAIL: goal success rate and minimum clearance |
| OpenVINS on XFS | `openvins-demo` (estimator, `dhdevspace/auto_mns:vio-estimator-openvins-69488123`) | `stereo-xfs-ov` | `CampaignSpec.openvins-first-flight.yaml` | PASS, position error 1.57 m |
| OpenVINS on SAFTI | `openvins-demo` | `stereo-safti-ov` | `CampaignSpec.openvins-over-safti.yaml` | PASS, position error 0.27 m |
| MIGHTY planner | `mighty-demo` (planner, `dhdevspace/auto_mns:mighty_algo_only`) | `planner-xfs-mighty` (yard-detour course) | `CampaignSpec.mighty-first-flight.yaml` | FAIL: not consistent from run to run (below) |

The scenarios are copies made for the demos, so the originals stay as they were:
- `stereo-xfs-ov` and `stereo-safti-ov` are stereo VIO scenarios.
- `planner-xfs-mighty` is `planner-xfs-ardupilot` with the yard-detour goal course.

MIGHTY behaved differently from run to run with the same files:
- **The recorded run:** MIGHTY reported ready after take-off but never planned (plan rate 0). The vehicle hovered 39 m from the goal until the 120 s timeout, at 0.38 m clearance, and the recording failed its flight-controller check.
- **The rehearsal and a retry:** both planned at about 46 Hz (5 ms per replan), but stalled about 15 m short in front of the structure, at 0.27 m/s mean speed and 0.1 m clearance, with gaps of up to 13–17 s between plans.

Plan runs do not yet keep component container logs, so the run that never planned left nothing to diagnose.

## What you need

- **This repository on `integration/v1.1`** (or this branch, which is built on it). Its pinned images are:
  - `mns-stacks-v1.1.0-dev-579e330`;
  - dashboard `-v1.1.0-dev-bc08ff2`.
- **The component images above**, all in the `dhdevspace/auto_mns` registry.
- **The level packs:** XFS for the XFS scenarios and SAFTI for `stereo-safti-ov`. Content in the dashboard shows whether they are ready.
- **For MIGHTY:** platform #143, which adds `interfaces` to a component.yaml. `mighty-demo` uses it, so it does not load without #143. MIGHTY publishes its trajectory as a `dynus_interfaces` type, and without it that topic is not recorded. The demo ran on `mns-stacks-v1.1.0-dev-579e330-if.1`, which is 579e330 plus #143.

## Run one from the dashboard

1. `make dashboard`, then open Scenario Configuration and pick the scenario.
2. **Test plan** opens the plan. The component shows under Components, and its checks show under Checks.
3. **Run**. Watch on Monitor. When the run ends, Monitor offers **See results in Analysis**.

The plans end "When the mission finishes", with a few seconds of extra recording. For an estimator, that
also keeps drift on the ground out of the score. Ending "When the vehicle lands" needs a MAVLink endpoint,
which generated stacks do not publish.

## Run one from a terminal

```bash
make campaign ARGS="run scenarios/stereo-xfs-ov/CampaignSpec.openvins-first-flight.yaml"
```

## Make your own

```bash
make stacks ARGS="component init my-thing --container <image> --kind estimator"
```

This writes `components/my-thing/component.yaml` and a README. The README lists every field and the checks
a component of that kind can be judged on. The demo packages above are worked examples of the result.
