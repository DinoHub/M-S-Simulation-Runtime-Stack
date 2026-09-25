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

## Exposure on the shared path

No exposure setting changes the shared image (TEVV-Airsim #201). Circle mean for the front camera:
- auto-exposure target 0.45 / 0.30 / 0.20: 153.5 / 152.9 / 153.4;
- manual EV −3 / −2 / −1 / 0 / +9: 147.1 / 153.4 / 158.1 / 153.5 / 166.5.

Auto-exposure can't meter there, because its luminance measurement only runs on the tiled/6-cam
readback. Manual EV doesn't reach the published image either. Face renders also strip shadows,
fog and atmosphere, which gives the washed-out look.

## Other observations

- **Exposure differs between the paths.** For the same camera and pose, tiled renders the
  ground almost black under a blown-out sky, and shared renders the ground near-white. Mean
  luminance was 66 against 108.
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
5. **Runtime host (TEVV-Airsim), [issue #201](https://github.com/DinoHub/TEVV-Airsim/issues/201):**
   pass exposure (manual and auto) through to the shared-cubemap image.

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
