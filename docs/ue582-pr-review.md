# Cross-repository PR conflict and dependency review — 2026-09-09

Scope: all 25 initially open PRs across the 11 platform/service/contract/product
repositories, plus the two feature follow-ups created during review (27 total).
This is a merge-conflict/dependency review with focused regression checks, not
an exhaustive correctness audit or approval of every PR. No PR was merged,
no branch history was force-pushed, and no image was published.

See [Electric Dreams image evidence and reviewer procedure](ue582-electric-dreams-review.md).
New feature PRs are draft because full native acceptance has not passed.
GitHub CI below is a snapshot, not a promise that queued checks will succeed.

## Inventory

| Repository / PR | Base | Merge status | CI snapshot | Dependency / action |
| --- | --- | --- | --- | --- |
| [MnS-Integration-Platform #93](https://github.com/DinoHub/MnS-Integration-Platform/pull/93) | `main` | Clean | No checks reported | Independent launcher fix; same host-ID does not guarantee plugin compatibility. No textual overlap with #92. |
| [MnS-Integration-Platform #92](https://github.com/DinoHub/MnS-Integration-Platform/pull/92) | `main` | Clean | Reported checks pass; some may be skipped | CI depends on the Authoring checkout pinned in services/manifest.yaml and SERVICES_READ_TOKEN; otherwise explicitly partial coverage. |
| [TEVV-Authoring #18](https://github.com/DinoHub/TEVV-Authoring/pull/18) | `chore/ue5.8-compat` | Clean | No checks reported | New follow-up: #15 → #18; pair with AirSim #148. Native rebuild/visual/E2E gates remain. |
| [TEVV-Authoring #17](https://github.com/DinoHub/TEVV-Authoring/pull/17) | `main` | Clean | Reported checks pass; some may be skipped | Independent Python CI addition; no textual conflict with #15/#18. |
| [TEVV-Authoring #15](https://github.com/DinoHub/TEVV-Authoring/pull/15) | `main` | Clean | No checks reported | Prerequisite for #18; coordinated with AirSim #129. Review image/evidence section updated. |
| [TEVV-Airsim #148](https://github.com/DinoHub/TEVV-Airsim/pull/148) | `feat/ue5.8-migration` | Clean | Pending | New follow-up: #129 → #148; pair with Authoring #18 and recooked packs. |
| [TEVV-Airsim #147](https://github.com/DinoHub/TEVV-Airsim/pull/147) | `docs/ros2-reference` | Clean | Pending | Resolved mkdocs conflicts in c4e0c756f; retain reorganized nav and removed legacy pages. #143 → #144 → #146 → #147. |
| [TEVV-Airsim #146](https://github.com/DinoHub/TEVV-Airsim/pull/146) | `docs/site-restructure` | Clean | Failures | Stacked after #144; consumes ROS bridge reference docs. Failing checks are not a merge-conflict result. |
| [TEVV-Airsim #144](https://github.com/DinoHub/TEVV-Airsim/pull/144) | `docs/vio-integration` | Clean | Failures | Stacked after #143; do not merge independently against main without restacking. |
| [TEVV-Airsim #143](https://github.com/DinoHub/TEVV-Airsim/pull/143) | `main` | Clean | Failures | First PR in the AirSim docs chain; syncs ROS bridge docs, including the VIO/reference work. |
| [TEVV-Airsim #139](https://github.com/DinoHub/TEVV-Airsim/pull/139) | `feat/ue5.8-migration` | Conflict | Failures | UNRESOLVED semantic choice: pinned third-party McpAutomationBridge vs Xfs's built-in ModelContextProtocol/EditorToolset/PCGToolset. User decision requested; no forced selection. |
| [TEVV-Airsim #129](https://github.com/DinoHub/TEVV-Airsim/pull/129) | `main` | Clean | Failures + pending | Resolved main conflict in 4a8e65cb0; runner-scoped cache retains tooling/build inputs. |
| [TEVV-Airsim #95](https://github.com/DinoHub/TEVV-Airsim/pull/95) | `main` | Clean | Pending | Restacked onto main in 22bfefac5; old parent #81 and cited #94 already merged. Three conflicts resolved; 55 tests passed. |
| TEVV-Content-Pack-SDK | — | No open PRs | — | No new feature-owned source changes to submit. |
| [MnS-ScenarioSpec #3](https://github.com/DinoHub/MnS-ScenarioSpec/pull/3) | `main` | Clean | Reported checks pass; some may be skipped | Immutable pack-reference contract; textually compatible with #2. No new contract revision added by Substrate follow-ups. |
| [MnS-ScenarioSpec #2](https://github.com/DinoHub/MnS-ScenarioSpec/pull/2) | `main` | Clean | Reported checks pass; some may be skipped | Named-list include loader fix; independent of #3's schema changes. |
| [M-S-Simulation-Runtime-Stack #59](https://github.com/DinoHub/M-S-Simulation-Runtime-Stack/pull/59) | `main` | Clean | Reported checks pass; some may be skipped | Independent drift-watch credential change; no conflict with candidate/onboarding work. Requires configured registry credentials. |
| [M-S-Simulation-Runtime-Stack #58](https://github.com/DinoHub/M-S-Simulation-Runtime-Stack/pull/58) | `upgrade/ue582-runtime-candidates` | GitHub recalculating; local merge-tree clean | Reported checks pass; some may be skipped | Stacked after #57; paired with Dashboard #104. Its published/default-channel images are NOT the local Substrate image set. |
| [M-S-Simulation-Runtime-Stack #57](https://github.com/DinoHub/M-S-Simulation-Runtime-Stack/pull/57) | `main` | Clean | Reported checks pass; some may be skipped | Candidate gate plus committed local review evidence; prerequisite for #58. |
| [M-S-Simulation-Runtime-Stack #56](https://github.com/DinoHub/M-S-Simulation-Runtime-Stack/pull/56) | `main` | Clean | Reported checks pass; some may be skipped | Independent immutable airsim-tools pin; tested prospective merge with #58 is clean. |
| [TEVV-Web-Dashboard #104](https://github.com/DinoHub/TEVV-Web-Dashboard/pull/104) | `main` | Clean | No checks reported | Pack onboarding paired with runtime-stack #58. Now prerequisite for #103. |
| [TEVV-Web-Dashboard #103](https://github.com/DinoHub/TEVV-Web-Dashboard/pull/103) | `feat/v2-pack-store-passthrough` | Clean | No checks reported | Resolved prospective conflict with #104 in 6d2cd01; retargeted onto #104. Preserves forwarding and adds read-only external-input mounts. 52 backend tests pass, 2 root-only skips; 4 publication tests pass. |
| [TEVV-Airsim-ROS2-Bridge #51](https://github.com/DinoHub/TEVV-Airsim-ROS2-Bridge/pull/51) | `main` | Clean | Failures | CI runner split; no textual conflict with #45/#46. Build failures still need triage. |
| [TEVV-Airsim-ROS2-Bridge #48](https://github.com/DinoHub/TEVV-Airsim-ROS2-Bridge/pull/48) | `docs/vio-integration` | Clean | Failures | Stacked after #46; ROS reference content is consumed by AirSim #146. |
| [TEVV-Airsim-ROS2-Bridge #46](https://github.com/DinoHub/TEVV-Airsim-ROS2-Bridge/pull/46) | `main` | Clean | Failures | VIO configuration/docs; parent of #48, consumed by AirSim's docs chain. |
| [TEVV-Airsim-ROS2-Bridge #45](https://github.com/DinoHub/TEVV-Airsim-ROS2-Bridge/pull/45) | `main` | Clean | Failures | Depends on AirSim #129, pins 8353009cfe24d5dfe39f5d86fb1e1043b1604815. Rebuild vendored archives/provenance if changing that pin. |
| [TEVV-Ardupilot #8](https://github.com/DinoHub/TEVV-Ardupilot/pull/8) | `master` | Clean | No checks reported | Dedicated C2 endpoint; no open same-repo dependency. Product images must be rebuilt/pinned to consume source changes. |
| [TEVV-PX4-Autopilot #5](https://github.com/DinoHub/TEVV-PX4-Autopilot/pull/5) | `master` | Clean | No checks reported | Routing regression tests/noninteractive launch; no open same-repo dependency. Earlier local image source identity differs from current head. |
| TEVV-Metrics | — | No open PRs | — | No new feature-owned source changes to submit. |

## Required coordination

- Runtime: AirSim **#129 → #148**; authoring: **#15 → #18**. Build both actual
  consumer contracts and a new base/DLC set before treating Substrate as releasable.
- Browser/product: runtime-stack **#57 → #58**, Dashboard **#104 → #103**.
  #58 and #104 need each other's product interfaces. #103's integrated source has
  unit coverage but is newer than the recorded local dashboard image.
- Docs: AirSim **#143 → #144 → #146 → #147**, with ROS bridge **#46 → #48** supplying
  the source documents. Retarget children only after their parent is merged.
- ROS bridge #45 is an exact-source/vendor dependency, not merely an engine-version
  label. Do not update its gitlink without rebuilding the paired headers/library.
- AirSim #95 no longer depends on the obsolete open-PR base: #81/#94 are merged.
  Main's exact-tag packaging, Grafana embedding and vehicle/time filtering coexist
  with the PR's persistence, loopback binding and run-scoped queries after resolution.
- #139 remains blocked on an editor integration choice. Recommended: keep the
  third-party provisioner optional and preserve Xfs's current built-in MCP selection.
  Enabling both should be a deliberate decision with editor startup/port/security
  verification, not an automatic conflict resolution.

## Checks performed during this review

- Fresh remote refs and all open PR metadata/declarations; local `git merge-tree`
  against each declared base. Prospective overlap checks covered Dashboard
  #103/#104, runtime-stack #56/#58, ScenarioSpec #2/#3, and AirSim #129/#143.
- Authoring: 75 content-pack tests, 30 asset-pack tests, installer shell syntax.
- Runtime host: 12 focused compatibility/build/image tests.
- AirSim #129: 3 toolchain-marker tests.
- AirSim #147: 3 docgen tests; all mkdocs nav paths exist. A full strict site build
  is not claimed.
- Dashboard: 52 backend tests passed, 2 ownership tests skipped for non-root;
  4 isolated publication tests passed, shell syntax passed. No Docker runtime
  started to run these tests; dependencies were installed only in a temporary venv.
- Runtime-stack: 11 candidate tests and staging shell syntax; its verify CI passed.
- AirSim #95: 55 metrics tests with protobuf in the declared supported range;
  Compose configuration validation only (no services started); incremental diff check.
- New feature/incremental resolution diffs pass whitespace checks. Historical
  upstream CRLF/whitespace noise and existing LFS pointer inconsistencies were not
  swept into unrelated commits.

## Remaining acceptance blockers

Textual mergeability is not merge readiness. AirSim/ROS2 CI still includes failing
or queued jobs. No checks reported is not a passing CI run. The recorded Electric
Dreams GPU runtime test exits on missing `MnSScenarioOrigin`; compatible vehicle
content and final visual verification remain open. Renderer settings are not yet
part of the compatibility-ID contract. Source maps, packs, local locks, downloaded
licensed assets, unrelated worktrees and the live dashboard/editor were preserved.
