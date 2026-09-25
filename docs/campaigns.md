# Campaigns: characterise an algorithm

A campaign is a run matrix over one scenario: the same stack flown many times with one
difference at a time, each flight recorded, gated and scored. The reference campaign ships
in this repository and is meant to be copied.

```bash
./product.sh setup            # once: images, including the estimator and the shell
make campaign                 # 12 flights: 4 wind strengths x 3 repeats, scored
make campaign-status          # one row per flight
```

`make campaign` runs the product shell's CLI, so it needs no platform checkout. Anything the
CLI accepts can go through it:

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

To test your own estimator, copy the reference campaign and change one block in its
`ScenarioSpec.yaml`. [`scenarios/vio-reference/README.md`](../scenarios/vio-reference/README.md) covers it, including the two things
checked before anything flies: your calibration against the rig the scenario declares, and
the topics your estimator subscribes to against the ones the stack publishes.

Reading the result: the `valid` column is the recording's own verdict and sits left of the
accuracy columns deliberately — a number computed from a recording that failed its gates is
worse than no number.

How a campaign run flows through validate, preflight, the generator, `validate_recording`
and `sim-real-eval`, and which files each step leaves, is in
[How it fits together](how-it-fits-together.md#campaigns-the-loop-n-times-scored).
