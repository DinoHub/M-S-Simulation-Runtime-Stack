# ardupilot-urbansim Scenario Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the UrbanSimDemo Unreal/Cosys-AirSim environment (`dhdevspace/auto_mns:urbansimdemo-latest`) as a launchable scenario `ardupilot-urbansim`, cloned from `ardupilot-xfs`.

**Architecture:** Dir-per-scenario clone. New `compose/ardupilot-urbansim/` + `config/unreal-airsim/urbansim/` template dirs rendered by `tools/generate_scenario.py` (Jinja2). Only the AirSim sim service diverges from `ardupilot-xfs`; SITL, MAVROS, bridges, zenoh, QGC are renamed copies. Spec: `docs/superpowers/specs/2026-08-11-urbansim-environment-design.md`.

**Tech Stack:** Docker Compose, Jinja2 (via `tools/generate_scenario.py`), bash (`launch.sh`/`stop.sh`), Make.

## Global Constraints

- Branch: `feat/ardupilot-urbansim-scenario` (already created off `main` @ c382748).
- Scenario name everywhere: `ardupilot-urbansim`. Sim service name: `airsim-urbansim`. Container prefix: `ardupilot-urbansim-*`.
- Settings mount target inside sim container: `/home/ue4/Documents/AirSim/settings.json` (image user is `ue4`, uid 1000).
- UE launcher inside image: `/app/UrbanSimDemo/UrbanSimDemo.sh` (forwards args to the shipping binary).
- Never edit generated files by hand (`compose/ardupilot-urbansim/docker-compose.yml`, `config/unreal-airsim/urbansim/settings-ardupilot.json`) — edit templates and regenerate.
- Do NOT touch any `ardupilot-xfs` file except the shared launcher/Makefile edits in Task 4.
- Generated-file checks: `python3 tools/generate_scenario.py --self-test` and `--check` must pass after every task that touches templates or the generator.

---

### Task 1: Compose template for ardupilot-urbansim

**Files:**
- Create: `compose/ardupilot-urbansim/templates/docker-compose.yml.j2` (from `compose/ardupilot-xfs/templates/docker-compose.yml.j2`)

**Interfaces:**
- Produces: template rendered by Task 3's generator registration. Service names consumed downstream: `airsim-urbansim` (sim), `airsim_bridge_d{{N}}`, `ardupilot-drone-{{instance}}` (unchanged from xfs pattern).

- [ ] **Step 1: Copy with mechanical renames**

```bash
mkdir -p compose/ardupilot-urbansim/templates
sed -e 's/ardupilot-xfs/ardupilot-urbansim/g' \
    -e 's/airsim-xfs/airsim-urbansim/g' \
    -e 's|unreal-airsim/xfs|unreal-airsim/urbansim|g' \
    compose/ardupilot-xfs/templates/docker-compose.yml.j2 \
    > compose/ardupilot-urbansim/templates/docker-compose.yml.j2
```

This renames the compose project (`name:`), all `container_name`/`hostname` values, the `./data/ardupilot-urbansim/...` state dirs, the settings config paths, and the sim service key + its `depends_on` references.

- [ ] **Step 2: Replace the sim service internals**

In the new template, the (now renamed) `airsim-urbansim:` service still carries xfs image/paths. Replace these five pieces. Everything else in the service block (profiles, depends_on, user, group_add, init, shm_size, deploy, environment, ipc, networks) stays as the sed output produced it.

2a. Image line:

```yaml
    image: ${AIRSIM_IMAGE:-dhdevspace/auto_mns:urbansimdemo-latest}
```

2b. In `volumes:` — replace the two settings mounts (xfs mounts the same file at two paths; urbansim uses the image's sanctioned single path):

```yaml
      - ${CONFIG_ROOT:-./config}/unreal-airsim/urbansim/settings-ardupilot.json:/home/ue4/Documents/AirSim/settings.json:ro
```

(The `/tmp/.X11-unix`, `.Xauthority`, `/dev/shm`, `/tmp/iceoryx2` mounts stay.)

2c. The comment block above `entrypoint:` — replace with:

```yaml
    # The urbansimdemo image ships its own entrypoint that bootstraps
    # settings.json from a baked template — but we always bind-mount settings,
    # so that bootstrap is a no-op. Override the entrypoint (same pattern as
    # xfs) because the headless / pixel-streaming toggles need shell
    # conditionals; exec the packaged launcher script, which forwards args to
    # the UrbanSimDemo shipping binary.
    #
    # AIRSIM_HEADLESS=true selects -RenderOffScreen (off-screen GPU render,
    # cameras still work, no window). false (default) keeps -windowed for
    # interactive use. -NullRHI would skip rendering entirely and break
    # AirSim cameras — do NOT add it.
```

2d. The last line of the `command:` script — exec the urbansim launcher:

```yaml
        exec /app/UrbanSimDemo/UrbanSimDemo.sh $$DISPLAY_FLAGS -ResX=1920 -ResY=1080 $$PS_FLAG
```

(The `sleep 5`, `DISPLAY_FLAGS`, and `PS_FLAG` shell logic above it stays verbatim, including the `$$` compose escapes.)

2e. Healthcheck — the xfs log grep targets a TEVV-specific "Display Control" plugin line and an xfs log path. Use the UE-generic engine-init line and the urbansim log path; keep the timing values:

```yaml
    # Health = RPC port open *and* engine init finished. Plain `nc -z 41451`
    # flips healthy a few frames before vehicles are safe to query; an early
    # bridge RPC call can SIGSEGV the sim. Gate additionally on UE's
    # engine-initialization log line (generic across builds — urbansimdemo may
    # not ship the TEVV Display Control plugin that xfs's healthcheck greps).
    healthcheck:
      test: ["CMD-SHELL", "nc -z localhost 41451 && grep -q 'LogLoad: (Engine Initialization)' /app/UrbanSimDemo/UrbanSimDemo/Saved/Logs/UrbanSimDemo.log || exit 1"]
      interval: 10s
      timeout: 5s
      retries: 24
      start_period: 150s
```

- [ ] **Step 3: Verify no xfs remnants**

```bash
grep -n -i 'xfs' compose/ardupilot-urbansim/templates/docker-compose.yml.j2
```

Expected: no output. If any line surfaces (e.g. a comment referencing `/app/Xfs` or `Xfs.log`), rewrite that line for urbansim.

- [ ] **Step 4: Commit**

```bash
git add compose/ardupilot-urbansim/templates/docker-compose.yml.j2
git commit -m "feat(urbansim): compose template cloned from ardupilot-xfs"
```

---

### Task 2: Settings template for the urbansim map

**Files:**
- Create: `config/unreal-airsim/urbansim/templates/settings-ardupilot.json.j2` (from `config/unreal-airsim/xfs/templates/settings-ardupilot.json.j2`)

**Interfaces:**
- Consumes: shared partial `config/unreal-airsim/_partials/cameras.json.j2` (unchanged).
- Produces: template rendered by Task 3 to `config/unreal-airsim/urbansim/settings-ardupilot.json`, mounted by Task 1's compose template.

- [ ] **Step 1: Copy the xfs template**

```bash
mkdir -p config/unreal-airsim/urbansim/templates
cp config/unreal-airsim/xfs/templates/settings-ardupilot.json.j2 \
   config/unreal-airsim/urbansim/templates/settings-ardupilot.json.j2
```

- [ ] **Step 2: Adapt three things**

2a. `_comment` line — point at the new source:

```json
  "_comment": "GENERATED FILE — do not edit by hand. Regenerate via tools/generate_scenario.py. Source: config/unreal-airsim/urbansim/templates/settings-ardupilot.json.j2",
```

2b. Delete the whole `PawnPaths` block:

```json
  "PawnPaths": {
    "SpiritPawn": {"PawnBP": "Class'/Game/Meshes/BP_SpiritPawn.BP_SpiritPawn_C'"},
    "DjiPawn": {"PawnBP": "Class'/Game/Meshes/BP_MyPawn.BP_MyPawn_C'"}
  },
```

2c. Delete the per-vehicle line inside the `{% for d in drones %}` loop:

```json
      "PawnPath": "SpiritPawn",
```

Rationale: those blueprints live under `/Game/Meshes/` in the xfs package; the UrbanSimDemo package almost certainly doesn't cook them, and a dangling PawnPath breaks vehicle spawn. Omitting PawnPath falls back to the Cosys-AirSim plugin's default multirotor pawn, which ships inside the plugin content in every package.

Everything else (ports, `LocalHostIp: 0.0.0.0` + per-vehicle `LocalHostIp: 172.30.0.10`, FDM Udp/Control addressing, sensors incl. lidar, cameras partial, drones loop) stays byte-identical to xfs.

- [ ] **Step 3: Commit**

```bash
git add config/unreal-airsim/urbansim/templates/settings-ardupilot.json.j2
git commit -m "feat(urbansim): ArduCopter settings template (default pawn, no PawnPaths)"
```

---

### Task 3: Register scenario in generate_scenario.py + self-test + generate

**Files:**
- Modify: `tools/generate_scenario.py` (three registries + one new self-test function)
- Create (generated): `compose/ardupilot-urbansim/docker-compose.yml`, `config/unreal-airsim/urbansim/settings-ardupilot.json`

**Interfaces:**
- Consumes: Task 1 + Task 2 templates; existing `build_context_ardupilot_xfs(env)` (map-neutral, reused as-is).
- Produces: `SCENARIOS["ardupilot-urbansim"]`, `CONTEXT_BUILDERS["ardupilot-urbansim"]`, `SELF_TESTS["ardupilot-urbansim"]`, and the two generated output files consumed by launch.sh.

- [ ] **Step 1: Write the failing test (registry entry + self-test)**

In `SCENARIOS` (after the `"ardupilot-xfs"` entry) add:

```python
    "ardupilot-urbansim": [
        (
            "compose/ardupilot-urbansim/templates/docker-compose.yml.j2",
            "compose/ardupilot-urbansim/docker-compose.yml",
        ),
        (
            "config/unreal-airsim/urbansim/templates/settings-ardupilot.json.j2",
            "config/unreal-airsim/urbansim/settings-ardupilot.json",
        ),
    ],
```

In `CONTEXT_BUILDERS` add (the xfs builder is map-neutral — same drones/ports/subnets):

```python
    "ardupilot-urbansim": build_context_ardupilot_xfs,
```

Above `SELF_TESTS`, add the self-test function (adapted from `_self_test_ardupilot_xfs`; urbansim has 2 render pairs, not 3 — no mavros-test template):

```python
def _self_test_ardupilot_urbansim() -> None:
    """Idempotency + invariant checks for the urbansim clone of ardupilot-xfs."""
    base_env = {
        "NUM_DRONES": "4",
        "VEHICLE_PREFIX": "Copter",
        "DRONE_X_SPACING_M": "8",
        "MAVLINK_PORT_BASE": "5760",
        "MAVLINK_PORT_STRIDE": "10",
        "FDM_TCP_PORT_BASE": "9002",
        "FDM_UDP_PORT_BASE": "9003",
        "FDM_PORT_STRIDE": "10",
        "AGENT_INTERNAL_SUBNET_BASE": "172.28",
    }
    j_env = make_env()
    pairs = SCENARIOS["ardupilot-urbansim"]

    for n in (1, 2, 4, 8, 16):
        env = dict(base_env, NUM_DRONES=str(n))
        ctx = build_context_ardupilot_xfs(env)

        renders = [render(j_env, t, ctx) for t, _ in pairs]
        renders2 = [render(j_env, t, ctx) for t, _ in pairs]
        for a, b in zip(renders, renders2):
            assert a == b, f"urbansim render not idempotent for N={n}"

        compose_yaml, settings_json = renders

        assert compose_yaml.count("ardupilot-urbansim-drone-") >= n, \
            f"expected at least {n} SITL containers in compose for N={n}"
        for d in ctx["drones"]:
            assert f"ardupilot-drone-{d.instance}:" in compose_yaml, \
                f"missing SITL service for drone instance {d.instance}"
            assert f"airsim_bridge_d{d.n}:" in compose_yaml, \
                f"missing bridge service for drone {d.n}"
            assert f"agent_internal-{d.n}" in compose_yaml, \
                f"missing agent_internal-{d.n} attachment"
            assert f'"{d.vehicle}":' in settings_json, \
                f"missing {d.vehicle} in settings.json"

        if n < MAX_DRONES:
            assert f"airsim_bridge_d{n + 1}:" not in compose_yaml, \
                f"unexpected bridge_d{n + 1} in compose for N={n}"

        # Urbansim-specific invariants.
        assert "urbansimdemo-latest" in compose_yaml, "wrong default AIRSIM_IMAGE"
        assert "/home/ue4/Documents/AirSim/settings.json" in compose_yaml, \
            "settings must mount at the image's sanctioned path"
        assert "airsim-urbansim:" in compose_yaml, "sim service must be airsim-urbansim"
        assert "xfs" not in compose_yaml.lower(), "xfs remnant leaked into urbansim compose"
        assert '"PawnPath"' not in settings_json, \
            "urbansim settings must not reference xfs pawn blueprints"

        assert "enable_coordination:=true" not in compose_yaml, \
            f"enable_coordination must be false on every bridge for N={n}"
        assert compose_yaml.count("enable_coordination:=false") == n, \
            f"expected {n} bridges with enable_coordination:=false for N={n}"

        for label, body in (("compose", compose_yaml), ("settings", settings_json)):
            assert "{{" not in body and "{%" not in body, \
                f"unsubstituted Jinja in {label} output for N={n}"

        json.loads(settings_json)
```

In `SELF_TESTS` add:

```python
    "ardupilot-urbansim": _self_test_ardupilot_urbansim,
```

(The module-level `_missing_builders` / `_missing_tests` asserts make a partial registration fail at import — that's the built-in test for forgetting one of the three registries.)

- [ ] **Step 2: Run the self-test**

```bash
python3 tools/generate_scenario.py --self-test --scenario ardupilot-urbansim
```

Expected: `self_test[ardupilot-urbansim]: OK`. If an assert fires, fix the Task 1/2 templates (not the asserts) unless the assert itself has a typo.

- [ ] **Step 3: Generate the outputs**

```bash
python3 tools/generate_scenario.py --scenario ardupilot-urbansim
python3 tools/generate_scenario.py --check
```

Expected: `Regenerated ardupilot-urbansim (2 files).` and `--check` exits 0 with no drift in ANY scenario (proves xfs untouched).

- [ ] **Step 4: Validate the generated compose parses**

```bash
docker compose --project-directory . \
  -f compose/ardupilot-urbansim/docker-compose.yml \
  --profile sim --profile per-drone-bridge --profile agent-external \
  --profile pixel-streaming config -q
```

Expected: exit 0, no output. (Uses repo-root `.env` exactly like launch.sh.)

- [ ] **Step 5: Run the full self-test suite (regression)**

```bash
python3 tools/generate_scenario.py --self-test
```

Expected: `self_test[<name>]: OK` for all five scenarios.

- [ ] **Step 6: Commit**

```bash
git add tools/generate_scenario.py compose/ardupilot-urbansim/docker-compose.yml \
  config/unreal-airsim/urbansim/settings-ardupilot.json
git commit -m "feat(urbansim): register ardupilot-urbansim in scenario generator"
```

---

### Task 4: Wire launch.sh and Makefile

**Files:**
- Modify: `launch.sh:294` (agent nets + per-drone-bridge profiles), `launch.sh:308` (pixel-streaming case), `launch.sh` header comments (lines ~38-48)
- Modify: `Makefile:4`, `Makefile:54`, `Makefile:70`
- `stop.sh`: NO change (auto-detects scenarios by compose dir; profile superset already covers).

**Interfaces:**
- Consumes: `compose/ardupilot-urbansim/docker-compose.yml` from Task 3 (launch.sh resolves scenarios by that path existing).
- Produces: `./launch.sh ardupilot-urbansim` and `make ardupilot-urbansim` entry points.

- [ ] **Step 1: launch.sh profile block**

At `launch.sh:294`, change:

```bash
if [ "$SCENARIO" = "ardupilot-xfs" ]; then
```

to:

```bash
if [ "$SCENARIO" = "ardupilot-xfs" ] || [ "$SCENARIO" = "ardupilot-urbansim" ]; then
```

At `launch.sh:308`, change the case pattern:

```bash
  ardupilot-xfs|px4-condo)
```

to:

```bash
  ardupilot-xfs|ardupilot-urbansim|px4-condo)
```

- [ ] **Step 2: launch.sh header comments**

In the usage comment block (lines ~37-48), update the two flag descriptions so the new scenario is documented:

- `--with-agent-external (ardupilot-xfs default flow only)` → `--with-agent-external (ardupilot-xfs / ardupilot-urbansim default flow only)`
- `--with-pixel-streaming (ardupilot-xfs, px4-condo)` → `--with-pixel-streaming (ardupilot-xfs, ardupilot-urbansim, px4-condo)`

- [ ] **Step 3: Makefile**

Line 54:

```make
SCENARIOS := ardupilot-xfs ardupilot-urbansim px4-xfs px4-condo ardupilot-condo
```

Line 4 comment and line 70 help echo: append `ardupilot-urbansim` to the scenario list in both strings, e.g.

```make
#   make ardupilot-xfs | ardupilot-urbansim | px4-xfs | px4-condo | ardupilot-condo
```

```make
	@echo "  make ardupilot-xfs | ardupilot-urbansim | px4-xfs | px4-condo | ardupilot-condo"
```

- [ ] **Step 4: Syntax-check both**

```bash
bash -n launch.sh && bash -n stop.sh
make -n ardupilot-urbansim >/dev/null && echo MAKE-OK
```

Expected: no bash errors; `MAKE-OK`.

- [ ] **Step 5: Commit**

```bash
git add launch.sh Makefile
git commit -m "feat(urbansim): wire ardupilot-urbansim into launch.sh and Makefile"
```

---

### Task 5: Scenario README + spec amendments

**Files:**
- Create: `compose/ardupilot-urbansim/README.md`
- Modify: `docs/superpowers/specs/2026-08-11-urbansim-environment-design.md` (sim-service delta section)

- [ ] **Step 1: Write the README**

```markdown
# ardupilot-urbansim

UrbanSimDemo Unreal/Cosys-AirSim map + ArduPilot SITL fleet. A clone of
`compose/ardupilot-xfs/` — same SITL / MAVROS / per-drone bridge / zenoh /
QGC topology, networks, and profiles — with the sim service swapped to
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

Untested knobs (wired but unverified on this map): `-scenario=` JSON
loading (needs ScenarioRunner in the package), pixel streaming (needs the
PixelStreaming plugin in the package).

Templates live in `templates/`; regenerate with
`python3 tools/generate_scenario.py --scenario ardupilot-urbansim`.
Never hand-edit `docker-compose.yml`.
```

- [ ] **Step 2: Amend the spec**

In the spec's "Sim service deltas" section, replace delta 2 ("Keep the image entrypoint...") with the implemented decision:

```markdown
2. **Entrypoint overridden** (same pattern as xfs): the headless /
   pixel-streaming toggles need shell conditionals, and the image's own
   entrypoint only bootstraps settings.json — a no-op under our bind
   mount. The compose command execs `/app/UrbanSimDemo/UrbanSimDemo.sh`.
```

And append two lines to the same section:

```markdown
7. Healthcheck greps UE's generic `LogLoad: (Engine Initialization)` log
   line instead of xfs's TEVV-specific "Display Control listening" line;
   log path `/app/UrbanSimDemo/UrbanSimDemo/Saved/Logs/UrbanSimDemo.log`.
8. Settings drop `PawnPaths`/`PawnPath` (xfs blueprint paths aren't in
   this package); the Cosys-AirSim default multirotor pawn is used.
```

- [ ] **Step 3: Commit**

```bash
git add compose/ardupilot-urbansim/README.md docs/superpowers/specs/2026-08-11-urbansim-environment-design.md
git commit -m "docs(urbansim): scenario README + spec amendments for implemented deltas"
```

---

### Task 6: Live smoke test (main session — needs GPU + X, not a subagent)

**Files:** none (verification only; fixes loop back into Task 1-3 templates + regenerate).

- [ ] **Step 1: Launch**

```bash
./launch.sh ardupilot-urbansim
```

Expected: UE window appears (urbansim map), no restart loops in `docker ps`.

- [ ] **Step 2: Verify healthcheck path actually exists**

```bash
docker exec ardupilot-urbansim-airsim ls /app/UrbanSimDemo/UrbanSimDemo/Saved/Logs/
docker inspect --format '{{.State.Health.Status}}' ardupilot-urbansim-airsim
```

Expected: `UrbanSimDemo.log` listed; status reaches `healthy` within ~3 min. If the log path is wrong (UE writing elsewhere or dir not writable as uid 1000), fix the healthcheck path in the Task 1 template, regenerate, relaunch. Fallback if the log line never appears: drop the grep and keep `nc -z localhost 41451` with the long `start_period`.

- [ ] **Step 3: Verify SITL ↔ AirSim FDM + bridge topics**

```bash
docker logs ardupilot-urbansim-drone-0 2>&1 | tail -5
docker exec ardupilot-urbansim-airsim-bridge-d1 bash -lc \
  'source /opt/ros/humble/setup.bash && ros2 topic list' 2>/dev/null | head -20
```

(If the bridge container name differs, find it with `docker ps --format '{{.Names}}' | grep bridge`.)
Expected: SITL shows FDM connection (no "link down" spam); sensor topics (`.../lidar`, `.../imu`) listed with publishers.

- [ ] **Step 4: Vehicles spawned, not falling through the map**

Watch the UE window: drones visible at spawn, not tumbling. If they spawn inside geometry, adjust spawn origin — raise per-vehicle `"Z"` or set an `OriginGeopoint`/PlayerStart-relative offset in the Task 2 template, regenerate, relaunch.

- [ ] **Step 5: Teardown + xfs regression**

```bash
make stop
docker ps --format '{{.Names}}' | grep -c urbansim; echo "---"
./launch.sh ardupilot-xfs
```

Expected: grep count 0 after stop; ardupilot-xfs still boots. Then `make stop` again.

- [ ] **Step 6: Commit any tuning that came out of the smoke test**

```bash
python3 tools/generate_scenario.py --check   # must be clean before commit
git add -A compose/ardupilot-urbansim config/unreal-airsim/urbansim
git commit -m "fix(urbansim): smoke-test tuning (healthcheck/spawn)"   # only if changes exist
```
