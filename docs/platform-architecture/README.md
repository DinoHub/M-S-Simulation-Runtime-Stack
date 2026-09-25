# Platform architecture snapshot (autonomy-tevv-architecture, 2026-09-21)

Vendored copy of the Autonomy TEVV platform's architecture repo so agents and
people working in this repo can read the specs the runtime stack is being
mapped onto. **Source of truth is the upstream repo; edit there, re-snapshot
here.**

- Source: `autonomy-tevv-architecture` (owner zhihan), snapshot taken
  2026-09-21 from `~/Downloads/autonomy-tevv-architecture-2026-09-21/`.
- The design specs themselves (the Argo baseline, the OSMO orchestration
  variant, the Jenkins CI plane) and their review dispositions are **not**
  vendored; read them in the upstream repo.
- Interactive mapping page (private, not agent-readable):
  https://claude.ai/artifact/1HsvevnJ4t6p5o12VYUe73 — the markdown version is
  [`bridge-platform-map.md`](bridge-platform-map.md).

## What is in here

| Path | What | Read it for |
|---|---|---|
| `diagrams/*.mermaid` | System, OSMO, data-flow, Jenkins diagrams. | Standalone sources of the spec figures. |
| `bridge-platform-map.md` | Where `TEVV-Airsim-ROS2-Bridge` sits in the platform, gap analysis, OSMO facts, open decisions. | The bridge side of the mapping. |
| `bridge/` | Copies of the bridge repo's `tevv.yaml` and two OSMO workflow YAMLs (header names the commit). Its [`docs/OSMO.md`](https://github.com/DinoHub/TEVV-Airsim-ROS2-Bridge/blob/main/docs/OSMO.md) is read upstream. | Concrete component contract + submittable group examples. |

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
`bridge-platform-map.md` and in the bridge's [`docs/OSMO.md`](https://github.com/DinoHub/TEVV-Airsim-ROS2-Bridge/blob/main/docs/OSMO.md) §5.
