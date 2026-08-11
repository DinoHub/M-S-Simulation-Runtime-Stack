# Design: `ardupilot-urbansim` simulation environment

**Date:** 2026-08-11
**Status:** Approved (pending implementation)
**Branch:** `feat/ardupilot-urbansim-scenario` (off `main` @ c382748)

## Goal

Add the UrbanSimDemo Unreal/Cosys-AirSim environment
(`dhdevspace/auto_mns:urbansimdemo-latest`) as a new launchable scenario,
`ardupilot-urbansim`, following the existing dir-per-scenario pattern
(`ardupilot-xfs`, `px4-xfs`, `ardupilot-condo`, `px4-condo`).

ArduPilot only. A PX4 variant is out of scope and can be added later by
copying this pattern.

## Approach decision

Three options were considered:

- **A. Dir-per-scenario clone** (chosen): new `compose/ardupilot-urbansim/`
  and `config/unreal-airsim/urbansim/` directories, cloned from
  `ardupilot-xfs` with the sim service adapted to the urbansimdemo image.
  Matches the repo convention (`launch.sh` resolves scenarios by directory
  name), zero risk to the working xfs scenario. Cost: ~90% duplicated
  compose template.
- **B. Parameterize `ardupilot-xfs` with an image/map env var**: rejected —
  container names and the compose project would still say "xfs", the two
  images want different settings-mount paths and invocation styles, and it
  fights the directory-keyed scenario convention.
- **C. Refactor to one shared map-agnostic template**: rejected for now —
  refactors a working scenario for a second map. Worth revisiting if a
  third map arrives.

## Image facts (drive the sim-service deltas)

Inspected from `dhdevspace/auto_mns:urbansimdemo-latest`:

- Standard UE5 packaged build. Launcher `/app/UrbanSimDemo/UrbanSimDemo.sh`
  execs `UrbanSimDemo-Linux-Shipping` and forwards all args.
- Image entrypoint (bash `-lc` script) bootstraps
  `/home/ue4/Documents/AirSim/settings.json` from
  `/opt/airsim/defaults/settings.template.json` unless a bind mount is
  already present, then execs the launcher. The sanctioned settings mount
  target is therefore `/home/ue4/Documents/AirSim/settings.json`.
- Runs as user `ue4` = 1000:1000, already in the `video` group.
- Cosys-AirSim (same lineage as xfs/condo); default template is
  SimpleFlight — our mounted ArduCopter settings replace it.
- Default cmd: `-windowed -ResX=1080 -ResY=720` (plain UE flags, so
  `-RenderOffScreen`, `-scenario=`, `-PixelStreamingURL` pass through).

## New files

```
compose/ardupilot-urbansim/
  templates/docker-compose.yml.j2      # cloned from ardupilot-xfs, adapted
  docker-compose.yml                   # generated output
  README.md                            # short: what differs from ardupilot-xfs
config/unreal-airsim/urbansim/
  templates/settings-ardupilot.json.j2 # cloned from xfs ArduCopter template
  settings-ardupilot.json              # generated output
```

Not cloned: `docker-compose.mavros-test.yml` (xfs-only test harness, YAGNI).

## Sim service deltas vs `ardupilot-xfs`

The AirSim sim service is the only service that meaningfully changes; SITL,
MAVROS, bridges, QGC, zenoh, and the pixel-streaming signalling sidecar are
copied unchanged (names aside).

1. `image: ${AIRSIM_IMAGE:-dhdevspace/auto_mns:urbansimdemo-latest}`.
2. **Entrypoint overridden** (same pattern as xfs): the headless /
   pixel-streaming toggles need shell conditionals, and the image's own
   entrypoint only bootstraps settings.json — a no-op under our bind
   mount. The compose command execs `/app/UrbanSimDemo/UrbanSimDemo.sh`.
3. Settings mount: single target
   `.../urbansim/settings-ardupilot.json:/home/ue4/Documents/AirSim/settings.json:ro`
   (drop xfs's dual `/app/Xfs/settings.json` mount).
4. `-scenario=` is *not* plumbed into the compose command (no flag, no
   `.env`/generator wiring) — unknown whether the UrbanSimDemo map ships a
   ScenarioRunner/ScenarioManager. Probe for it at first boot; add the flag
   then if present.
5. Pixel streaming: unknown whether the plugin is baked into this build.
   Profile and env plumbing stay; default off (as on xfs).
6. Container/hostname prefix `ardupilot-urbansim-*`. Same networks and
   fixed IPs, same `/dev/shm` + `/tmp/iceoryx2` mounts, same healthcheck,
   same profiles (`sim`, `per-drone-bridge`, `agent-external`,
   `pixel-streaming`), same `user: ${UID}:${GID}` (maps onto ue4's
   1000:1000) and `group_add: video`.
7. Healthcheck greps UE's generic `LogLoad: (Engine Initialization)` log
   line instead of xfs's TEVV-specific "Display Control listening" line;
   log path `/app/UrbanSimDemo/UrbanSimDemo/Saved/Logs/UrbanSimDemo.log`.
8. Settings drop `PawnPaths`/`PawnPath` (xfs blueprint paths aren't in
   this package); the Cosys-AirSim default multirotor pawn is used.

## Generator registration (`tools/generate_scenario.py`)

- Add `SCENARIOS["ardupilot-urbansim"]` with two template pairs (compose +
  settings).
- Reuse `build_context_ardupilot_xfs` for the render context (it is
  map-neutral). Introduce a thin wrapper only if map-specific variables
  emerge during implementation.
- Extend the self-test to cover the new scenario (same invariants as the
  xfs self-test: drone-count scaling, profile presence).

## Launcher wiring

- `launch.sh:294`: `if [ "$SCENARIO" = "ardupilot-xfs" ]` → also match
  `ardupilot-urbansim` (agent_internal network creation +
  `per-drone-bridge` / `agent-external` profile selection).
- `launch.sh:308`: add `ardupilot-urbansim` to the
  `ardupilot-xfs|px4-condo` pixel-streaming case.
- `Makefile:54`: append `ardupilot-urbansim` to `SCENARIOS` (gives
  `make ardupilot-urbansim` and help text).
- `stop.sh`: no change — auto-detects scenarios by compose dir and already
  passes the full profile superset.

## Settings template

Clone `config/unreal-airsim/xfs/templates/settings-ardupilot.json.j2`:
same SITL ports, `ClockType`, SHM lidar knobs, per-vehicle `LocalHostIp`
(FDM works over the docker bridge net when set at vehicle level), same
`NUM_DRONES` parametrization. Spawn origin starts at `0,0,0`; tune to the
urbansim map after first boot if drones spawn inside geometry.

## Verification

1. `python3 tools/generate_scenario.py --self-test --scenario ardupilot-urbansim` passes.
2. `python3 tools/generate_scenario.py --check` clean (no drift, other
   scenarios untouched).
3. `./launch.sh ardupilot-urbansim`: UE window appears, SITL connects,
   bridge sensor topics publish, QGC sees the vehicle.
4. `make stop` tears everything down; `ardupilot-xfs` still launches
   unaffected.

## Open items (resolved during implementation, not blockers)

- Does the map ship ScenarioRunner? Probe with `-scenario=` once booted.
- Does the build include the PixelStreaming plugin? Probe with
  `--with-pixel-streaming` once booted.
- Map-appropriate spawn origin.
