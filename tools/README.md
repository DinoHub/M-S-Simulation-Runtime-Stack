# tools/

This dir holds host-run helpers for the runtime stack (`generate_scenario.py`).
Sim-facing dev/user tools (weather control, etc.) live in **Cosys-AirSim** under
`rpc-clients/python/tools/` and are reached via `./tools.sh` or run on the host —
see [Sim-facing tools](#sim-facing-tools-toolssh) below.

## generate_scenario.py

Jinja2-templated regenerator for scenario compose files (ardupilot-xfs today;
more scenarios register via the `SCENARIOS` dict).
Single source of truth: the runtime-stack root `.env`.

### What it generates

| Output | Template |
|---|---|
| `compose/ardupilot-xfs/docker-compose.yml` | `compose/ardupilot-xfs/templates/docker-compose.yml.j2` |
| `compose/ardupilot-xfs/docker-compose.mavros-test.yml` | `compose/ardupilot-xfs/templates/docker-compose.mavros-test.yml.j2` |
| `config/unreal-airsim/xfs/settings-ardupilot.json` | `config/unreal-airsim/xfs/templates/settings-ardupilot.json.j2` |

### Inputs (from root `.env`)

```
NUM_DRONES                  drones in the fleet (1..16; bump MAX_DRONES if more)
VEHICLE_PREFIX              default vehicle name prefix (e.g. "Copter")
DRONE_X_SPACING_M           per-drone X offset in AirSim NED frame
MAVLINK_PORT_BASE/STRIDE    SITL N -> MAVLink TCP base + N*stride
FDM_{TCP,UDP}_PORT_BASE     ArduPilot ↔ AirSim FDM ports
FDM_PORT_STRIDE
AGENT_INTERNAL_SUBNET_BASE  /24 prefix for agent_internal-N (e.g. "172.28")
```

Per-drone overrides remain optional: `VEHICLE_{N}_NAME` and
`DRONE_{N}_DOMAIN_ID` fall back to `${VEHICLE_PREFIX}{N}` and `{N}`.

### Usage

```bash
# Regenerate (default)
python3 tools/generate_scenario.py

# Self-test (renders for N in {1, 2, 4, 8, 16}; no write)
python3 tools/generate_scenario.py --self-test

# Drift check (exit 0 = outputs match .env+templates, 1 = drift)
python3 tools/generate_scenario.py --check
```

`launch.sh ardupilot-xfs` runs `--check` first and only regenerates if
drift is detected, so the dev flow is simply: edit `.env`, run
`./launch.sh ardupilot-xfs`.

### Dependencies

```bash
pip install -r tools/requirements.txt   # jinja2, python-dotenv
```

### Editing the templates

The Jinja2 templates live next to their outputs:

- `compose/ardupilot-xfs/templates/*.j2`
- `config/unreal-airsim/xfs/templates/*.j2`

After editing, regenerate and run `--self-test`. Self-test invariants
catch most copy-paste mistakes (port arithmetic, clock-master uniqueness,
unsubstituted Jinja tokens, expected service counts).

## Sim-facing tools (`./tools.sh`)

Tools that talk to a running simulator over RPC (e.g. `weather_gui` — a web
weather panel) live in Cosys-AirSim's `rpc-clients/python/tools/` and are bundled
in the `dhdevspace/auto_mns:airsim-tools-*` image (`AIRSIM_TOOLS_IMAGE` in `.env`),
built & published from that repo via
`docker compose -f runtime/docker/compose/docker-compose.tools.yml build/push tools`.

Run them against the live stack (host-networked, hits AirSim on `127.0.0.1:41451`):

```bash
./tools.sh                    # list available tools
./tools.sh <name> [args...]   # run a tool
./tools.sh weather_gui        # serves the web panel — open http://localhost:8088
```

`./tools.sh` wraps `docker compose -f docker-compose-tools.yml --profile tools
run --rm tools …`; the `tools` profile means it never starts with `docker compose up`.
A **web tool** (`PORT = …`, e.g. `weather_gui` on 8088) stays attached and serves a
port — Ctrl-C to stop; under host networking the port is just `localhost:<port>`.

A hypothetical **native-window tool** (`GUI = True`) would render a window, so
`./tools.sh <it>` prints host instructions instead — run those on the host:

```bash
pip install -e <cosys-airsim>/rpc-clients/python
python <cosys-airsim>/rpc-clients/python/tools/run.py <name>
# set AIRSIM_HOST / AIRSIM_PORT if the simulator isn't on localhost:41451
```
