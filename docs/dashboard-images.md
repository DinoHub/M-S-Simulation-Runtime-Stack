# How the dashboard gets its images

`make dashboard` touches two different sets of images, and confusing them is
the source of most "it pulled the wrong thing" reports:

1. **The dashboard's own services** — backend, frontend, Lichtblick, the
   optional TimescaleDB. Compose starts these directly.
2. **The images the backend hands to generated stacks** — the stack generator,
   the authoring app, the ROS 2 bridge, and the whole image set a generated
   stack resolves its runtime host, autopilot and estimator from. The backend
   never starts these itself; it runs the generator, which writes their pins
   into `generated/<name>/.env`, and then runs `docker compose up` on that.

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

By default, `make dashboard` runs the transitional development workflow: it keeps any locally built matching image tags, pulls only tags absent from the Docker image store, and uses the tag-only development image-set overlay for generated stacks. It does not refresh an existing tag. Run `./product.sh setup` when you deliberately want the approved remote images refreshed; use `IMAGE_MODE=production` to test the immutable release pins.

To run a dashboard backend or frontend you built locally from a
TEVV-Web-Dashboard branch, put `DASHBOARD_BACKEND_IMAGE=` /
`DASHBOARD_FRONTEND_IMAGE=` in `./.env`: a key set there (or in the shell) is
never overridden by the generated image env files, and `pull_policy: missing`
keeps a local tag.

## What `make dashboard` does, in order

```
make dashboard
  -> ensure-images           tools/ensure-images.sh: local-first pull of the channel's refs
  -> ensure-demo-packs       install missing packs through the product-shell image
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
silently replacing it. `./product.sh setup` is the operation that refreshes.

```bash
tools/ensure-images.sh --dry-run --development --channel standalone_v2_ue582   # LOCAL / MISSING per ref
```

### 2. `IMAGE_MODE` picks which generated files are loaded

| `IMAGE_MODE` | Env files loaded, in order | `MNS_IMAGE_SET_FILE` handed to the backend |
| --- | --- | --- |
| `development` (default) | the channel's development env, then `images/standalone-v2-development.generated.env` | `images/image-set.development.generated.yaml` — tag-only refs, so a local build with the same tag wins |
| `production` | the channel's env, then `product-images.env`, then `images/platform-images.generated.env` | `images/image-set.generated.yaml` — exact `repo:tag@digest` pins |

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
image: ${DASHBOARD_BACKEND_IMAGE:?source product-images.env}
pull_policy: ${DASHBOARD_PULL_POLICY:-missing}
```

`missing` is why a local tag is kept. Set `DASHBOARD_PULL_POLICY=always` for a
deliberate registry re-check of the dashboard itself; it does not affect
generated stacks.

### 4. What the backend hands to generated stacks

The backend runs in **distribution mode**: no platform checkout. It runs
`${MNS_STACK_GENERATOR_IMAGE}` through the Docker socket, against this
repository mounted at its own host path, and the generator writes
`generated/<name>/`. These variables travel from the generated env files,
through compose, into the backend, and on into the generator:

| Variable | Default in compose | Read by |
| --- | --- | --- |
| `MNS_STACK_GENERATOR_IMAGE` | empty (baked default, see below) | the backend, to run the generator |
| `MNS_AUTHORING_IMAGE` | empty (baked default) | the backend, to launch ScenarioLab |
| `MNS_ROS2_BRIDGE_IMAGE` → `AIRSIM_BRIDGE_IMAGE` | **required** — the error names the env file to source | the backend, for the `ros2-tools` container |
| `MNS_PRODUCT_SHELL_IMAGE` | empty (the pack lock's pin) | pack install and staging |
| `MNS_IMAGE_SET` | `ue582` (`CHANNEL=v2` selects `published`) | the generator: which `image_sets` entry a stack resolves roles from |
| `MNS_IMAGE_SET_FILE` | the production overlay; `make dashboard` overrides per `IMAGE_MODE` | the generator: which rendered file holds that entry |
| `MNS_IMAGE_PULL_POLICY` | `missing` | see the next section |

A generated stack's runtime host therefore comes from `MNS_IMAGE_SET_FILE`,
**not** from `MNS_RUNTIME_HOST_IMAGE` — that variable is passed for the
product shell's benefit and the compose file says so.

### 5. `MNS_IMAGE_PULL_POLICY` — an override, not a setting

Nothing in the backend or the generator reads `MNS_IMAGE_PULL_POLICY` as
configuration. The generator writes a stack's policy into
`generated/<name>/.env` from the image set's own `pull_policy` (both rendered
sets declare `missing`). The compose line

```yaml
- MNS_IMAGE_PULL_POLICY=${MNS_IMAGE_PULL_POLICY:-missing}
```

matters for a different reason: it becomes the backend's **shell environment**,
and when the backend runs `docker compose up` on a generated stack, a shell
variable outranks that stack's `.env`. So this value silently overrides every
stack the dashboard launches. The default `missing` agrees with what the
generator wrote, so normally nothing changes hands.

**The trap:** export `MNS_IMAGE_PULL_POLICY=always` before `make dashboard`
and every backend-launched stack re-pulls. Any `channel: local` image is then
fetched from a registry that has never heard of it, and the whole stack comes
down after compose has already pulled everything else. Generated stacks used to
default to `always`, which is why older notes tell you to `sed` the `.env`;
that is no longer needed, and the edit reverts on regeneration anyway.

### 6. Credentials: the socket alone is not enough

The backend mounts `${DOCKER_CONFIG:-$HOME/.docker}` at `/root/.docker`,
read-only. When it runs `docker run <generator>` or `docker compose up`, the
pull uses **this container's** Docker config, not the host shell's login.
Without the mount, the pull of the private `dhdevspace/auto_mns` generator is
anonymous and `/api/scenario/generate` fails with `422 generation failed`; a
stack whose images are not all local fails the same way. Check it is there:

```bash
docker inspect airsim-dashboard-api --format '{{range .Mounts}}{{.Destination}}{{"\n"}}{{end}}' | grep docker/config
# expect: /root/.docker/config.json
```

### 7. Baked defaults

The backend image carries `MNS_AUTHORING_IMAGE_DEFAULT` and the generator
equivalent *inside the built image*. The env lines above are overrides; an
image-only deploy with none of them set uses the baked values. No `sync` can
fix a stale baked default — it needs a backend rebuild and `tools/images.sh
bump --only dashboard_backend`. `tools/images.sh baked` (needs docker) reports
when the released backend's baked refs no longer match the catalog. Details in
[Changing a container image](images.md#three-things-the-catalog-does-not-control).

### 8. `ros2-tools`

The backend creates `ros2-tools` **outside compose**, from
`MNS_ROS2_BRIDGE_IMAGE`. It serves the Foxglove websocket Lichtblick renders
from and is what bag recording execs into, so `make dashboard` recreates it
**only when the selected bridge image differs** from the running one —
removing it unconditionally used to destroy an in-progress recording every
time the target was re-run. `RECREATE_ROS2_TOOLS=always` restores the old
behaviour; `=never` leaves it alone.

## Checking what the dashboard is actually running

```bash
docker inspect -f '{{.Config.Image}}' airsim-dashboard-api        # which backend
docker inspect -f '{{.Config.Image}}' ros2-tools                  # which bridge
tools/ensure-images.sh --dry-run --development --channel standalone_v2_ue582
tools/images.sh status                                            # overrides under FYI, drift under NEEDS YOU
tools/images.sh baked                                             # baked defaults vs the catalog
./product.sh doctor                                               # every channel ref present?
```

## Related

- [Changing a container image](images.md) — the catalog, `sync`, `verify`, channels.
- [ADR 0002](adr/0002-one-image-catalog.md) — why one catalog.
- `docker-compose-dashboard.yml` — the header comment is the authoritative
  statement of the precedence order and the identical-path mount.
- TEVV-Airsim docs, *Deployment → How the Product Pulls Images* — the same
  story from the image-build side.
