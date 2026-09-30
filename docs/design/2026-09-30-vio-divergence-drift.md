# Investigation: stereo VIO divergence that appeared after 25 Sep 2026

**Status: cause identified; one confirming experiment pending an idle GPU.** OpenVINS's
sliding window is a fixed 11 camera frames. At the ~25 Hz camera rate the PX4 pipeline
delivers on an idle host, that is about 0.44 s: too little parallax for the route's 40 °/s
corner, so the filter diverges there. At ~11 Hz the same 11 frames span about 1 s, and it
does not diverge.

The camera rate is not set anywhere. It is whatever the simulator and bridge manage, and
it halves when something else loads the GPU: another session's simulator did exactly that
during these experiments. That is the drift. On 25 Sep the host was busier, the cameras ran
slower, and the runs passed. Since then they run at full rate, and the runs diverge.

Related: [the OSMO/Argo benchmark](../../argo/README.md#benchmark-argo-against-osmo-30-sep-2026),
[migration decision log](2026-09-30-argo-migration-decisions.md) entries 21, 27 and 28.
Tools: [`tools/vio-drift/`](../../tools/vio-drift/README.md).

## The finding, in numbers

All runs are on the `vio-osmo-xfs` yard-box campaign, PX4, on Argo unless noted, 30 Sep.

| Condition | Cameras (bag) | Estimator updates | Runs | Passed (ATE ≤ 1 m) |
| --- | --- | --- | --- | --- |
| Idle GPU, default config | 23–27 Hz | ~13.5/s | 13 (compose, OSMO, Argo) | 2 |
| Another simulator on the GPU, default config | 10.8–11.0 Hz | ~7.1/s | 6 | 6 (0.30–1.13 m) |
| Another simulator on the GPU, bridge `poll_rate_hz` 12 | 10.8–10.9 Hz | ~7.4/s | 3 | 3 (0.42–0.74 m) |
| Another simulator on the GPU, `max_clones` 22 | 10.9–11.1 Hz | — | 3 | 3 (0.28–0.58 m) |

- **The contended rows come from GPU sharing, not from any setting of mine.** The six
  "default config" runs are the frame-dump runs. The dump was first taken for the cause,
  but they all started after another session's simulator had taken the same GPU (08:06
  UTC). Every run before that point had 23 Hz cameras and diverged; every run after it had
  11 Hz cameras and passed.
- **The same effect on identical images, with no load involved.** The 29 Sep compose
  recording (26.5 Hz cameras) replays deterministically. Only the window changes:

| Replay of the 29 Sep recording | Error at the end of the flight |
| --- | --- |
| Default: 11 clones at 26.5 Hz, about 0.42 s | 52.8 m |
| `track_frequency` 20.8 | 20.1 m |
| `track_frequency` 15 | 36.9 m |
| `track_frequency` 11 | 2.6 m |
| `max_clones` 22 | 1.7 m |

- **The history matches.**
  - The two good 25 Sep flights still on disk had 12.9–14.4 Hz and gappy cameras
    (wind-6-r1), or gappy delivery (calm-r2). The two bad ones had 27 Hz.
  - A 25 Sep sim log shows the engine at ~23 FPS: a loaded host.
  - The estimator config was tuned when the bridge delivered 20.8 Hz ("11 clones = 0.50 s
    measured window").
- **Still to confirm live:** a clean A/B on an *idle* GPU, with the bridge at 12 Hz and with
  `max_clones` 22, each against the default. The other simulator held the GPU for the rest
  of the session, and every run in that window came out at 11 Hz.

## Symptom

On the XFS yard-box route, stereo OpenVINS (pinhole 640×480, PX4, `vio-osmo-xfs` CampaignSpec,
v1 images) diverges at one point of the route:

- **Where:** the corner at ENU (−10, 6), 21–28 s after takeoff, where the vehicle yaws at
  about 40 °/s.
- **Effect:** the estimate's error goes from under 1 m to 3–10 m ATE RMSE (15–50 m by the end
  of the flight), and the campaign's 1.0 m gate fails.

| Period | Backend | Distinct flights | Good (ATE ≤ 1 m) |
| --- | --- | --- | --- |
| 25 Sep, 13:58–15:35 | OSMO | 9 | 7 (0.45–0.82 m) |
| 29 Sep, 21:00 | compose (vio-reference, same corner) | 1 | 0 |
| 30 Sep | OSMO | 3 | 0 |
| 30 Sep | Argo | 10 | 2 (0.54, 0.55 m) |

**How the 25 Sep row was counted.** Several registry attempts on 25 Sep carry the same ATE to
the last digit: wind-6-r1 attempts 1 and 2 are both 0.620148…, and wind-6-r2 attempts 1
and 2 are both 0.65893…. Those are one bag scored twice, not two flights, so the row counts
distinct flights. Earlier write-ups said "10 of 12", which counted attempts.

## What is established

1. **OpenVINS is deterministic on fixed inputs.** Three replays of the 29 Sep compose
   recording each reproduce the live estimate: 1,784 updates, and divergence at 24.6 s. The
   error curves match to two decimals. The run-to-run variation is therefore in the inputs,
   not in the estimator.
2. **Every recorded non-image input is the same in good and bad runs** (`signals.py` over
   ten bags: 25 Sep good and bad, and 30 Sep OSMO and Argo):
   - real-time factor 1.0;
   - camera intrinsics, and extrinsics (`tf_static`);
   - stereo pairing (0 % unpaired);
   - IMU noise, bias, gravity and gaps;
   - the flown trajectory, within 0.07 m on calm runs, with the same 40 °/s yaw at the
     corner.
3. **The stack is the same:**
   - the generated stack configs are byte-identical, except `metrics_runtime.json`;
   - the pak files are the same (sizes and 25 Sep mtimes);
   - the image digests are the same, checked on the node;
   - the GPU driver is 580.178.04, installed on 25 Sep at 11:06, *before* the good runs,
     on the same boot.
4. **So the variation is in the rendered image stream.** The simulator does not deliver the
   same frames from run to run, and since 25 Sep the stream at the corner defeats this
   OpenVINS configuration far more often.
5. **The configuration is marginal at the corner.** Capping OpenVINS's tracking rate delays
   the divergence on a fixed recording but does not remove it: 24.6 s at the default 30 Hz
   gate (26.5 Hz of images), 29.3 s at 20.8 Hz, 36.6 s at 15 Hz. The config was tuned when
   the bridge delivered 20.8 Hz (its comment: "11 clones = 0.50 s measured window"); today
   the cameras run at 23–27 Hz.

## Ruled out

| # | Hypothesis | Test | Result |
| --- | --- | --- | --- |
| H0 | The Argo pod | Interleaved benchmark: same spec and images, OSMO and Argo | Both diverge, 0 of 3 and 0 of 3 |
| H1 | The sky follows the wall clock | `simGetSolarTime` twice, a minute apart | 0.0 at boot on every run, then +0.033 h per minute. The sim log also warns "UltraDynamicSky exposes none of the known refresh functions; time-of-day writes will not move the sun" |
| H2 | Transport: Fast DDS shared memory | UDPv4 only, as compose runs it; one flight | 9.85 m, no better (reverted) |
| H3 | `-MnSScenarioConditions` resets the sky | Sim command line | Not passed for this stack. The adapter logs "Scenario time of day and weather are disabled", the same on 25 Sep |
| H4 | Camera–IMU time offset | Replay with `timeshift_cam_imu` −50/−25/+25/+50 ms, and with online calibration | Every shift is worse. Online calibration converges to −2 ms and still diverges at 24.6 s |
| H5 | Stale frames (new stamp, previous pixels) | Per-frame freshness in flight (`FRAME_STATS_TOPICS`) | About 10 % stale on Argo in good *and* bad runs (0.55 m against 7.4 m). The 29 Sep compose bag shows a 25 % burst at the corner and 0 % in straight flight: present, but it does not separate good from bad |
| H6 | Engine frame rate (~23 FPS on 25 Sep against ~50 today) | Engine capped at 23 FPS (`t.MaxFPS`, verified from the sim log), 3 flights | 0 of 3 good (4.0, 5.3, 10.3 m) |
| H7 | Camera rate against the rate the config was tuned for | Replay capped at 20.8 and 15 Hz | Delays the divergence, does not remove it (see 5 above). Refined by H8 |
| H8 | **Camera rate against the window length (confirmed)** | Live at ~11 Hz; replay at 11 Hz and with 22 clones | See "The finding, in numbers" |
| — | IMU noise differs per run | Parked gyro noise cross-correlated between runs (`imuseed.py`) | It differs (0.22–0.39 correlation). AirSim seeds it with a fixed 42, and the per-run difference comes from the free-running clock's tick timing, so there is no seed to fix. It is not what flips the outcome: rate does |
| — | `SteppableClock` (lockstep), ArduPilot only | 2 flights each way | 0.95 and 1.00 m against 1.19 and 1.25 m on `ScalableClock`: slightly better, not decisive. ArduPilot's cameras ran at 10–11 Hz both ways; the other simulator held the GPU. PX4 must stay on `ScalableClock` (not lockstep) |
| — | Engine hitches at the corner | Wall gaps between capture services (`hitch.py`) | None over 100 ms, good or bad; p99 67–86 ms on Argo, 42–62 ms on OSMO |
| — | Boot-to-takeoff time | Sim log start to ground-truth takeoff | 39.0 ± 0.2 s in every run |
| — | The task-script changes on this branch | Diff against the 25 Sep `osmo/files` | The same behaviour for this stack; compose diverged without them |
| — | GPU throttling | `nvidia-smi -q` | No thermal or power-brake events; the power cap was reached for 0.2 s in total |

## Side findings

These are real, but none of them is the drift:

- **Argo pod camera stamps.** In the one-pod layout, 266–275 camera stamps per run are
  duplicated or run backwards, against 0–3 on OSMO. The Argo pod's cameras also deliver
  about 8 % fewer frames (see the benchmark). The bridge drops a repeated
  `response.time_stamp` (`vehicle_node_base.cpp` ~2838), so these are different stamps.
- **Stale pixels pass the bridge.** It deduplicates by timestamp only, so a frame with the
  previous frame's pixels and a new stamp goes through to the estimator.
- **The clock contradicts the estimator config.** The stack runs `"ClockType":
  "ScalableClock"`, a free-running clock. The OpenVINS config's `timeshift_cam_imu: 0.0` is
  commented "lockstep run; the steppable clock does not advance while a capture is pending".
  That premise does not hold for this stack, even though the time offset itself measures
  about 2 ms.
- **The registry holds re-scored evidence as separate attempts** (see "How the 25 Sep row
  was counted"). Count distinct flights, not attempts, before comparing rates.

## What to change

1. **Size the estimator's window in seconds, not frames.** `max_clones` scales with the
   camera rate (22 at 25 Hz, about 0.9 s). Or cap `track_frequency` near the rate the
   config was tuned for. On the fixed recording, both cut the error 20–30×.
2. **Make the camera rate part of the scenario.** At the moment it falls out of GPU load.
   Pin it: the bridge's `poll_rate_hz` is now an override (`POLL_RATE_HZ`, template
   parameter `bridge_poll_rate_hz`). The stack generator should derive it from the
   ScenarioSpec's cameras.
3. **Record and gate it.** The recording validator should fail a run whose camera rate is
   outside the scenario's bounds, the way it gates the IMU. A campaign that compares
   estimators across runs must not let a neighbour's GPU load pick the frame rate.
4. **Give campaign runs the GPU to themselves.** On the cluster, a whole-GPU request plus
   Kueue does this; on a shared host, check for other simulators first. The design doc's
   risk list already names shared hosts.

## Experiments run on 30 Sep (the earlier list)

1. **Corner images of a good and a bad run.** The recorder gained an opt-in frame dump
   (`FRAME_DUMP_TOPICS`). Every dump run passed, but because of the other simulator (see
   above), not the dump, so there was no bad run to diff against. The rate finding made
   the pixel diff unnecessary.
2. **IMU noise seed.** It cannot be fixed per run, and it is not the lever (see H-table).
3. **Lockstep clock.** A small gain on ArduPilot. Not applicable to PX4 as configured.
4. **Host or GPU reset.** Not needed: the "state" was other GPU load, not stuck host
   state.

## Operational note

During this investigation the root filesystem reached 100 %: Claude session scratch held
15 GB under `/tmp/claude-1000`, and another session was cloning a repository at the same
time. Tool output, memory and git writes all failed until the user approved deleting this
session's 28 Sep city recordings (4.1 GB). Free space was still falling afterwards.
