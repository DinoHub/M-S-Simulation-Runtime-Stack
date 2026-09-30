# How the dashboard gets its images

`make dashboard` touches two different sets of images, and confusing them is
the source of most "it pulled the wrong thing" reports:

1. **The dashboard's own services** — backend, frontend, Lichtblick, the
   optional TimescaleDB. Compose starts these directly.
2. **The images the backend runs as siblings, and hands to generated stacks**
   — `mns-stacks`, `mns-packs`, the authoring app, the ROS 2 bridge, and the
   whole image set a generated stack resolves its runtime host, autopilot and
   estimator from. The backend never starts the stack's images itself: it runs
   `mns-stacks generate`, which writes their pins into `generated/<name>/.env`,
   and then `mns-stacks run` brings that stack up.

Both come from the same place — `images/catalog.yaml`, rendered by
`tools/images.sh sync` (see [Changing a container image](images.md)) — but
they reach the dashboard by different routes and are pulled by different
rules.

## Starting it, and running your own build

```bash
make dashboard                         # local-first; pulls only missing tags
make dashboard IMAGE_MODE=production   # exact release pins
make dashboard-down
```

By default, `make dashboard` runs the development workflow: it keeps any locally built matching image tags, pulls only tags absent from the Docker image store, and uses the tag-only development image-set overlay for generated stacks. It does not refresh an existing tag. Run `./setup.sh` (or `make pull-images`) when you deliberately want the approved remote images refreshed; use `IMAGE_MODE=production` to test the immutable release pins.

To run a dashboard backend or frontend you built locally from a
TEVV-Web-Dashboard branch, put `DASHBOARD_BACKEND_IMAGE=` /
`DASHBOARD_FRONTEND_IMAGE=` in `./.env`: a key set there (or in the shell) is
never overridden by the generated image env files, and `pull_policy: missing`
keeps a local tag.

## What `make dashboard` does, in order

```
make dashboard
  -> ensure-images           tools/ensure-images.sh: local-first pull of the channel's refs
  -> ensure-demo-packs       bring installed packs to the lock's versions (install-demo-packs.sh --missing --sync)
  -> stage-authoring-packs   refresh ScenarioLab's view of the pack store
  -> load-images-env         export the generated env files, without clobbering overrides
  -> docker compose up       docker-compose-dashboard.yml, with MNS_IMAGE_SET_FILE set
  -> ros2-tools              recreated only if the bridge image changed
```

### 1. `ensure-images` — pull only what is missing

`tools/ensure-images.sh` walks every ref the selected channel declares and asks
`docker image inspect`. Present is kept and reported `LOCAL`; absent is pulled,
with three retries. `channel: local` rows are checked for presence and **never
pulled** — a missing one is a build step you have to run, and the error names
the row so the catalog can say what builds it.

This never refreshes a tag that already exists locally. That is deliberate:
it is what lets you iterate on a locally built image without a dashboard start
silently replacing it. `./setup.sh` / `make pull-images` is the operation that refreshes.

```bash
tools/ensure-images.sh --dry-run --development   # LOCAL / MISSING per ref
```

### 2. `IMAGE_MODE` picks which generated files are loaded

| `IMAGE_MODE` | Env files loaded, in order | `MNS_IMAGE_SET_FILE` handed to the backend |
| --- | --- | --- |
| `development` (default) | the channel's env (`images/v1.0.0.generated.env`), then `images/development.generated.env` | `images/image-set.development.generated.yaml` — tag-only refs, so a local build with the same tag wins |
| `production` | the channel's env, then `images/platform-images.generated.env` | `images/image-set.generated.yaml` — exact `repo:tag@digest` pins |

`tools/load-images-env.sh` exports each `KEY=VAL` from those files **only when
the key is unset in the shell and absent from `./.env`**. So the precedence,
highest first, is: shell environment, `./.env`, then the generated files in the
order above (the first file to set a key wins). That is the whole mechanism
behind running a locally built dashboard: put

```
DASHBOARD_BACKEND_IMAGE=local/tevv-web-dashboard-backend:v2-dev
DASHBOARD_FRONTEND_IMAGE=local/tevv-web-dashboard-frontend:v2-dev
```

in `./.env`, and nothing generated can override it. `tools/images.sh status`
lists such overrides under FYI so they are not forgotten.

### 3. The dashboard's own services

Every dashboard service reads its image from a required variable and pulls
under one policy:

```yaml
image: ${DASHBOARD_BACKEND_IMAGE:?add --env-file images/v1.0.0.generated.env, or use make dashboard}
pull_policy: ${DASHBOARD_PULL_POLICY:-missing}
```

`missing` is why a local tag is kept. Set `DASHBOARD_PULL_POLICY=always` for a
deliberate registry re-check of the dashboard itself; it does not affect
generated stacks.

### 4. What the backend hands to generated stacks

The backend runs in **distribution mode**: no platform checkout. It runs
`${MNS_STACKS_IMAGE}` through the Docker socket, against this repository
mounted at its own host path: `mns-stacks generate` (no socket,
`--network=none`) writes `generated/<name>/`, and `mns-stacks run/stop/status`,
`record` and `campaign` drive it. `make fly` and `make campaign` run the same
image the same way (`tools/mns-stacks.sh`). These variables travel from the
generated env files, through compose, into the backend, and on into
mns-stacks:

| Variable | Default in compose | Read by |
| --- | --- | --- |
| `MNS_STACKS_IMAGE` | empty; the only source (nothing is baked, see below) | the backend, to generate, run, record and fly campaigns |
| `MNS_PACKS_IMAGE` | empty; the only source (the pack lock's pin for the installer) | the backend's pack status, and pack install and staging (`tools/install-demo-packs.sh`, `mns-packs`) |
| `MNS_AUTHORING_IMAGE` | empty; the only source | the backend, to launch ScenarioLab |
| `MNS_CAPABILITY_KIT`, `MNS_AUTHORING_PROJECT` | empty | optional overrides: a local kit folder instead of the pinned host's kit, and a ScenarioLab project for the preflight's graded host check |
| `MNS_ROS2_BRIDGE_IMAGE` → `AIRSIM_BRIDGE_IMAGE` | **required** — the error names the env file to source | the backend, for the `ros2-tools` container |
| `MNS_IMAGE_SET` | `v1` | mns-stacks: which `image_sets` entry a stack resolves roles from |
| `MNS_IMAGE_SET_FILE` | the production overlay; `make dashboard` overrides per `IMAGE_MODE`, with the image overrides applied (below) | mns-stacks: which rendered file holds that entry |
| `MNS_IMAGE_PULL_POLICY` | `missing` | see the next section |

A generated stack's images come from `MNS_IMAGE_SET_FILE` alone; mns-stacks
reads no `*_IMAGE` variable. So that an override still reaches the stacks,
`make dashboard` (and `tools/mns-stacks.sh`, for `make fly` and `make campaign`)
passes a copy of the selected file with the overrides applied,
`.mns/image-set.effective.yaml`, whenever the shell or `./.env` sets
`MNS_RUNTIME_HOST_IMAGE` or `MNS_ROS2_BRIDGE_IMAGE` to something other than its
pin. Each override prints a NOTE naming the image-set slot it replaced
(`tools/images.sh effective-image-set`). Without an override the selected file
is passed as it is. The mapping comes from `images/catalog.yaml`: a release
channel variable owns every slot of its channel's image set that holds the same
catalog key. The other slots (autopilots, QGroundControl, sim-real-eval, the
VIO estimator) have no variable; point `MNS_IMAGE_SET_FILE` at your own file to
change them. The backend also reads `MNS_RUNTIME_HOST_IMAGE` directly, for the
Content phase's report.

### 5. `MNS_IMAGE_PULL_POLICY` — an override, not a setting

Nothing in the backend or `mns-stacks generate` reads `MNS_IMAGE_PULL_POLICY` as
configuration. `mns-stacks generate` writes a stack's policy into
`generated/<name>/.env` from the image set's own `pull_policy` (both rendered
sets declare `missing`). The compose line

```yaml
- MNS_IMAGE_PULL_POLICY=${MNS_IMAGE_PULL_POLICY:-missing}
```

matters for a different reason: it becomes the backend's **shell environment**,
which it forwards to the Compose that brings a generated stack up, and a shell
variable outranks that stack's `.env`. So this value silently overrides every
stack the dashboard launches. The default `missing` agrees with what the
`mns-stacks generate` wrote, so normally nothing changes hands.

**The trap:** export `MNS_IMAGE_PULL_POLICY=always` before `make dashboard`
and every backend-launched stack re-pulls. Any `channel: local` image is then
fetched from a registry that has never heard of it, and the whole stack comes
down after compose has already pulled everything else. Generated stacks used to
default to `always`, which is why older notes tell you to `sed` the `.env`;
that is no longer needed, and the edit reverts on regeneration anyway.

### 6. Credentials: the socket alone is not enough

The backend mounts `${DOCKER_CONFIG:-$HOME/.docker}` at `/root/.docker`,
read-only. When it runs `docker run <mns-stacks>` or brings a stack up, the
pull uses **this container's** Docker config, not the host shell's login.
Without the mount, the pull of the private `dhdevspace/auto_mns` images is
anonymous and `/api/scenario/generate` fails with `422 generation failed`; a
stack whose images are not all local fails the same way. The mns-stacks
siblings (from the backend and from `tools/mns-stacks.sh`) run as the host user,
with the socket's group and this config read-only at `/tmp/.docker`
(`DOCKER_CONFIG`), for the commands that drive containers. Check it is there:

```bash
docker inspect airsim-dashboard-api --format '{{range .Mounts}}{{.Destination}}{{"\n"}}{{end}}' | grep docker/config
# expect: /root/.docker/config.json
```

### 7. No baked defaults

The backend image carries no image pins (since TEVV-Web-Dashboard#122). It
used to bake the mns-stacks and authoring refs, which made every rebuild of
either force a dashboard backend rebuild. Now `MNS_STACKS_IMAGE`,
`MNS_PACKS_IMAGE` and `MNS_AUTHORING_IMAGE` from the channel env file are the
only source, so rebuilding one of those images is a repin here (`bump` and
`sync`) and nothing more. If one is unset, the dashboard still starts, and the
action that needs it (generate or launch, pack status, the editor) fails with
a message naming the variable and `images/v1.0.0.generated.env`.
`v1_dashboard_backend` declares no `bakes:`, so `tools/images.sh baked` has
nothing to check.

### 8. `ros2-tools`

The backend creates `ros2-tools` **outside compose**, from
`MNS_ROS2_BRIDGE_IMAGE`. It serves the Foxglove websocket Lichtblick renders
from and plays bags back, so `make dashboard` recreates it **only when the
selected bridge image differs** from the running one — removing it
unconditionally dropped the viewer's connection every time the target was
re-run. (Recording no longer uses it: `mns-stacks record` records inside the
stack's bridge container.) `RECREATE_ROS2_TOOLS=always` restores the old
behaviour; `=never` leaves it alone.

## Checking what the dashboard is actually running

```bash
docker inspect -f '{{.Config.Image}}' airsim-dashboard-api        # which backend
docker inspect -f '{{.Config.Image}}' ros2-tools                  # which bridge
tools/ensure-images.sh --dry-run --development
tools/images.sh status                                            # overrides under FYI, drift under NEEDS YOU
tools/images.sh baked                                             # baked refs vs the catalog (none in v1.0.0)
make doctor                                                       # every channel ref present?
```

## Related

- [Changing a container image](images.md) — the catalog, `sync`, `verify`, channels.
- [ADR 0002](adr/0002-one-image-catalog.md) — why one catalog.
- `docker-compose-dashboard.yml` — the header comment is the authoritative
  statement of the precedence order and the identical-path mount.
- TEVV-Airsim docs, *Deployment → How the Product Pulls Images* — the same
  story from the image-build side.
