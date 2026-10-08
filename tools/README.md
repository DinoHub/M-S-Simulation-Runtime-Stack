# tools/

Host-run helpers behind `./setup.sh`, `./download-packs.sh`, `make dashboard`
and the headless `make` targets (`fly`, `stop`, `author`, `campaign`, `stacks`,
`doctor`). Users run those entry points, not these directly.

| Tool | Does |
|---|---|
| `images.sh` / `images.py` | The image catalog (`images/catalog.yaml`): `sync` renders the generated env and image-set files, `verify` is the CI drift gate (it also warns about pending digests and host-pin label mismatches), `status` / `report` / `bump` / `drift` / `baked`. See [docs/images.md](../docs/images.md). |
| `ensure-images.sh` | Uses local image tags and pulls only missing ones (`make ensure-images`). In development mode a tag that points at another digest than its catalog pin is pointed back at the pin, pulled by digest only if it is not local. |
| `pull-all-images.sh` | Refreshes every published pin and points its tag at it (`make pull-images`). |
| `doctor.sh` | Docker, Compose, every pinned image present, and (development mode) every pinned tag at its pin (`make doctor`). Changes nothing. |
| `image-tags.sh` | Sourced by the three above: what a local tag points at, compared with `images.sh pins`. |
| `load-images-env.sh` | Sources a generated image env without overriding the shell or `./.env`; `dotenv_value` reads a `./.env` override. |
| `mns-stacks.sh` | Runs `mns-stacks` (`MNS_STACKS_IMAGE`) as a sibling container with host paths; the Docker socket only for the commands that need it. Behind `make fly/stop/campaign/stacks`, `images.sh drift` and `osmo/campaign.py`. |
| `fly.sh` | `mns-stacks generate`, then `run [--record] --until-done` (`make fly`). |
| `author.sh` | ScenarioLab, started exactly as the dashboard's editor window starts it (`make author`, `make author-stop`). |
| `install-demo-packs.sh` / `install_demo_packs.py` | Downloads, verifies and installs content packs from the channel's lock with `mns-packs install` (behind `./download-packs.sh`). |
| `pull-packs.sh` | Installs packs by release tag, lists remote releases, or imports archives from the pack mount directory. |
| `stage-authoring-packs.sh` | Stages installed packs for ScenarioLab: `mns-packs stage-authoring --lock`, plus ScenarioLab's PackLibrary copy of the lock's asset packs. |
| `build_pack_lock.py` | Rebuilds a channel's pack lock from published releases, verified with `mns-packs verify` (`make pack-lock`). |
| `check_spawn.py` | Warns before `make fly` / `make campaign` when a vehicle starts at the world origin of a level with no floor there (XFS, Condo), with the measured start from `packs/level-spawn-hints.json`. Never blocks. |
| `preview_topics.py` | The ROS 2 topics a generated stack will publish (`make topics STACK=generated/<name>`). |
| `check_docker.sh`, `compose_retry.sh` | Docker / X11 / port preflights and a retrying `docker compose` wrapper. |
| `test_install_demo_packs.py`, `test_channels.py`, `test_check_spawn.py`, `test_image_tags.py` | Offline tests: `python3 -m unittest discover -s tools`. |
