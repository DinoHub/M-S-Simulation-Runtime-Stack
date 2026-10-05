# tools/fisheye_probe

Small measurement helpers behind [docs/fisheye-rig-performance.md](../../docs/fisheye-rig-performance.md).
They run against a live generated stack; none of them change it.

| File | Runs where | What it does |
| --- | --- | --- |
| `ue_cmd.py` | host | Sends one Unreal console command to AirSim's RPC port (`simRunConsoleCommand`). Use it for live CVars (`r.LumenScene.SurfaceCache.CardCapturesPerFrame 16`, `r.Fisheye.SyncCaptureFPS 30`) and for `startfpschart` / `stopfpschart`. |
| `hashcount.py` | bridge container | Subscribes to one image topic until SIGINT. Counts delivered frames, frames whose pixels changed, and distinct capture stamps. A re-published frame keeps its old stamp, so **distinct stamps per second is the real frame rate**. |
| `grabframe.py` | bridge container | Saves one frame as `.npy` and prints mean luminance and dark fraction. |
| `rtf.py` | bridge image | For every `*/bag`: sim real-time factor (`/clock` against receive time) and per-camera `camera_info` rates. |
| `flight_stats.py` | bridge image | The same, but only over the flight window (ground truth more than 0.5 m from the start), plus path length and altitude. |

Engine frame rate: `ue_cmd.py startfpschart`, wait, then `ue_cmd.py stopfpschart`. Read
`Saved/Profiling/FPSChartStats/*/*.log` inside the sim container: `N frames collected over T
seconds`. Don't use the log's `[frame]` prefix, because it wraps at 1000.

GPU breakdown of one frame: `ue_cmd.py "r.ProfileGPU.ShowUI 0"`, then `ue_cmd.py ProfileGPU`.
The table lands in `docker logs <sim>`.
