# tools/

## generate_scenario.py

Jinja2-templated regenerator for the **ardupilot-xfs** scenario.
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
