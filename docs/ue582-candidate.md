# UE 5.8.2 browser acceptance

Run from the canonical `M-S-Simulation-Runtime-Stack` checkout. The removed
`M-S-Simulation-Runtime-Stack-standalone-v2` worktree is not a product dependency.

The acceptance chain is browser dashboard → packaged ScenarioLab → scenario
export → stack generator → generic runtime host with cooked level/object packs.
Packaging consumes declared dependencies; project-specific plugin, streaming,
and world implementation choices belong to the source project.

## Candidate inputs

Use an actual Linux UE 5.8.2 installation. `Engine/Build/Build.version` supplies
the changelist. ScenarioLab and TEVVRuntimeHost images must expose that exact
host ID in `tevv.content_packs.host_compatibility_id`; recook all selected packs
with the same frozen host/base release. Never relabel old cooked payloads.

Keep the previous release unchanged. Create a candidate lock using the existing
`mns.pack_release_lock.v1` shape, with real published digests and download
receipts. Its `required_images` must include `product_shell`, `authoring`,
`stack_generator`, `runtime_host`, `ros2_bridge`, `dashboard_backend`, and
`dashboard_frontend`. Launch additionally requires pinned `ardupilot`, `px4`,
`qgroundcontrol`, `sim_real_eval`, `lichtblick`, and `timescaledb` supporting
images. Compose interpolates the TimescaleDB image even when its optional
`db` profile is disabled, so omitting it blocks the dashboard before launch.
These images belong to the tested candidate set; only Unreal-bearing images
contain UE.

Install the chosen packs and pull the exact image digests before the check:

```bash
python3 tools/install_demo_packs.py --lock .mns/ue582.lock.json --all
python3 tools/check_ue_candidate.py \
  --engine-root /path/to/Linux_Unreal_Engine_5.8.2 \
  --lock .mns/ue582.lock.json --start-dashboard
```

The check rejects engine/host mismatches, unpinned images, missing or invalid
packs, and bad bundle receipts. Pack integrity uses the Authoring-owned
verifier inside the pinned product-shell image with read-only mounts and
network disabled. After verification, `--start-dashboard` stages packs,
writes an ignored candidate image overlay, and starts the dashboard with
explicit image selections that override stale `.env` values. No release
catalog or version is promoted. Without this flag, no dashboard is started.

### Local-only image set

Use `--local-images` when the reviewed 5.8.2 images are built on this host and
must not be published yet. Default mode is unchanged and still requires every
image to use a registry digest. Local mode permits a mix of those pins and
explicit, non-`latest` local tags, but the lock must also record the exact
Docker image ID for every role:

```json
{
  "schema": "mns.pack_release_lock.v1",
  "capability_id": "ue-5.8.2-cl56702186-linux-development-vulkan-sm6-iostore-v2",
  "required_images": {
    "product_shell": "dhdevspace/auto_mns:mns-product-shell-ue582-local.1",
    "authoring": "dhdevspace/auto_mns:mns-authoring-20260907.1",
    "ros2_bridge": "local/tevv-airsim-ros2-bridge-humble:ue582-review.1-07a5da0"
  },
  "required_image_ids": {
    "product_shell": "sha256:<64 lowercase hex>",
    "authoring": "sha256:<64 lowercase hex>",
    "ros2_bridge": "sha256:<64 lowercase hex>"
  }
}
```

The abbreviated example omits the other mandatory image roles and packs; in a
real lock, `required_image_ids` must have exactly the same keys as
`required_images`. Local tags may use the normal `dhdevspace/auto_mns` name or
a `local/` name, but must be unique in the lock and must not end in `latest`.
The gate compares every `docker image inspect` ID before pack verification or
launch, while retaining the exact engine/host-label and pack checks:

```bash
python3 tools/check_ue_candidate.py \
  --engine-root /path/to/Linux_Unreal_Engine_5.8.2 \
  --lock .mns/ue582-local.lock.json --local-images --start-dashboard
```

Local pack verification, the candidate image-set overlay, and dashboard launch
all use pull-never behavior. The launch writes the explicit selections and
their inspected IDs to ignored files under `.mns/ue-candidate/`, plus an
executable `.mns/ue-candidate/rerun.sh`. Use that script for subsequent starts:
it reruns the full engine, image-ID, host-label, and pack verification with the
same absolute engine/lock/store/workspace paths before launch. It does not use
the Makefile or released catalog path. The ignored environment file also
captures `DISPLAY` and `XAUTHORITY` when they were present for the successful
launch, and the rerun script exports those values before revalidation.

For a manual Compose invocation, export the generated values instead of merely
reading them into the shell:

```bash
set -a
source .mns/ue-candidate/local-images.env
set +a
docker compose -p m-s-simulation-runtime-stack \
  -f docker-compose-dashboard.yml up -d --pull never
```

Once the backend is healthy, verify its actual selections:

```bash
python3 tools/check_ue_candidate.py \
  --engine-root /path/to/Linux_Unreal_Engine_5.8.2 \
  --lock .mns/ue582.lock.json --dashboard-container airsim-dashboard-api
```

## Runtime gate

The preflight deliberately reports `e2e_verified: false`. Open
`http://localhost:3001`, launch ScenarioLab, load a level and object packs,
change a scenario, export it, generate and launch its stack. Record the
generated manifest, actual running image IDs, pack/variant digests, RGB and
lidar samples, ROS2 domains, conditions and object placement, MCP automation,
and cleanup results. Repeat across the four catalog levels and their supported
autopilot/multi-vehicle flows. Include sky/depth/segmentation and asynchronous
sensor shutdown regressions from the owning services.

The legacy `tests/full-product-e2e` runner is not evidence of this browser
acceptance. Unit tests and a successful catalog response are prerequisites
only. Until the real 5.8.2 build, cook, and runtime gates succeed, candidate
PRs remain open with their missing evidence stated explicitly.

Focused checks (no Unreal workload):

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tools -p 'test_check_ue_candidate.py'
```
