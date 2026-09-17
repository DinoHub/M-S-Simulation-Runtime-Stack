# Reference VIO campaign

Twelve flights over the XFS level — four wind strengths, three repeats each —
recorded, checked and scored. It is here to be copied.

```bash
./product.sh cli campaign run vio-reference
```

Or, with the platform on PATH, `tevv-campaign run vio-reference`.

## What it is for

Two things at once. It is a worked example of every part of a campaign, and it
is a rig that already flies, so a new estimator can be tested by changing one
block rather than by assembling a scenario from nothing.

```bash
tevv-campaign init my-test --from vio-reference
```

That copies the whole directory and rewrites the identity. The copy runs
unedited, so `tevv-campaign validate my-test` is useful before you change
anything.

## Testing your own estimator

One block in `ScenarioSpec.yaml`:

```yaml
extensions:
  mns.vio_estimator:
    enabled: true
    kind: custom              # openvins is a preset; custom means you say
    package: my_estimator     # your ROS 2 package
    launch_file: run.launch.py
    config_arg: params_file   # what your launch file calls the config
    config_dir: ./my-config   # staged to /cfg/vio, read-only
    odom_topic: /my_estimator/odom
```

And the image behind that package, in an image set beside the campaign:

```yaml
# my-images.yaml
image_sets:
  ue582:
    images:
      vio_estimator: my-registry/my-estimator:v1
```

Then `tevv-campaign run my-test --image-set-file scenarios/my-test/my-images.yaml`.

Two things are checked before anything flies, and both are worth knowing about:

- your calibration against the rig the scenario declares — the mount offsets and
  focal lengths have to agree, or the estimator is solving for a camera that is
  not there
- the topics your estimator subscribes to, against the ones the stack publishes

## What is in here

```
ScenarioSpec.yaml    the world, the vehicle, its sensors, the estimator
CampaignSpec.yaml    the variants, the route, what is recorded and scored
routes/              the flight, as time-indexed waypoints
openvins/            the frozen estimator calibration
```

Nothing pins an image. Every role — runtime host, bridge, autopilot, estimator —
comes from the channel's image set in `images/catalog.yaml`, so this campaign
runs against exactly what `./product.sh setup` installed.

## Reading the results

```bash
tevv-campaign status vio-reference
```

One row per flight. The `valid` column is the recording's own verdict and sits
left of the accuracy columns deliberately: a number computed from a recording
that failed its gates is worse than no number. A run marked `NO` names the gates
it failed underneath.
