# Where the AirSim ROS 2 bridge sits in the TEVV platform, and what it owes it

Markdown version of the mapping page (2026-09-21). Specs: baseline 2026-07-10,
OSMO variant 2026-07-13, Jenkins CI plane 2026-08-18. Bridge repo state:
`TEVV-Airsim-ROS2-Bridge` origin/main @ PR #64, fixes on branch
`feat/tevv-platform-contract`.

## 1. The spec set

| Spec | Decides | Status |
|---|---|---|
| Baseline (2026-07-10) | Five planes: trigger, orchestration, run, data, presentation. Argo Workflows, namespace per run, Postgres run registry, MinIO + MCAP, Loki, Grafana, Foxglove. `omega.yaml` + `tevv.yaml` + `tevv-compile`. Tiers L0–L3. FAILED vs INFRA_FAILED invariant. | Rationale record |
| OSMO variant (2026-07-13) | Swap Argo for NVIDIA OSMO gang groups + KAI preemption. Shared namespace, per-run NetworkPolicy. Registry owns the attempt counter. `ignoreNonleadStatus: false`. Lead exit code = verdict. | Gated on a P1 spike |
| Jenkins CI plane (2026-08-18) | GitHub Actions → Jenkins in-cluster with a BuildKit sidecar. `ci.json` resolve contract. `unit_tests:` block in tevv.yaml. Monte Carlo `matrix.mode: sample`. Disk and image-GC rules because CI now shares nodes with sim. | Draft |

### Invariants that survive all three

- Contract surface every profile must present: `/clock`, `/run_state`,
  `/<vehicle>/ground_truth/odom`, `/<vehicle>/collision`, sensor topics per
  interface spec, the command surface.
- Sim time is authoritative. Lockstep mandatory. Wall clock only for phase
  budgets. Conductor watches RTF; collapse = INFRA_FAILED.
- Four interchangeability axes (`sim`, `autopilot`, `middleware`, `comms`),
  resolved at compile time; every combination passes a certification suite.
- `tevv.yaml` per component repo: image, entrypoint, provides/requires with
  QoS, resource profile, L0 tests. QoS mismatch = compile error.
- stdout-only logs, MCAP bags split by size with rolling upload, digest-pinned
  images from Harbor, no runtime internet.
- Unit tests never produce a verdict; `verifies:` inside `unit_tests` is a
  schema error.

## 2. Where the bridge sits

The spec's "existing assets" name `M-S-Simulation-Runtime-Stack`, not the
bridge. The bridge is "per-drone ROS 2 bridges" inside the run-plane
sim-adapter box: **one component in the `sim: cosys-airsim-ue5` profile**, plus
pieces of the `middleware` (airsim_mavros_bringup) and `comms` axes. Not the
platform, harness or conductor.

## 3. Mapping

Legend: fits · partial/decision · gap · n/a. "fixed" = landed on
`feat/tevv-platform-contract`.

| Spec concept | What exists in the bridge repo | Fit | Action |
|---|---|---|---|
| Contract topics `/clock`, `/tf`, `ground_truth/odom` | `config/topic_contract.yaml` authoritative; `test_topic_contract.py` asserts type, presence, stamp domain vs `/clock`. | fits | none |
| `/run_state` | `airsim_interfaces/msg/RunState` defines it; scenario controller in this repo publishes it. | fits | none |
| `/<vehicle>/collision` | Only legacy `simple_multirotor_node` published it. | gap | **fixed**: `MultirotorNode` polls `simGetCollisionInfo` on the state tick, publish-on-change + initial sample, reliable/transient_local. Added to contract yaml + launch enumeration. Live test `make test-collision`. |
| `/<vehicle>/…` naming | Image default is bare (`/ground_truth/odom`); `{vehicle}/` opt-in. | decision | Orchestrated runs pass `topic_prefix:={vehicle}/` (done in tevv.yaml + OSMO example), or spec relaxes to bare for `drones: 1`. |
| tevv.yaml provides/requires + QoS | None existed. | gap | **fixed** (draft shape): `tevv.yaml` at repo root. First real instance for the platform schema. |
| Platform certification suite | `make validate`: tf-tree + sensor-fidelity → one `mns.bridge.validation.v1` report; plus contract test. | fits | This *is* the sim profile's certification suite; wired as `certify` lead in the OSMO example. |
| L0 pure unit tests | `colcon test`, `test_tf_frames.py`, `test_topic_naming.py`, dep-surface job. | fits | Declared under `unit_tests` in tevv.yaml. |
| Sim time + lockstep | `/clock` from AirSim clock, `use_sim_time`, `stamp_odom_with_sim_time`; px4/ardupilot settings variants ship `LockStep: true`. | fits | none (lidar caps at 10 Hz under PX4 lockstep on the dev host) |
| Autopilot axis px4 / ardupilot | compose files, mavros configs, settings variants. | fits | none |
| Middleware axis ros2-mavros | `airsim_mavros_bringup`. | fits | `direct-mavlink` not the bridge's job |
| Comms axis `zenoh` | Spec: `rmw_zenoh_cpp` + router task. Bridge: `zenoh-bridge-ros2dds` gateway over CycloneDDS. | decision | Spec §16 defers rmw_zenoh. Proposal: accept `zenoh-bridge` as pilot profile. |
| Comms axis `fastdds-ds` | `fastdds-udp-only.xml` existed; no DS config. | gap | **fixed**: nodes need only `ROS_DISCOVERY_SERVER`; `config/fastdds-discovery-superclient.xml` for tooling. |
| Sim pod ↔ bridge pod separation | iceoryx2 SHM needs shared `/dev/shm`, `/tmp/iceoryx2`, IPC namespace. | decision | SHM forces sim + bridge into **one task**. Example uses RPC. Platform owner picks. |
| MCAP bags, split, rolling upload | `make bag` used sqlite3, one file. | gap | **fixed**: `-s mcap --max-bag-size 1 GiB` default. Uploader = harness. |
| Startup barrier ("container running ≠ sim answering RPC") | none | gap | **fixed**: entrypoint `AIRSIM_DIAL_WAIT_SEC` dial-waits the RPC port, exit 42 on expiry; `AIRSIM_HOST_IP` feeds `host_ip:=`. |
| "Bridge connects once, no reconnect" (spec §2.1 defect) | launch `respawn=True, respawn_delay=5.0`. | tell spec owner | Partly stale. Under `ignoreNonleadStatus: false` the gang fails first anyway. |
| stdout-only logging | plain `ros2 launch`. | fits | `RCUTILS_LOGGING_USE_STDOUT=1` in task env. |
| Fault injection, sim backend | none in bridge. | n/a | fault-injector harness pod owns it; `fault_capabilities: []`. |
| Digest-pinned images from Harbor | GHCR publish; GHCR base-image mirror; vendored-lib manifest + pin check. | later | GHCR is hosted SaaS → conflicts with air-gap end state; platform move. |
| CI vendor | GitHub Actions self-hosted. | later | Jenkins spec retires GHA; only `unit_tests` block carries forward. |
| Integration ladder Stage 0–4 (docs/integration) | toggle-based substitution GT↔VIO, GT map↔SLAM, scripted goals↔exploration. | fits | same idea as L1/L2 `stack[]` substitution. |
| Multi-drone 1–16 | `airsim_bringup.launch.py` fleet + `{vehicle}/` prefix. | fits | none |
| HIL above L3 (Jetson gang group) | zenoh → Orin registered-cloud path. | precursor | OSMO HIL how-to uses the same discovery-server pattern. |

## 4. OSMO, as it actually is (user guide, 2026-09-21)

- Groups: exactly one `lead`, gang-scheduled; `barrier: true` default (all
  images pulled, containers ready, before any task starts).
- `ignoreNonleadStatus` defaults **true** (dead non-lead restarts alone); the
  compiler must emit `false`.
- `{{host:<task>}}` gives live DNS between tasks; NVIDIA's ROS 2 cookbook runs
  a FastDDS discovery server as a task.
- `exitActions: {COMPLETE, FAIL, RESCHEDULE}` by exit-code range. User codes
  ≤ 255. Service codes: 3006 preempted, 3004 evicted, 3005 start timeout,
  137 OOM. Default: 0 completes, preemption reschedules, else fails.
- **Changed since the 07-13 spec:** `workflow.labels` (max 16, immutable) exist,
  settable with `--label k=v`. The "no labels, registry-only auto-cancel" text
  is stale; cancellation is still by exact workflow id.
- `volumeMounts` host mounts exist (`/dev/shm` documented); no documented
  hostIPC and no same-node guarantee across tasks → SHM needs one task.
- Local dev: kind + KAI + CloudNativePG + Helm chart, ~20 min;
  `osmo workflow submit … --dry-run` validates schema.

## 5. Exit-code contract the bridge speaks

| Exit | Meaning | OSMO action | Registry |
|---|---|---|---|
| 0 | finished normally | COMPLETE | — |
| 1 | contract / validation violation (certify lead) | FAIL, never retried | FAILED |
| 42 | readiness timeout (sim RPC never answered, contract topics never appeared) | RESCHEDULE | INFRA_FAILED |
| 137 | OOM / SIGKILL | RESCHEDULE (default) | INFRA_FAILED |
| 3006 | preempted by KAI | RESCHEDULE (default) | new attempt |

## 6. Decisions left for the platform owner

1. SHM co-location: RPC on the cluster (assumed) or one supervisor-run
   sim+bridge task that redefines the `cosys-airsim-ue5` profile.
2. Zenoh profile semantics: rmw_zenoh (spec) vs zenoh-bridge over Cyclone
   (working). One RMW per group; FastDDS↔Cyclone data never crosses.
3. Bare vs `{vehicle}/` topics: compiler always passes the prefix, or spec
   allows bare for one drone.
4. Auto-cancel via OSMO labels now that they exist.
5. Reconnect defect: launch respawn exists; confirm whether the spec still
   needs to design around "connects once".

## 7. Landed on `feat/tevv-platform-contract`

`/<vehicle>/collision` on MultirotorNode · `topic_contract.yaml` entry ·
`tevv.yaml` · `osmo/bridge-certify.workflow.yaml` +
`osmo/bridge-certify-hostsim.workflow.yaml` · `docs/OSMO.md` ·
`config/fastdds-discovery-superclient.xml` · entrypoint dial-wait ·
`make bag` MCAP + split · `make test-collision` live test.
