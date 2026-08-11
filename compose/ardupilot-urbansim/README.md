# ardupilot-urbansim

UrbanSimDemo Unreal/Cosys-AirSim map + ArduPilot SITL fleet. A clone of
`compose/ardupilot-xfs/` — same SITL / per-drone bridge / zenoh / QGC
topology, networks, and profiles — with the sim service swapped to
`dhdevspace/auto_mns:urbansimdemo-latest`.

Launch: `./launch.sh ardupilot-urbansim` (or `make ardupilot-urbansim`).
Same flags as ardupilot-xfs: `--headless`, `--editor`,
`--with-agent-external`, `--with-pixel-streaming`.

Differences from ardupilot-xfs:

- Image: `urbansimdemo-latest`; launcher `/app/UrbanSimDemo/UrbanSimDemo.sh`
  (entrypoint overridden, same as xfs, for the headless/PS shell toggles —
  the image's own settings-bootstrap entrypoint is a no-op under our
  settings bind mount).
- Settings mount: single target `/home/ue4/Documents/AirSim/settings.json`
  (image user `ue4`, uid 1000).
- Settings: no `PawnPaths` / per-vehicle `PawnPath` — the xfs pawn
  blueprints aren't cooked into this package; the Cosys-AirSim default
  pawn is used.
- Healthcheck greps UE's generic `LogLoad: (Engine Initialization)` line
  (the TEVV "Display Control" plugin line xfs greps may not exist here).

Untested knobs (wired but unverified on this map): pixel streaming (needs
the PixelStreaming plugin in the package).

Scenario-JSON loading (`-scenario=`) is *not* plumbed into this scenario's
sim command — it would need a `-scenario=` flag added to the compose
command (and a value sourced from `.env`/generator context) if the
UrbanSimDemo map turns out to ship a ScenarioRunner/ScenarioManager.

Templates live in `templates/`; regenerate with
`python3 tools/generate_scenario.py --scenario ardupilot-urbansim`.
Never hand-edit `docker-compose.yml`.

When the SHM lidar transport knobs land in
`config/unreal-airsim/xfs/templates/settings-ardupilot.json.j2` (currently
on the unmerged `fix/dashboard-shm-transport` branch), port them into this
scenario's settings template too so the clone doesn't drift out of sync.
