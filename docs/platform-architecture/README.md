# Platform architecture snapshot (autonomy-tevv-architecture, 2026-09-21)

Vendored copy of the Autonomy TEVV platform's architecture repo so agents and
people working in this repo can read the specs the runtime stack is being
mapped onto. **Source of truth is the upstream repo; edit there, re-snapshot
here.**

- Source: `autonomy-tevv-architecture` (owner zhihan), snapshot taken
  2026-09-21 from `~/Downloads/autonomy-tevv-architecture-2026-09-21/`.
- The upstream `CLAUDE.md` is kept as `SOURCE-REPO-CLAUDE.md` so Claude Code
  sessions in this repo do **not** load it as instructions; read it as a doc.
- Interactive mapping page (private, not agent-readable):
  https://claude.ai/artifact/1HsvevnJ4t6p5o12VYUe73 — the markdown version is
  [`bridge-platform-map.md`](bridge-platform-map.md).

## What is in here

| Path | What | Read it for |
|---|---|---|
| `docs/superpowers/specs/2026-07-10-autonomy-tevv-architecture-design.md` | **Baseline.** Five planes, Argo-native orchestration, omega/tevv YAML contract, `tevv-compile`, run registry, MinIO/MCAP, Loki, Grafana/Foxglove, L0–L3 tiers, FAILED vs INFRA_FAILED. | The contract surface every sim profile must present; the data plane. |
| `docs/superpowers/specs/2026-07-13-autonomy-tevv-osmo-orchestration-design.md` | **OSMO variant.** Supersedes the baseline's §7/§8 orchestration: one gang-scheduled OSMO group per run, conductor = lead task, KAI preemption, `ignoreNonleadStatus: false`, registry owns attempts. | How a run is orchestrated; the exit-code contract. |
| `docs/superpowers/specs/2026-08-18-autonomy-tevv-jenkins-ci-design.md` | **Jenkins CI plane.** Supersedes the trigger plane: Jenkins in-cluster, BuildKit sidecar, `ci.json`, `unit_tests:` in tevv.yaml, Monte Carlo matrix, disk/image-GC rules. | CI ↔ submit coupling; what a component repo must declare. |
| `docs/superpowers/specs/reviews/*.md` | Two adversarial review dispositions (Gemini). | Why decisions look the way they do. |
| `docs/superpowers/plans/2026-07-10-tevv-architecture-doc-set.md` | 16-task plan for the `architecture/`, `schemas/`, `tasks/` doc set. **None executed yet** — those directories do not exist upstream. | What is still missing (schemas!). |
| `diagrams/*.mermaid` | System, OSMO, data-flow, Jenkins diagrams. | Standalone sources of the spec figures. |
| `bridge-platform-map.md` | Where `TEVV-Airsim-ROS2-Bridge` sits in the platform, gap analysis, OSMO facts, open decisions. | The bridge side of the mapping. |
| `bridge/` | Copies of the bridge repo's `docs/OSMO.md`, `tevv.yaml`, and two OSMO workflow YAMLs (header names the commit). | Concrete component contract + submittable group examples. |

## Where this repo (the runtime stack) sits

The baseline spec names `M-S-Simulation-Runtime-Stack` as the **existing asset
to reuse, not rebuild** (§2.1): `tools/generate_scenario.py` becomes
`tevv-compile`'s compose renderer, the metrics-collector / `evaluate.py` /
`/run_state` lifecycle become harness components, the compose overlays remain
the manual path. The OSMO variant adds `--target osmo` to the same compiler.
The bridge repo is one task in the run-plane group (sim-adapter profile
`cosys-airsim-ue5`); SITL, MAVROS, mavlink-router and the sim image are this
repo's.

Open decisions the specs leave to the platform owner are listed at the end of
`bridge-platform-map.md` and in `bridge/OSMO.md` §5.
