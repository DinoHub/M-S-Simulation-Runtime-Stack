# tools/

Host-run helpers behind `./setup.sh`, `./download-packs.sh`, `make dashboard`
and `./product.sh`. Users run those entry points, not these directly.

| Tool | Does |
|---|---|
| `images.sh` / `images.py` | The image catalog (`images/catalog.yaml`): `sync` renders the generated env and image-set files, `verify` is the CI drift gate, `report` / `bump` / `drift`. See [docs/images.md](../docs/images.md). |
| `ensure-images.sh` | Uses local image tags and pulls only missing ones (`make ensure-images`). |
| `pull-all-images.sh` | Refreshes every published pin (`./product.sh setup` / `pull-images`). |
| `check-image-pins.sh` | CI check that nothing pins an image outside the catalog. |
| `load-images-env.sh` | Sources a generated image env without overriding the shell or `./.env`. |
| `install-demo-packs.sh` / `install_demo_packs.py` | Downloads, verifies and installs content packs from the channel's lock (behind `./download-packs.sh`). |
| `pull-packs.sh` | Installs packs by release tag, lists remote releases, or imports archives from the pack mount directory. |
| `stage-authoring-packs.sh` | Stages installed packs for ScenarioLab. |
| `build_pack_lock.py` | Rebuilds a channel's pack lock from published releases (`make pack-lock`). |
| `preview_topics.py` | The ROS 2 topics a generated stack will publish (`make topics STACK=generated/<name>`). |
| `check_docker.sh`, `compose_retry.sh` | Docker / X11 / port preflights and a retrying `docker compose` wrapper. |
| `test_install_demo_packs.py` | Offline tests: `python3 -m pytest tools/`. |

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
