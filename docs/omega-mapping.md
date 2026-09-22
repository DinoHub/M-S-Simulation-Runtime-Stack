# Working backwards from omega.yaml

The Autonomy TEVV platform specs define a declaration called **`omega.yaml`**:
one file per test *suite*, compiled by `tevv-compile` together with each
component's `tevv.yaml` into run bundles — and, since the OSMO variant, into one
OSMO workflow per matrix point. This page maps that contract onto what this
repository already has, so the distance between them is a list rather than a
feeling.

Sources, all under `docs/platform-architecture/`: the baseline design
(2026-07-10) owns the omega contract; the OSMO variant (2026-07-13) owns
orchestration only; the Jenkins CI design (2026-08-18) owns the trigger plane
and adds sampled matrices. Both later specs list the omega contract first under
*what does not change*, so the baseline is authoritative for everything here.

## Status of the contract itself

**There is no schema file.** The downloaded architecture repository contains no
`schemas/` directory; `omega.schema.json`, `tevv.schema.json`, `ci.schema.json`
and `registry.sql` exist only as unexecuted task steps inside the doc-set plan.
The bridge's own `tevv.yaml` says as much in its header — *"the platform's
tevv.schema.json (doc-set plan Task 3) is not written yet"*.

So treat the JSON in the plan as design intent, not a shipped artifact, and
expect the field names to move. Several already contradict each other; the
important ones are listed at the end.

## The shape of omega.yaml

Required: `suite`, `tier`, `scenario`, `platform`, `stack`, `mission`,
`evaluation`. Optional: `verifies`, `faults`, `record`, `matrix`.
`additionalProperties: false` throughout.

Notably **absent**: `pool`, `priority` and `resources`. Pool and KAI priority
are derived from `tier`; resources come from each component's `tevv.yaml`.
That is worth knowing before anyone tries to put them in the file.

## Where this repository already sits

| omega key | What supplies it here | Fit |
| --- | --- | --- |
| `scenario` | `ScenarioSpec.yaml` — `environment` (pack id/version/digest) and `vehicles[]` | **poor, see below** |
| `platform.sim` | `runtime.profile` (`airsim_unreal_px4_docker`) | direct |
| `platform.autopilot` | `runtime.profile`, and `CampaignSpec.mission.autopilot` | direct |
| `platform.middleware` | implied — the bridge always runs MAVROS | direct |
| `platform.comms` | the generated stack's DDS config | direct |
| `stack[]` | `extensions.mns.vio_estimator` — the algorithm under test | good |
| `mission` | `CampaignSpec.mission` (`trajectory`, `done`, `timeout_s`) | good |
| `evaluation.gates` | `CampaignSpec.evaluation.evaluator` + `sim_real_eval` | **shape mismatch** |
| `record.topics` | `CampaignSpec.recording.topics` | direct |
| `matrix` | `CampaignSpec.variants[]` | good |
| `suite`, `tier`, `verifies` | nothing | absent here |

Read the other way, the pieces this repository has that omega has nowhere to
put are the more interesting half.

### omega.scenario cannot express a ScenarioSpec

This is the load-bearing gap. The whole of `scenario` is:

```yaml
scenario:
  world: xfs
  drones: 1
  origin: preset:xfs
```

`world` is a bare string, `origin` is constrained to `^preset:[a-z0-9-]+$`, and
the object is `additionalProperties: false`. A `ScenarioSpec` carries, and omega
has no slot for:

- **The sensor rig.** Cameras with position, FOV, resolution and exposure; IMU
  noise densities; which sensors exist at all. For a VIO suite this *is* the
  experiment — `vio-reference` exists to hold a stereo pair at a known baseline
  against a frozen kalibr chain. Under omega that rig would have to live in the
  `sim` profile or in a component's `tevv.yaml`, and neither is specified for it.
- **Content addressing.** `environment` names a pack by id, version **and
  `artifact_digest`**, which is what makes a run reproducible against a
  specific cook. `world: xfs` names a string.
- **The estimator's calibration.** `extensions.mns.vio_estimator.config_dir`
  points at the frozen `estimator_config.yaml` and kalibr chains that travel
  with the spec. omega's `stack[].params` is a flat map.

None of that is a criticism of omega — it was written for planner and
navigation suites, where the rig is fixed and the world is a preset. It does
mean a VIO campaign cannot be expressed as an omega file today without
extending `scenario`.

### Smaller gaps

- **`repeats` has no home.** `CampaignSpec.repeats: 3` is repetition without
  variation. omega's `matrix` is `mode: product` over explicit lists or
  `mode: sample` Monte Carlo; neither expresses "run this same point N times",
  which is how you separate estimator noise from a real regression.
- **No suite-level evaluator.** The baseline text promises *"pluggable
  evaluator containers (declared per suite / per repo tevv.yaml)"*, but
  `evaluation` is `additionalProperties: false` with `gates` as its only
  property. `CampaignSpec.evaluation.evaluator: vio-stress` has nowhere to go.
- **`verifies` needs a requirements register.** The baseline defers *"the
  requirements source of truth"* to a later phase, so `verifies: [REQ-NAV-012]`
  has nothing to resolve against yet.

## What already matches, exactly

The OSMO renderer table in the variant spec prescribes, row by row, what
`tevv-compile --target osmo` should emit. `osmo/sim-bridge-vio.workflow.yaml`
was written by hand against a live cluster, before that table was read, and it
landed on the same answers:

| Spec row | What the hand-written workflow does |
| --- | --- |
| one workflow per matrix point, name = `run_id` | one workflow per scenario; `--set stack=` selects it |
| one `group`, every pod a `task`, conductor `lead: true` | one group, five tasks, `score` is the lead |
| `ignoreNonleadStatus: false` — a restarted task corrupts a mid-run gang | set, with the same reasoning in a comment |
| `fastdds-ds`: a discovery-server task, participants get `ROS_DISCOVERY_SERVER` from `{{host:<task>}}` | exactly this, plus the resolution to an address that Fast DDS actually requires |
| no per-run ClusterIP Service — `{{host:}}` replaces it, the TCP dial-wait stays | both |
| config injected via task `files:` | both, though large config comes from a host mount instead |
| verdict via the lead's exit code and `exitActions` | `0` / `1` / `42` / `137` |

That convergence is the useful result. The workflow in this repository is, in
effect, a hand-rendered instance of what the compiler is specified to emit — so
the path forward is to teach a compiler to produce it, not to rethink the
topology.

One row does *not* match: the spec expects suite, tier, PR and SHA to live in
a **registry row**, with the compiler injecting `{{workflow_id}}` so the
conductor can write `workflow_ref` back. There is no registry here, and the
6.3.1 control plane rejects `workflow.labels` outright, so today that metadata
has nowhere to live at all.

## Can CampaignSpec fulfil omega's role?

Yes, and it is the better base — because of which direction the gaps run.

The two documents sit at the same level and disagree about scope.
`CampaignSpec` **delegates**: the world is `scenario: ./ScenarioSpec.yaml`, the
verdict is `evaluation.evaluator: vio-stress`. omega **inlines**: a three-field
world, and gates written out in the document. Composition-by-reference against
one flat file.

| Concern | CampaignSpec | omega.yaml |
| --- | --- | --- |
| the world and the rig | a reference to a ScenarioSpec | `{world, drones, origin}`, closed |
| sweep | `variants[]` with structured `overrides:` | `matrix:` axis→list, or `mode: sample` |
| repetition | `repeats: 3` | — |
| evaluator | `evaluation.evaluator` + `inputs` | — (`gates` only) |
| pass/fail | the evaluator decides | `evaluation.gates`, explicit |
| component under test | ScenarioSpec `extensions` | `stack[]`, `ref: pinned@sha256:` |
| classification | — | `tier`, `verifies: [REQ-…]` |
| interchangeability | implied by `runtime.profile` | `platform.{sim,autopilot,middleware,comms}` |
| faults | — | `faults[]` timeline |

What CampaignSpec is missing — `tier`, `verifies`, the `platform` axes,
`faults`, explicit `gates` — are **additive keys**. Nothing structural changes.

What omega is missing — a sensor rig, content-addressed packs, an evaluator
declaration, `repeats` — requires **opening a closed schema**: `scenario` is
`additionalProperties: false` and `origin` is pinned to `^preset:[a-z0-9-]+$`.

Adding five keys to `CampaignSpec` is a smaller change than teaching omega to
carry a stereo rig, and the `scenario:` indirection that omega lacks is
precisely the thing a rig needs. The cost is ownership: omega is the platform's
contract, so extending CampaignSpec instead means carrying a fork unless the
divergence is agreed first. That is a conversation, not an engineering problem.

## If this is pursued

The order that avoids wasted work:

1. **Decide whether omega replaces `CampaignSpec` or sits above it.** The specs
   are silent — they never mention ScenarioSpec or CampaignSpec, so this is an
   unwritten decision rather than a retrievable fact. omega's `suite` + `matrix`
   occupies the level `CampaignSpec` occupies, and carries gates, tier and
   `verifies` on top, which argues for superset. But it cannot express a rig,
   which argues against replacement.
2. **Extend `scenario`, or agree the rig lives elsewhere.** Everything else is
   mechanical; this one is a contract change and blocks any VIO suite.
3. **Write `omega.schema.json`.** It does not exist. Until it does, every
   consumer is guessing, and the plan's own examples already contain fields the
   plan's own schema forbids.
4. **Then render.** A `--target osmo` renderer has a working reference to match:
   `osmo/sim-bridge-vio.workflow.yaml` and [the runbook](osmo-runbook.md).

## Known contradictions in the contract

Worth carrying, because they will bite whoever implements first. Each is a
disagreement inside the specs themselves, not a disagreement with this
repository:

- `scenario.origin.yaw_deg` is the axis the flagship Monte Carlo example
  sweeps, but `scenario.origin` is a `preset:` string and `scenario` forbids
  additional properties. Nothing validates matrix axis paths against the
  document, so it passes CI and fails at compile time.
- **Matrix axis path syntax is unspecified.** `local-planner.params.max_vel`,
  `faults[2].params.drop_pct` and `faults[0].params.vector_ms[0]` mix component
  lookup, dotted paths and array indices, with no grammar given.
- **`verifies` cannot appear in `l0_tests`** per the schema, but the Jenkins
  spec puts it there — so there is no path from a component test to a
  requirement id.
- **Three incompatible `tevv.yaml` shapes** exist across the plan, the Jenkins
  spec, and the bridge's real file.
- **`custom` sim profile** is documented in prose and absent from the enum.
- **`fault_capabilities`** is load-bearing for compile step 4 but is declared by
  *profiles*, and profiles have no schema anywhere.
- **omega has no `schema_version`**, while `ci.json` and the bridge's
  `tevv.yaml` both do.
