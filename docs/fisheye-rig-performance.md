# Fisheye surround rig: tiled vs shared cubemap

What a four-camera 190° fisheye surround rig costs the v1 runtime host, why the default
(tiled) path is so slow, and how to set the shared-cubemap path up for VIO. Measured
2026-09-25.

## Setup

| | |
| --- | --- |
| Rig | 4 × fisheye, 190°, 512×512, yaw 0/90/180/270, 0.15 m above the body origin (`scenarios/fisheye-surround-shm`) |
| Paths | **tiled**: AirLib default, 4 off-axis tiles per camera; **shared**: one 6-face cube rig for the group (`fisheye_shared_cubemap: true`, group `surround`) |
| Host | `tevv-runtime-host-v1.0.0` (TEVV-Airsim `663cfee`), shell and generator v1.0.1, RTX 5080 |
| World | XFS 1.0.1, container yard, ArduPilot (SteppableClock), yard-box route (≈73 s, ≈50 m, up to 4.1 m) |
| Transport | iceoryx2 shared memory, compose stacks with `ipc: host`. On OSMO the sim and bridge are separate pods, so this rig only rides RPC there. |
| Display | windowed 1920×1080 viewport. Every variant pays for it, so differences between variants are valid but absolute numbers are not comparable with headless OSMO. |

**Real frame rate** means distinct capture stamps per second on `/fisheye_front/image_raw`
(`tools/fisheye_probe/hashcount.py`). Both paths re-publish the last frame on a timer with its
old stamp, so the delivered message rate overstates what the camera saw. A pixel-hash count
agrees with the stamp count in flight. Parked, it undercounts, because a fresh capture of a
static scene can be pixel-identical.

## Results

### Defaults, flying

| Path | Engine fps | GPU ms/frame | Delivered / camera | **Real / camera** | GPU W | Sim RTF |
| --- | --- | --- | --- | --- | --- | --- |
| baseline (no cameras, parked) | 130 | 7.3 | – | – | ~200 | 1.000 |
| tiled | 1.5 | 680 | 3.4 Hz | **0.37 Hz** | 257 | 1.000 |
| shared | 40–61 | 14–22 | 24–30 Hz | **7.7 Hz** | 133–204 | 1.000 |

Physics, flight control and the sim clock keep real time in every case (RTF 1.000). Render cost
shows only as engine fps and camera rate. The drone flies the route equally well at 1.5 fps.

### Why tiled is slow: Lumen

One tiled frame under `ProfileGPU` took 863 ms. Each fisheye capture cost ≈210 ms, of which
≈206 ms was `LumenSceneUpdate: 300 card captures`. The main 1080p viewport cost 10.5 ms. All
four cameras rendered in the same frame even though `FisheyeRenderArbiter MaxPerFrame=1`.
Every capture view re-runs Lumen's surface-cache card captures at the full default budget
(`r.LumenScene.SurfaceCache.CardCapturesPerFrame` = 300).

### Lever: cap Lumen card captures

`r.LumenScene.SurfaceCache.CardCapturesPerFrame`, set live:

| Path | Cards | Engine fps | GPU ms | Real / camera | Notes |
| --- | --- | --- | --- | --- | --- |
| tiled, parked | 300 | 1.0 | 950 | – | Lumen 738 ms of an 830 ms frame |
| tiled, parked | 64 | 3.5 | 288 | – | |
| tiled, parked | 16 | 6.9 | 142 | – | Lumen 44 ms of a 155 ms frame |
| tiled, parked | 4 | 9.4 | 105 | – | diminishing: the tiles themselves remain |
| **tiled, flying** | **16** | **13.4** | – | **3.35 Hz** | = fps ÷ 4, round-robin |
| **shared, flying** | **16** | **52** | 14 | **8.4 Hz** | |

Parked frames at 300 and 16 cards are visually identical (mean luminance 66.4 against 66.3).
The expected cost is lighting that lags briefly when the view changes fast, because the
surface cache refreshes more slowly. It hasn't been checked frame by frame in flight yet.

### Lever: match publish to capture (does not work as is)

Publishing runs on a free-running cadence timer (`UFisheye6CamCaptureComponent`,
`bCadencePublishEnabled` hardcoded `true`), not on capture completion. With the shared burst at
10 Hz and `r.Fisheye.PublishFPS 10`: **9.98 Hz delivered, 6.6 Hz real (34% repeats), against
8.4 Hz real at publish 30.** The timer drifts against the burst, so it both repeats and drops
real frames. Keep publish at 30, or higher, and drop same-stamp frames downstream, or publish on
burst completion in the plugin.

### 30 Hz capture and publish

`r.Fisheye.SyncCaptureFPS 30` makes every fisheye in the stack capture together at 30 Hz. For
tiled that replaces round-robin; for shared it replaces the implied 10 Hz. Publish stays at 30,
cards at 16, flying:

| Path | Engine fps | Delivered / camera | **Real / camera** | |
| --- | --- | --- | --- | --- |
| tiled | 3.6 | 7.3 Hz | **3.6 Hz** | GPU-bound: 16 tile renders per frame. 30 Hz tiled needs ~8× cheaper tiles. |
| **shared** | **30.6** | 29.8 Hz | **24.7 Hz** | 83% of delivered frames are real. The engine frame locks to the burst interval (TEVV-Airsim #174). |

Parked, with the settings applied at startup rather than live, shared delivered 30 Hz with
18.9 Hz distinct at 33 engine fps. **30 Hz is reachable with the shared cubemap only**, and
lands at about 19–25 real frames per second per camera.

## At 1000×1000: replicating DSTA's test

Four 1000×1000 fisheyes at 30 Hz (120 fisheye fps needed), XFS, flying. DSTA measured about
25 fisheye fps (0.19×) on an RTX 4090, at 22.7/24 GB VRAM.

| Setup (RTX 5080, 16 GB) | Engine fps | Fisheye fps (all 4) | × of 120 | GPU ms per fisheye frame | VRAM peak |
| --- | --- | --- | --- | --- | --- |
| tiled, defaults | 1.4 | 1.4 | 0.012 | ~205 | 15.5 GiB |
| tiled, cards 16, 30 Hz sync | 3.8 | 15.2 | 0.13 | ~74 | 15.5 GiB |
| shared, cards 16, 30 Hz burst (3 runs) | 21.9–22.1 | 83–87 | 0.70–0.73 | ~9 | 7.8 GiB |

- Resolution barely matters for tiled (1.4 fps at 1000² against 1.5 at 512²). Lumen card captures
  dominate.
- A single 1920×1080 pinhole renders five capture passes per frame. That's 1,217 ms per frame
  uncapped and 120 ms with cards 16, not the ~5 ms DSTA measured.
- Sim RTF stays 1.000; DSTA's sim ran at 0.13×.

## Exposure: HDR bloom ran before exposure

The washed-out fisheye image comes from HDR bloom running in the wrong units. It is not specific
to the shared path; the tiled path has the same bug. #201 first read it as "exposure ignored",
but EV does apply (EV −12 darkens the shared image to 48).

The face and tile captures hold absolute scene luminance (`SCS_SceneColorHDR`). The ISP applies
`0.0001 × 2^EV` last, but HDR bloom and veiling glare ran before it, on the raw values:

- Bloom's threshold (1.0) is meant for exposed values, so every raw pixel passed it.
- The bloom term `hdr × bright × (lum − threshold) × intensity` then grew with luminance squared.
- The frame sat about 12 stops hot on the Reinhard shoulder. EV 0 to 9 barely moved it, and
  auto-exposure sat on its MinEV −2 clamp for every target.
- Veiling glare, added in raw units, did nothing.

[TEVV-Airsim #202](https://github.com/DinoHub/TEVV-Airsim/pull/202) applies exposure first.
Front camera, parked in the XFS yard, 1000×1000. Circle mean is 0–255 inside 0.95 of the image
circle; clip is the share of pixels at or above 250:

| setting | shared, before | shared, #202 | tiled, before | tiled, #202 |
|---|---|---|---|---|
| EV −3 | 147.2 (5.9% clip) | 18.8 | 94.1 | 17.6 |
| EV 0 | 161.4 (8.4%) | 39.4 | 94.0 | 32.0 |
| EV 4 | 165.9 (10.4%) | 117.9 (4.1%) | 98.9 | 80.9 |
| EV 9 | 166.5 (10.8%) | 166.1 (10.5%) | 105.5 | 97.8 |
| auto-exposure target 0.45 / 0.30 / 0.20 | EV −2 for all three, 153.8 | EV +2.1 / +0.7 / −0.6, 78 / 50 / 33 | EV −2 for all three, 90.8 | EV +5.2 / +1.6 / +0.1, 86 / 50 / 33 |

What changes for users:
- Manual EVs and SceneTypes that were tuned against the washed-out image now look different.
  The XFS yard is mid-grey at EV 4–5, and `SceneType: outdoor` (EV 9) is close to the old look.
- Auto-exposure, the default when no exposure key is set, needs no retuning.
- Workaround on older runtime-host images: `HDRBloomEnabled: false` and a manual EV of about 4.

Face renders still strip shadows, fog and atmosphere (`StripShowFlags`). That is a separate
fidelity gap, not part of the exposure bug.

## Lens model and CameraInfo

The shader lens math is correct. Kannala-Brandt runs with a Newton inverse, Double Sphere
matches Usenko 2018 (eq. 40 and the closed-form inverse), and the resolve-map bake uses the
exact K-B series of the stereographic and equisolid lenses (0.14% off at θ = 95°). Where it goes
wrong is keeping the published calibration consistent with what is rendered:

- **CameraInfo reports neither the focal length nor the distortion that is rendered.** Every
  fisheye topic in these runs publishes `kannala_brandt`, `d = [0.03, 0, 0, 0]`,
  `fx = fy = 301.56`. The running sim (`simGetFisheyeCameraInfo`) renders **fx = 426.47** with
  k = 0 (pure equidistant):
  - `FisheyeLensType` defaults to Diagonal, so θmax = 95° lands on the image corners
    (f = half diagonal / θmax). The bridge assumed a circular lens (f = (W/2)/(fov/2)).
  - So the published focal length is 41% off. Inside the visible circle the camera sees about
    ±67°, not ±95°: the cosmetic lens mask blacks out the corners that hold the rest of the
    190°. `FisheyeLensType: Circular` puts all 190° inside the circle.
  - The 0.03 is `FisheyePolynomialK`, a default that only the legacy 6-cam shader applies, and
    that the SHM bridge folds into d[0].
  - A consumer that trusts CameraInfo is about 8% off in angle at the image rim (θ = 95°).
  - Even on the 6-cam path the fold is only first-order, and it has the wrong sign. The shader
    maps pixel angle θd to ray angle θd(1 + Kθd²), which is K-B with k1 ≈ −K.
- **The three paths render different lenses from the same settings.**
  - Only the 6-cam path applies `FisheyePolynomialK` and the optional barrel distortion.
  - Only the tiled and shared bake applies `FisheyeK` to an equidistant lens.
- **Automatic focal length assumes equidistant.** Without `FisheyeFx`/`FisheyeFy`,
  `PIPCamera.cpp` uses `f = r / θmax` for every lens. For stereographic, equisolid or K-B lenses
  with non-zero k, the image circle then doesn't match the image size. The SHM bridge falls back
  to `f = width / 2` for K-B and Double Sphere, which doesn't match the sim either.
- **"plumb_bob" is published for a zero-distortion equidistant lens.** A zero-distortion
  plumb_bob is a pinhole camera, not equidistant. It isn't hit today only because K = 0.03 is on
  by default.

Fixed in [TEVV-Airsim #206](https://github.com/DinoHub/TEVV-Airsim/pull/206) plus
[bridge #76](https://github.com/DinoHub/TEVV-Airsim-ROS2-Bridge/pull/76). The sim renders and
reports one lens: `FisheyePolynomialK` defaults to 0, barrel distortion is opt-in, the 6-cam
path uses K-B like the bake, and the automatic focal length follows the model. The bridge
publishes the sim's runtime calibration, so it now reads `kannala_brandt`, d = [0, 0, 0, 0],
fx = 426.47, which matches the sim. `gen_vio_calib.py` mirrors the sim's focal rule. Until
those land, calibrate the default 190° 1000×1000 shared/tiled camera as equidistant with
**f = 426.47, D = 0**, not from the published CameraInfo.

## Lens rain was on in every XFS run

Fisheye lens rain is `max(UDW rain / 10 × r.Fisheye.LensRainWeatherScale, the degradation
zone's cam_rain_intensity)`. The XFS level's weather actor loads `Partly_Cloudy` (dry) but
carries an inert manual `Rain = 6.0`, with `Rain - Manual Override` off. The runtime host read
that manual value regardless, so **every XFS fisheye frame in this document has lens drops at
0.6**, including the exposure and tone measurements. The drops cover a small area; feature
trackers see them.

- Fix: [TEVV-Airsim #205](https://github.com/DinoHub/TEVV-Airsim/pull/205) reads UDW's
  effective rain (the manual value only while overridden; otherwise UDW's own
  `Get Local Weather State Values`). Verified: dry = no drops, `simSetWeatherParameter` rain
  0.8 = drops, 0 = none.
- Stopgap on current hosts: `r.Fisheye.LensRainWeatherScale: 0` in
  `conditions.render.system_settings`. Both fisheye scenarios here now set it.
- Intensity is linear in the weather rain. Drop density goes from 12% to 34% of cells, tails
  from 2.2 to 5.2 radii, refraction from 0.46° to 1.4°. There is no wetting/drying over time
  and no dependence on camera orientation, airspeed or shelter.

## Rolling shutter and motion blur

[TEVV-Airsim #207](https://github.com/DinoHub/TEVV-Airsim/pull/207) adds both to the tiled and
shared paths. They are rotation-only, from the camera body rate estimated from pose changes, and
off by default: `FisheyeRollingShutterReadoutMs`, `FisheyeExposureTimeMs`,
`FisheyeMotionBlurMaxTaps`, or live `r.Fisheye.RollingShutterReadoutMs` /
`r.Fisheye.ExposureTimeMs`. Checked against a numpy model of the warp on a static camera with
an injected 2 rad/s rate:

| render | vs global shutter | vs model | vs model with −ω |
|---|---|---|---|
| rolling shutter 30 ms, yaw | 6.38 | **3.62** | 7.57 |
| rolling shutter 30 ms, pitch | 6.28 | **3.30** | 7.54 |
| exposure 15 ms, yaw | 5.80 | **3.03** | 3.03 |

Values are mean absolute error in grey levels; the noise floor is 3.6–4.3. With a live spin at
2 rad/s, the estimator logs 114–116 °/s and the ground-band gradient energy falls from 8.6 to 2.0
at 15 ms exposure. Translation is not modelled. Typical cheap CMOS fisheye values: readout
15–30 ms, exposure 1–20 ms.

## VIO: OpenVINS on the shared rig

One recorded flight (XFS yard, 98 s, 50 m path; 4 × 190° at 512×512, shared cubemap, Circular
lens, anchor at z −0.35 m, 30 Hz synchronized capture, about 28 Hz delivered) was replayed into
OpenVINS and scored with the tevv_ws metrics collector (ATE after alignment). The calibration
came from the bridge's `gen_vio_calib.py` (bridge #76: anchor extrinsics, fx 154.40). Masks
cover the airframe (about 22% of each circle) plus everything past θ = 85°, where OpenVINS's
equidistant unprojection breaks. Three replays per config:

| config | cameras | ATE runs (m) | median ATE | median RPE (m/1 s) |
|---|---|---|---|---|
| campaign-v1 (frozen) | front | 4.08, 9.06, 4.12 | 4.12 | 0.74 |
| campaign-v1 | front + back | 10.49, 8.96, 2.55 | 8.96 | 1.20 |
| campaign-v1 | all 4 | 2.78, 2.11, 2.80 | 2.78 | 0.48 |
| fisheye-tuned | front | 2.94, 6.37, 6.20 | 6.20 | 0.88 |
| fisheye-tuned | front + back | 1.39, 1.41, 1.58 | **1.41** | 0.36 |
| fisheye-tuned | all 4 | 1.66, 6.75, 5.89 | 5.89 | 0.82 |

"fisheye-tuned" is campaign-v1 plus `max_clones` 15, `max_slam` 40, `max_msckf_in_update` 60,
`fi_max_dist` 150 and `fi_max_baseline` 120.

- **It works, but not well yet.** Every config initialises and tracks. The best result is
  about 3% of the path length, against 0.13–0.64 m for the pinhole stereo campaign on the same
  level.
- **Why the frozen config struggles:** it averages about 2 features per MSCKF update. The yard's
  features are 20–100 m away, and the airframe hides the near ground. `fi_max_baseline` 40
  (a depth-to-baseline ratio) rejects most far points over a 0.4 s window. 512 px across 190° is
  also about 3× coarser per pixel than the campaign's pinhole.
- **Replays are not deterministic.** One config spans 2.6–10.5 m. Front + back tuned is the only
  config that is tight (1.39–1.58 m).
- **Four cameras don't beat two.** Stock OpenVINS treats more than two cameras as independent
  mono trackers, one update per image. In the 4-camera tuned runs no feature was promoted to
  SLAM. A multi-camera estimator (e.g. OpenVINS with a synchronized multi-camera update,
  Kimera-Multi, or MCVIO) is the real test of this rig.
- **Gotchas:**
  - OpenVINS divides `init_max_features` by the camera count. campaign-v1's 50 gives each of
    four cameras 12, the tracker never fills and initialisation fails silently. Use 50 × N.
  - The bag's image topics are best-effort. Replay with a reliable QoS override or OpenVINS
    receives nothing.
  - The ArduPilot profile turns off AirSim's own IMU (`DISABLE_RPC_AUX_SENSORS=true`) unless
    the scenario names a VIO estimator. A recording for offline VIO needs it set to false
    (200 Hz `/imu/data`).

## Do the default values make sense?

- **Tone curve: no display gamma.** The ISP writes `pow(x/(x+1), 0.85)` straight into 8-bit
  (linear render targets, and the format convert only clamps). A real camera applies an sRGB or
  BT.709 curve (about 1/2.2). Here an 18% grey at unit exposure encodes to 52 instead of about
  118, so shadows are darker and flatter than a real image. That is a sim-to-real gap for
  feature detectors tuned on real footage.
  [TEVV-Airsim #204](https://github.com/DinoHub/TEVV-Airsim/pull/204) makes ISP mode Reinhard
  plus the sRGB curve. A pixelwise check against a RAW frame matches it (median error −0.03
  levels), and 18% grey now lands at 109. The old curve stays as `TonemapMode: 3`.
- **Bloom threshold 1.0 is the mid-tone.** Even with #202, bloom reaches everything brighter
  than mid-grey (+10 levels at EV 4, clip 0.8% to 4.1%). Real lens scatter shows only near
  saturation, so a threshold of about 4–8 fits better. #204 sets the default to 4.0, and bloom
  now adds +1.6 levels at EV 4.
- **The SceneType table assumes physical light levels.** `outdoor` = EV 9 is about 4 stops too
  bright for the XFS yard, which is mid-grey at EV 4–5. Prefer auto-exposure.
- **Auto-exposure metering.** It meters mean max(R,G,B) over 0.7 of the circle radius, so target
  0.45 gives a luma circle mean of about 0.31 (78/255). That is reasonable for sky-heavy frames.
  The MinEV −2 floor never binds after #202.
- **Veiling glare** with `VeilingGlareStrength` 0.25 now adds about +1.4 levels at EV 4 in sun.
  That is plausible.

## Other observations

- **Exposure differs between the paths.** For the same camera and pose, tiled renders
  darker than shared. Before #202 the means were 66 against 108; with #202 at EV 4, the circle
  means are 81 against 118, and tiled keeps about 5% of the circle crushed to black. The
  bloom bug hit both paths; this remaining gap between them is not explained yet.
- **The shared rig sees the airframe.** With `fisheye_shared_cubemap_offset_z: -0.15` the
  drone's arms and propellers fill the bottom third of each view. The group renders from its
  anchor, not from each camera's pose. At −0.35 m they shrink to a band at the bottom, and at
  −0.5 m to a sliver at the image circle's edge. They can't go away: a horizontal 190° fisheye
  sees 95° off-axis, past straight down, so the airframe is always in view. Mask it in the VIO
  front end. `scenarios/fisheye-surround-shm` uses −0.35 m.
- **No stereo from the shared rig.** Every camera in a group shares one optical centre, so
  there's no baseline. Scale has to come from the IMU (mono or multi-camera VIO). Stereo VIO
  needs separate optical centres, i.e. tiled, or the pinhole pair.
- TEVV-Airsim #174 (shared-cube synchronous `CaptureScene` stall) is still open, but its
  8–10 fps symptom didn't reproduce here.

## Recommendations

1. **Per scenario, done in `scenarios/fisheye-surround-shm`:**
   ```yaml
   # ScenarioSpec.yaml, every variant
   conditions:
     render:
       system_settings:
         r.LumenScene.SurfaceCache.CardCapturesPerFrame: 16
   ```
   ```yaml
   # CampaignSpec.yaml, shared variant overrides (tiled keeps round-robin)
   conditions:
     render:
       system_settings:
         r.Fisheye.SyncCaptureFPS: 30
   # and in each shared camera's capture_settings:
   #   fisheye_shared_cubemap_offset_z: -0.35
   ```
   The generator emits `system_settings` as `-ini:Engine:[SystemSettings]:<cvar>=<value>`,
   which survives scalability settings, and the sim log confirms `Set CVar` at startup. The
   `r.Fisheye.*` CVars are the workaround until the generator aliases in (2) land.
2. **Generator (MnS-Integration-Platform), [#114](https://github.com/DinoHub/MnS-Integration-Platform/pull/114):** alias `fisheye_sync_capture_fps`,
   `fisheye_publish_fps`, `fisheye_capture_fps` and `fisheye_max_cameras_per_frame`. Today they
   CamelCase onto the camera block and nothing reads them, the same gap platform #83 closed for
   the shared-cubemap keys.
3. **Bridge (TEVV-Airsim-ROS2-Bridge), [#75](https://github.com/DinoHub/TEVV-Airsim-ROS2-Bridge/pull/75):**
   drop an iceoryx sample whose capture stamp equals the previous one, and publish each bundled
   IMU stamp once. Checked live with the shared group at 30 Hz: 30.0 Hz delivered with 18.9
   distinct before; 18.03 delivered = 18.03 distinct after, with `repeats_dropped` in
   `/diagnostics`.
4. **Runtime host (TEVV-Airsim), [issue #200](https://github.com/DinoHub/TEVV-Airsim/issues/200):**
   publish a shared group on burst completion instead of on a timer.
5. **Runtime host (TEVV-Airsim), [PR #202](https://github.com/DinoHub/TEVV-Airsim/pull/202)
   (fixes #201):** apply exposure before HDR bloom and veiling glare.
6. **Bridge SHM CameraInfo:** publish from `simGetFisheyeCameraInfo`, not from settings.json. See
   "Lens model and CameraInfo".
7. **Runtime host (TEVV-Airsim), [PR #203](https://github.com/DinoHub/TEVV-Airsim/pull/203)
   (stacked on #202):** lens-rain fixes. Beads are no longer sliced across the head, drawn on
   the masked rim, or clipped to white. Tails are no longer cut at the drop grid, drops no
   longer teleport, and the clock is world seconds instead of a per-dispatch counter. The rain
   rays also use the lens the resolve map was baked with.
8. **Runtime host (TEVV-Airsim), [PR #204](https://github.com/DinoHub/TEVV-Airsim/pull/204)
   (stacked on #203):** sRGB display curve in ISP mode (legacy curve as `TonemapMode: 3`) and a
   bloom threshold of 4.0.
9. **Runtime host, [PR #205](https://github.com/DinoHub/TEVV-Airsim/pull/205) (off main):**
   lens rain from UDW's effective rain. Until then `r.Fisheye.LensRainWeatherScale: 0`.
10. **Runtime host [#206](https://github.com/DinoHub/TEVV-Airsim/pull/206) + bridge
    [#76](https://github.com/DinoHub/TEVV-Airsim-ROS2-Bridge/pull/76):** one lens, published as
    rendered (fx 426.47, D = 0 for the default 190° camera).
11. **Runtime host, [PR #207](https://github.com/DinoHub/TEVV-Airsim/pull/207) (stacked on #206):**
    rolling shutter and motion blur.
12. **Lens type.** The scenarios set `fisheye_lens_type: Circular`, so the full 190° sits inside
    the image circle (f = 301.6 px at 1000×1000). That also works on older hosts.
    [TEVV-Airsim #209](https://github.com/DinoHub/TEVV-Airsim/pull/209) makes Circular the
    runtime default, the usual VIO fisheye. Diagonal stays available for real diagonal fisheyes.
13. **Runtime host, [PR #208](https://github.com/DinoHub/TEVV-Airsim/pull/208) (stacked on #207):**
    rim effects are measured on the lens's image circle, a >180° Diagonal lens logs a warning,
    and the lens mask is off by default. The mask was redundant: the image already ends at the
    circle (level 0.0 outside), its ring isn't physical, and it cost 8% of the rim corners.
14. **Runtime host, [PR #209](https://github.com/DinoHub/TEVV-Airsim/pull/209) (stacked on #208):**
    one relative-illumination curve over the field angle θ/θmax, applied in linear light,
    replaces the two vignettes. Default 0.7 at the edge; a datasheet curve can be given with
    `FisheyeRelativeIlluminationCurve`. It matches its model to 0.001 in RAW. FAST corners in
    the outer quarter of the circle go from 352 to 1317 (1370 with no falloff at all). The old
    vignette keys and the lens mask are deprecated and log warnings.
15. **Runtime host, [PR #210](https://github.com/DinoHub/TEVV-Airsim/pull/210) (stacked on #209):**
    lens-flare ghosts from the scene's sun, gated on the sun disc being visible in the image. On
    XFS the sky preset keeps the sun behind cloud, so no ghosts appear by default.
16. **Recording for offline VIO on ArduPilot:** set `DISABLE_RPC_AUX_SENSORS=false` in the stack
    `.env`, or the bag has no IMU. The generator should turn it on for any SHM fisheye scenario,
    not only when a VIO estimator is named.

## Bugs found on the way

| # | Where | Symptom | Workaround |
| --- | --- | --- | --- |
| 1 | product shell, `campaign run` | run dirs created as root; the generator (host uid) gets `PermissionError` | pre-create the run dirs as the user |
| 2 | product shell, `campaign run` | compose gets container `/workspace/...` paths; the host daemon mounts empty stubs; the sim sits on `/Engine/Maps/Entry` ("environment failure") | generate with `product.sh cli runtime --no-run`, run compose from the host |
| 3 | stackgen networking | `sim` subnet hardcoded in `172.30.x.0/24`; a sibling auto-pool network takes `172.30.0.0/16` ("Pool overlaps") | guard network on `172.30.0.0/24` |
| 4 | iceoryx2 teardown | stale nodes and services in `/tmp/iceoryx2` and `/dev/shm/iox2_*`; the next sim can't create publishers (`error=1`) | clean both between stacks |
| 5 | product shell pilot | `fly_mission_mavros.py` in the shell has no ArduPilot takeoff; ArduPilot arms, never climbs, and disarms | use this repo's `osmo/files/fly_mission_mavros.py`; the fix is in [MnS-Integration-Platform#109](https://github.com/DinoHub/MnS-Integration-Platform/pull/109) |

## Reproducing

Stacks come from `scenarios/fisheye-surround-shm` (variants `baseline`, `tiled`, `shared`).
They're generated with `./product.sh cli runtime --no-run` using the v1.0.1 images exported as
`MNS_PRODUCT_SHELL_IMAGE` / `MNS_STACK_GENERATOR_IMAGE`. They're driven with host
`docker compose`, flown with `osmo/files/fly_mission_mavros.py` inside the bridge container, and
measured with `tools/fisheye_probe/`. Raw data from these runs is under
`generated/campaigns/fisheye-surround-shm-{host,fly,fly2,fly3}/` and
`fisheye-lever-test/` on the measurement host.
