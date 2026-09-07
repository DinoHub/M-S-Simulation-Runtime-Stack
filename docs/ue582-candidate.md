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
`qgroundcontrol`, `sim_real_eval`, and `lichtblick` supporting images. They
belong to the tested candidate set; only Unreal-bearing images contain UE.

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
