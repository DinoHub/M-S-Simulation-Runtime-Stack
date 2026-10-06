# Campaigns: characterise an algorithm

A campaign is a run matrix over one scenario: the same stack flown many times with one
difference at a time, each flight recorded, gated and scored. The reference campaign ships
in this repository and is meant to be copied.

```bash
./setup.sh                    # once: images, including the estimator and mns-stacks
make campaign                 # 12 flights: 4 wind strengths x 3 repeats, scored
make campaign-status          # one row per flight
```

Each wind variant sets both the visual wind (`wind`, what the cameras see) and
the physics wind (`wind_mps`, which AirSim applies to the vehicle as drag), so
the drone is pushed as well as the scene moving. See the note in
`scenarios/vio-reference/CampaignSpec.yaml`.

`make campaign` runs `mns-stacks campaign` from the pinned `mns-stacks` image (the same
command the dashboard's campaign page runs), so it needs no platform checkout. Every
`campaign` subcommand can go through it:

```bash
make campaign ARGS="validate vio-reference"   # spec, route and calibration; flies nothing
make campaign ARGS="preflight vio-reference"  # + disk, ports, images
make campaign ARGS="init my-test"             # your own copy of the reference campaign
make campaign CAMPAIGN=my-test                # fly yours
```

A campaign is hours long and holds the GPU, the simulator ports and the X display, so only
one runs at a time; a second is refused and told which one holds the machine. `make campaign
ARGS="cancel <name>"` stops one cleanly — the flight in the air is finished and bundled,
nothing further starts.

Campaign runs render the simulator off-screen. A campaign is unattended, and an
unattended desktop puts its monitor to sleep: a windowed simulator then stalls its cameras
for about half a second at each display switch, which fails the recording's image-gap check.
Drawing the window also costs GPU time (on XFS it holds the stereo cameras near 10 Hz). To
watch the runs anyway, keep the display awake and either pass `--window`
(`make campaign ARGS="run my-test --window"`) or set it in the CampaignSpec, which is what
the dashboard's **Show the simulator window during runs** writes:

```yaml
extensions:
  mns.render:
    offscreen: false
```

`--window` and `--offscreen` win over the file; the file wins over a
`conditions.render.offscreen` in the ScenarioSpec. `campaign validate` and `campaign plan`
say which applies.

Each flight is generated, flown and recorded exactly as `make fly SCENARIO=... RECORD=1`
flies one scenario (see [Headless](stacks.md)): the bag lands in the campaign's
`runs/<run_key>/bag`, recorded by the bridge container, in the layout the dashboard's
replay and `sim_real_eval` read.

To test your own estimator, copy the reference campaign and change one block in its
`ScenarioSpec.yaml`. [`scenarios/vio-reference/README.md`](../scenarios/vio-reference/README.md) covers it, including the two things
checked before anything flies: your calibration against the rig the scenario declares, and
the topics your estimator subscribes to against the ones the stack publishes.

Reading the result: the `valid` column is the recording's own verdict and sits left of the
accuracy columns deliberately — a number computed from a recording that failed its gates is
worse than no number.

## The same campaign on a cluster (OSMO)

`osmo/campaign.py` flies a CampaignSpec on NVIDIA OSMO instead of compose. It asks
`mns-stacks` (through `tools/mns-stacks.sh`) to plan the matrix and generate each run's
stack, then submits one workflow per run and pulls the evidence back into the same
`generated/campaigns/<id>/` layout, so `status` and the dashboard read it
unchanged. Four campaigns ship ready: `vio-osmo-condo` (PX4),
`vio-osmo-condo-ardupilot`, `vio-osmo-xfs` and `vio-osmo-xfs-ardupilot`.

```bash
osmo/campaign.py run    vio-osmo-condo                  # every run in the matrix
osmo/campaign.py run    vio-osmo-condo --only calm-r1 --viz
osmo/campaign.py watch  <workflow id>                   # Foxglove at ws://<GPU node IP>:30765
osmo/campaign.py status vio-osmo-condo
```

You edit two files per campaign, `scenarios/<campaign>/CampaignSpec.yaml` and
`ScenarioSpec.yaml`, plus the routes and estimator config beside them. Not every field
reaches an OSMO run, and some are silently ignored there (`recording.topics`, the
estimator's `launch_args`); images come from `images/catalog.yaml`, and
`osmo/campaign.py images` checks the GPU node holds them. The live view is
`runtime.features.foxglove_bridge` in the spec, or `--viz` / `--no-viz`. One pair has to be changed together:
`mission.autopilot` and `runtime.profile`.

- [Authoring for OSMO](osmo-runbook.md#authoring-for-osmo-what-you-edit-and-what-the-run-reads-from-it):
  every field, the generated files it becomes, and the whole folder tree.
- [OSMO runbook](osmo-runbook.md): setting up the cluster, running, watching, and what to do when a run fails.
- [OSMO and Kubernetes for this repo](osmo-kubernetes-concepts.md): the concepts to learn first.
- [One run, end to end](osmo-run-flow.md): what each step writes, where, and who reads it,
  from the CampaignSpec to the run registry and Grafana.
- [Logs on OSMO](osmo-logs.md): what Loki and Alloy are for, what they are not, and how to
  tell whether they are working.

How a campaign run flows through validate, preflight, `mns-stacks generate`, `validate_recording`
and `sim-real-eval`, and which files each step leaves, is in
[How it fits together](how-it-fits-together.md#campaigns-the-loop-n-times-scored).
