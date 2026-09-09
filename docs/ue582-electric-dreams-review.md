# UE 5.8.2 Electric Dreams review record (2026-09-09)

This is a **local candidate verification record, not a release receipt or a successful full-flow certification**.
The review combines owner changes in TEVV-Authoring and TEVV-Airsim with the
runtime-stack candidate gate (#57). Nothing in this record changes production
image pins, publishes licensed content, or permits a compatibility bypass.

## Exact images used

All nine tags below were selected locally. Image IDs are Docker config/image
IDs, **not registry manifest digests**; do not put them after an @ in a pull
reference. These candidates were not pushed. A reviewer on another machine
must build the owning sources with authorized content, or obtain an authorized
copy of the artifacts. Do not silently substitute the newer published images
used by runtime-stack #58: that is a different test set.

| Role | Local tag (under dhdevspace/auto_mns unless stated) | Docker image ID (sha256) |
| --- | --- | --- |
| Runtime host | tevv-runtime-host-ue582-substrate-local.1 | 29bb0dcadb6228a193d4f4620ca1a7613d04f62401568f6c5cdce5b3bc5f9ea7 |
| Authoring | mns-authoring-ue582-substrate-local.1 | 4aab37216b998e41568a3f60ec5b1352c222970549ec6535504817d0f942c254 |
| Product shell | mns-product-shell-ue582-plugins-local.1 | ad9558dc1f253753f68e2a0178f57672b499769d99a446cd0999479dc3ae9624 |
| Stack generator | mns-stack-generator-ue582-plugins-local.1 | 9b0045172a60787fc0db88df9dec09cc4af60766ad682366eba957f05e9c625c |
| Dashboard backend | tevv-web-dashboard-backend-ue582-local.2 | 0d61f0478b5f2bd30be483bd1a308fa1e4eae320f8a3465841b84951fda3df04 |
| Dashboard frontend | tevv-web-dashboard-frontend-ue582-local.1 | f4e08ccddf70ced9fb5c850c04dff68aecfc4d8f7aa81fca989d7676fbd790b5 |
| ROS 2 bridge | local/tevv-airsim-ros2-bridge-humble:ue582-review.1-07a5da0 (full reference) | 86a4fa972d73a017f5c6eb0b2f91ff14d3705400a268b3305c862ff0831598f4 |
| ArduPilot | ardupilot-20260908.1 | aa77cd67258cb5f11c17d327cc78833eb959145e4c54778313117c2b0c645920 |
| PX4 | px4-airsim-px4-ue582-review.1-routing-36f7634 | 59aaa3a3e992317ed92de69ad7fc2b5221faaf97a79d6b426be07b2e1af605cc |

The support-image selections were:
- QGC: `dhdevspace/auto_mns:airsim-qgc-x11-latest@sha256:fe1e9e05d9300afeab92787973b84c378cf021c25f0f61b8f27cbf712e299804`
- Evaluation: `dhdevspace/auto_mns:sim-real-eval-worker-latest@sha256:4159d4c63f747dbdfc1ff86dfcbcbde87685c014b7f6fc9eb18478ddaa20c7eb`
- Lichtblick: `ghcr.io/lichtblick-suite/lichtblick:latest@sha256:b469986d523b06500a04445933fde4b96ca3bd3def918844e3af47362106c75f`
- TimescaleDB: `timescale/timescaledb:2.17.2-pg16@sha256:4e459e217f00cbb09920c34d245501e63427e6767a495de57ce76823ff280f12`

These are historical test selections, not proposed production catalog entries.
The product shell/generator candidates refreshed their embedded frozen contracts
from earlier local candidates; they were not complete fresh source rebuilds in
the Substrate verification run.

## Source and artifact provenance

The native-tested working-tree changes are preserved in:
- TEVV-Authoring commit `90b2475` (parent `79f1a67`).
- TEVV-Airsim commit `e00907d00` (parent `d17cff8ae`).

Images were built before those changes were committed. Their original revision
labels name the parents, not the later integrated PR heads. PR conflict-resolution
merges include newer upstream code and have focused test coverage, **not another
full native rebuild**. Reviewers must rebuild current PR heads before release.

Engine: official Linux UE **5.8.2 CL56702186**, Development, Vulkan SM6, IoStore.
Capability ID: `ue-5.8.2-cl56702186-linux-development-vulkan-sm6-iostore-v2`.
Base release: `tevv-runtime-host-ue-5.8.2-substrate-base-v3`.

Test pack: `electric_dreams@1.0.4`.
Artifact digest: `sha256:925e8ec08703114ad2bc0179e3970b92bd49fc80ceee2b1507e5fc099d337d69`.
Entry map: `/MnsEnv_ElectricDreams_1_0_3/Levels/ElectricDreams_Env`.
The existing plugin namespace was retained while the immutable distribution
version advanced; the old archive was not overwritten. The ~12 GB pack, native
cook kit, logs, source sample and licensed content are not part of Git changes.

## Observed results and remaining gates

Passed on the test set above:
- Runtime native compile, base full cook, packaging audit, Docker build and engine-init smoke.
- Authoring native compile/cook/stage and Docker build.
- Both packaged executables reported `r.Substrate=1`, `r.VirtualTextures=1`,
  `r.Substrate.ProjectGBufferFormat=1`, `r.Substrate.BytesPerPixel=170`, from ProjectSetting.
- Fresh relocated portable cook kit and Electric Dreams DLC cook: zero errors, 40 warnings.
  The earlier puddle material shader compilation errors did not recur.
- Archive assembly and declared-host validation, installation and candidate preflight.
- Actual browser Launch editor returned HTTP 200 and selected the new authoring image.
- Authoring logged `MNS_ENVIRONMENT_READY id=electric_dreams version=1.0.4`.

Not passed / not established:
- GPU/offscreen runtime pack-load reproducibly exited **139**, OOMKilled=false, after
  environment readiness: `Generic pack host requires exactly one PlayerStart tagged
  MnSScenarioOrigin; found 0.` This requires a normal map/project-policy audit fix
  and recook. Do not remove the guard or fabricate an origin in the loader.
- The current generic audit did not enforce that runtime-specific origin requirement.
- Full browser → authoring export → generated runtime acceptance has **not passed**.
- The test library lacked a compatible vehicle pack. The older available vehicle
  pack declares AirSim 2.0.0, while authoring uses a content-preview plugin; do not
  declare them compatible by copying the runtime contract into authoring.
- Final visual color/material correctness has not been conclusively verified;
  the captured camera was inside/near geometry. No audio verification was performed.
- Renderer settings are not encoded in the capability ID. Keep this image/pack
  set together; the shared ID alone does not prove compatibility with older
  non-Substrate cooks. This remains a release-contract follow-up.
- The copied legacy `r.Substrate.AccurateSRGB` key is a deferred dummy variable
  in this engine, not a verified effective setting.

## Reviewer procedure

### 1. Review and run narrow tests

In TEVV-Authoring, check out the portable-kit follow-up over #15:
```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tooling/content_packs/tests -q
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tooling/asset_packs/tests -q
bash -n MnSPackaging/install.sh
```
Review the environment-tree parent/child selection tests, source-preserving
legacy migration probe, installer path/symlink validation, early immutable pack
identity validation, and generated authoring-contract identity.

In TEVV-Airsim, check out the Substrate host follow-up over #129:
```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_freeze_host_compatibility tests.test_runtime_build_script tests.test_image_build -q
```

In this runtime-stack checkout:
```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tools -p 'test_check_ue_candidate.py'
bash -n tools/stage-authoring-packs.sh
```

### 2. Build owner artifacts (expensive, opt-in reviewer action)

Use the exact engine and authorized UDS/SDK/plugin inputs. Follow owner assembly
and packaging prerequisites; an empty checkout is not a complete licensed build
workspace. In the runtime owner, the normal project hook is:
```bash
UE_ROOT=/path/to/Linux_Unreal_Engine_5.8.2 \
DLC_RELEASE_VERSION=tevv-runtime-host-ue-5.8.2-substrate-review-base \
UE_BUILD_MAX_PARALLEL_ACTIONS=4 FULL_COOK=1 \
bash projects/TEVVRuntimeHost/package-ue582.sh \
  --tag tevv-runtime-host-ue582-substrate-review.2 --exact-tag
```
Choose a new review tag/base release, never overwrite an existing immutable
artifact. Freeze the authoring contract against the authoring project's actual
assembled plugins using the owner freezer; do not pass the runtime's AirSim
inventory as an authoring inventory. Build ScenarioLab with
`ScenarioLab/scripts/build_authoring_app_image.sh --image <new-review-image> --host-contract <frozen-authoring-contract>`.

Export a new kit using TEVV-Authoring's
`tooling/content_packs/export_compatibility_kit.py` and the actual built runtime
cook host, release root, frozen contracts and project policy.
See `MnSPackaging/COMPATIBILITY_KITS.md` for required inputs and relocation tests.
Install through `MnSPackaging/install.sh`; check attached actors do not appear
under a spurious literal None folder and that child selection survives refresh.
Cook the source level normally, then run `tooling/content_packs/pack.py assemble`
against both actual host contracts. A successful cook alone is not a distributable
archive or runtime validation.

### 3. Start from the browser

Create a local candidate lock per [the candidate guide](ue582-candidate.md)
using exact image IDs and the installed pack receipt. Include all required support
images, not only the Unreal images. Use a separate workspace/store from production.
Run the candidate gate without launch first, then deliberately start the dashboard:
```bash
python3 tools/check_ue_candidate.py \
  --engine-root /path/to/Linux_Unreal_Engine_5.8.2 \
  --lock /path/to/review.lock.json --local-images
python3 tools/check_ue_candidate.py \
  --engine-root /path/to/Linux_Unreal_Engine_5.8.2 \
  --lock /path/to/review.lock.json --local-images --start-dashboard
```
The original machine-local record is
`/tmp/mns-electric-web-e2e.nkaUOi/substrate.lock.json`, with workspace
`/tmp/mns-electric-web-e2e.nkaUOi/workspace`. These are not portable release inputs.

Open http://localhost:3001. Launch ScenarioLab, select Electric Dreams, move the
camera to a clear exterior view, and inspect rocks/leaves/puddles with UDS active.
Check actual running image IDs and logs, not just the catalog labels.
For full acceptance, install a genuinely compatible vehicle/object pack, author a
scenario, export ScenarioSpec, generate from that export and launch the generic
runtime. Confirm pack/version/digest selection, a valid MnSScenarioOrigin, RPC/ROS
readiness and sensor output. Capture screenshots and logs. Stop only the review
stack afterward. At present, record the known origin failure rather than claiming
the final steps pass.
