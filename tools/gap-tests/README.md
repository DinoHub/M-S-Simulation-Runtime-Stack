# Sim-to-real gap tests

Flights for the test cases in the Sim-to-Real Validation Plan, on the 4 × 190° fisheye rig
with OpenVINS: flying into a low sun, dusk, fog and rain, the city street with distant
features. One command generates the stack from a committed ScenarioSpec, flies a route under
one condition and records the bag, with OpenVINS running live in the stack. A second scores
the live estimate against ground truth.

```bash
make gap-flight GAP=gap-fisheye-xfs OUT=runs/gap-tests/low-sun TIME=16 WEATHER=clear
make gap-score  OUT=runs/gap-tests/low-sun                  # live estimate: ATE, RPE, landmarks per update
make gap-replay OUT=runs/gap-tests/low-sun CONFIG=t2zc      # re-run another OpenVINS config on the same bag
```

| Scenario | World | Route | Use it for |
| --- | --- | --- | --- |
| `gap-fisheye-xfs` | XFS yard, sun real for 28 Sep 2026 | `xfs_yard_box`, 50 m, 70 s | baseline, low sun, dusk, night, weather |
| `gap-fisheye-safti` | SAFTI city streets, Singapore | `safti_city_streets_v1`, 223 m | urban street with distant features |

## Commands

`tools/gap-tests/gap.sh` does the work; the Make targets wrap it.

| Command | What it does |
| --- | --- |
| `gap.sh stack SCENARIO` | Generates `generated/gap-tests/SCENARIO/stack` and applies `overlay.json` |
| `gap.sh up SCENARIO` / `down SCENARIO` | Starts the stack and waits for the cameras / stops it |
| `gap.sh fly SCENARIO OUT [--time H] [--weather W] [--flare on\|off] [--veil on\|off] [--route F] [--chase]` | Fresh stack, sets the condition, snapshots the cameras, records the bag while the route is flown, stops the stack |
| `gap.sh score OUT [TAG]` | Scores the estimate OpenVINS published live during the flight (recorded as `/ov_msckf/odomimu`) |
| `gap.sh replay OUT TAG [--config t2zc\|t2] [--dump]` | Re-runs OpenVINS on the bag's images, scored; `--dump` also saves the track-history frames for a video |
| `gap.sh video OUT TAG OUT.mp4` | Chase view + feature tracks + top-down + error plot (needs `--chase` on the flight) |
| `gap.sh cases SCENARIO CASES OUT` | Every line of a cases file (`tag time weather flare veil`), flown and replayed `REPLAYS` times |
| `gap.sh sweep ...` | `lighting_sweep.py`: image sweeps over time of day and weather on a running stack |

`--time` is local solar time at the site. `--weather` is `level` (the level's own weather),
`clear`, `haze`, `fog`, `rain` or `dust`. Only one flight runs at a time: it holds the GPU,
the simulator ports and the X display.

## What each scenario directory holds

- `ScenarioSpec.yaml`: world, origin, rig, sensors, render settings. The generator reads it.
- `overlay.json`: what the spec cannot say yet, applied after generation. Today that is the
  runtime images (below), the real-sun keys of TEVV-Airsim #216 and one bridge switch. Each
  entry says why. When the generator learns a key, move it into the spec.
- `routes/*.b64`: the route the pilot flies.
- `openvins/t2/` (XFS) and `openvins/t2zc/`: estimator configs. `replay` uses `t2` where the
  scenario has it, else `t2zc`. `t2` is the XFS campaign config; it diverges in the city
  because its zero-velocity update switches off after start-up. `t2zc` fixes the city but
  scored 7–8 m on the XFS yard, so no single config is frozen yet.

## Measured so far (XFS yard route, sun 09:00, 29 Sep 2026)

| Flight | Stack | `t2` ATE | `t2zc` ATE | Landmarks/update (`t2`) |
| --- | --- | --- | --- | --- |
| 1 | `gap.sh` | 2.06 m (3 replays 2.06–2.18) | 6.99 m (6.93–6.99) | 12.8 |
| 2 | `gap.sh` | 1.35 m | 7.81 m | 12.4 |
| 3 | hand-built stack, old scripts | 4.71 m | – | 11.6 |

Live OpenVINS in the stack, same route (29 Sep 2026), each bag also replayed with `t2`:

| Flight | Live ATE | Replay ATE |
| --- | --- | --- |
| live-2 | 397 m (diverged) | 0.94 m |
| live-3 | 0.76 m | 0.54 m |
| live-4 | 10.19 m | 1.35 m |

Live is worse than a replay of its own bag in every flight so far. Part of that is an
OpenVINS failure mode that also shows up in replays: the live launch command replayed on
the live-2 bag gave 1689, 0.92 and 1.23 m. Treat live and replay scores as different
measurements and report which one a number is.

Two flights on 28 Sep scored 0.64 and 0.89 m. Flight-to-flight spread on this route is far
wider than those two suggested (0.64–4.71 m over five flights), so compare conditions on
several flights each, not one.

## Dependencies not yet in a release

| Needed | Why | Where it comes from |
| --- | --- | --- |
| `dhdevspace/auto_mns:tevv-runtime-host-v1.0.0-bloomfix.16` | Flare, veil, sensor model, real sun (TEVV-Airsim #210–#216, open) | Local build of the runtime host |
| `tevv-airsim-ros2-bridge:v1.0.0-shmfix.1` | Fisheye frames without repeats and the real lens in CameraInfo (bridge #75, #76, open) | `bridge-image/build.sh` |
| `dhdevspace/auto_mns:mns-stack-generator-v1.0.1` | Fisheye capture keys, SteppableClock for ArduPilot | Published; not yet the product pin |
| `tevv-metrics:humble` and `~/tevv_ws/metrics` | ATE/RPE scoring in `replay` | tevv_ws |
| SAFTI level pack 1.0.3 | The city scenario | `tools/pull-packs.sh --release-tag pack-level-safti-level-1.0.3` |

OpenVINS runs live in the stack (`extensions.mns.vio_estimator` in each spec; `use_stereo`
false, because the front and back fisheyes are two mono cameras). `fly` restarts it just
before recording so it starts on the parked segment. A flight scores once with `score`;
`replay` re-runs any config on the same bag, which is how configs are compared fairly.
