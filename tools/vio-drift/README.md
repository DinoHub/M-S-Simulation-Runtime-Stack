# VIO drift probes

The tools behind [the VIO divergence investigation](../../docs/design/2026-09-30-vio-divergence-drift.md).
They read recorded bags (mcap or sqlite) with `rosbags` on the host; `replay.sh` also needs docker.

| Tool | What it answers |
| --- | --- |
| `signals.py BAG...` | Are the estimator's non-image inputs the same across runs? Real-time factor, IMU and `camera_info` rates, gaps, duplicate or backwards stamps, stamp lag against `/clock`, intrinsics, `tf_static`, stereo pairing, parked IMU noise. |
| `score_bag.py BAG [EST_TOPIC]` | How the estimate's error grows over the flight. It aligns on the first 10 s of flight, prints the median error per 10 s and when it first exceeds 3 m. |
| `replay.sh BAG VIO_CFG TAG [N]` | Is the estimator deterministic on this recording, and what does a config change do? It plays the bag's images, IMU and clock into the estimator image N times and scores each run. Output goes under `$OUTROOT` (default `./vio-drift-replays`). |
| `frames.py BAG` | For a bag with images: stale frames (the previous frame's pixels under a new stamp), brightness and sharpness per 5 s of flight. |
| `hitch.py SIMLOG GT_TUM` | Engine hitches: wall gaps between capture services, in cruise and at the corner, relative to takeoff. |
| `enginefps.py SIMLOG...` | The engine's own frame rate over a run, from the frame counter on the capture log lines. |

Without recording images, set the WorkflowTemplate's `frame_stats_topics` parameter (the
recorder's `FRAME_STATS_TOPICS`). The recorder then writes each frame's stamp, its
difference from the previous frame and its brightness to `frame_stats.jsonl`, beside the
bag.
