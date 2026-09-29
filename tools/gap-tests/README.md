# Sim-to-real gap tests

Flights for the test cases in the Sim-to-Real Validation Plan, on the 4 × 190° fisheye rig
with OpenVINS: flying into a low sun, dusk, fog and rain, the city street with distant
features. One command generates the stack from a committed ScenarioSpec, flies a route under
one condition and records the bag. A second replays OpenVINS on the bag and scores it against
ground truth.

```bash
make gap-flight GAP=gap-fisheye-xfs OUT=runs/gap-tests/low-sun TIME=16 WEATHER=clear
make gap-replay OUT=runs/gap-tests/low-sun                  # ATE, RPE, landmarks per update
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
| `gap.sh replay OUT TAG [--config t2zc\|t2] [--dump]` | OpenVINS on the bag, scored; `--dump` also saves the track-history frames for a video |
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

OpenVINS runs as a replay of the recorded bag, not live in the stack. Replays of one bag
are not deterministic, so score each flight three times (`gap.sh cases` does).
