# Investigation: stereo VIO divergence that appeared after 25 Sep 2026

**Status: open.** The root cause is not found yet. This page records what changed, what was
measured, what is ruled out, and the next experiments, so the work can be picked up
without repeating it.

Related: [the OSMO/Argo benchmark](../../argo/README.md#benchmark-argo-against-osmo-30-sep-2026),
[migration decision log](2026-09-30-argo-migration-decisions.md) entries 21, 27 and 28.
Tools: [`tools/vio-drift/`](../../tools/vio-drift/README.md).

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
| H7 | Camera rate against the rate the config was tuned for | Replay capped at 20.8 and 15 Hz | Delays the divergence, does not remove it (see 5 above) |
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

## Next experiments, most informative first

1. **Record the corner images of a good and a bad run, and diff them.** Every other input is
   equal, so the difference is in the pixels: exposure, blur, content, staleness pattern.
   About 0.2 GB per run at half resolution in grey is enough. It was not run: the disk was
   full (see Operational).
2. **Seed the IMU noise.** `generate_noise: true` may draw a new random sequence each run. A
   fixed seed would show whether the per-run randomness is the IMU's rather than the
   renderer's.
3. **Fly with the steppable (lockstep) clock** that the OpenVINS config assumes. It removes
   the render-versus-stamp race altogether. If divergence stops, the config and the clock
   must be made to agree.
4. **Reset the host state.** The shift began after 25 Sep 16:00, on a host that has not
   rebooted since 25 Sep 11:09. A GPU reset or a reboot, then a few flights, tests a
   host-state cause. That needs the user: it affects every session on the machine.

## Operational note

During this investigation the root filesystem reached 100 %: Claude session scratch held
15 GB under `/tmp/claude-1000`, and another session was cloning a repository at the same
time. Tool output, memory and git writes all failed until the user approved deleting this
session's 28 Sep city recordings (4.1 GB). Free space was still falling afterwards.
