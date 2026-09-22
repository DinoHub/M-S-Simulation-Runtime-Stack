# TEVV Architecture Doc Set Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **Per repo CLAUDE.md §6: spawn Opus worker agents for individual tasks.**

**Goal:** Produce the complete, validated Architecture-as-Code deliverable for the Autonomy TEVV platform: 11 architecture docs, 9 ADRs, machine-validated schemas (omega/tevv/ci JSON Schema + registry DDL), diagram sources, and the four P0 handoff packages.

**Architecture:** Every artifact expands the three approved specs (baseline 2026-07-10 amended per the Gemini review disposition, the OSMO orchestration variant 2026-07-13, and the Jenkins CI plane 2026-08-18 — see Global Constraints for the supersession order). A small validation harness (`tools/validate_docs.sh`) enforces conventions and is run before every commit. Schemas are tested against worked examples; DDL is tested against a real dockerized Postgres.

**Tech Stack:** Markdown + Mermaid (native syntax, flowcharts TD/LR), JSON Schema draft 2020-12, PostgreSQL 16 DDL, bash + python3 (jsonschema, PyYAML), docker (postgres:16, ghcr.io/mermaid-js/mermaid-cli/mermaid-cli — Docker Hub pulls fail on this machine, use GHCR). Documented-not-executed targets: Jenkins (JCasC + Kubernetes plugin + shared library), BuildKit (`buildkitd` sidecar + `buildctl`), NVIDIA OSMO, KAI scheduler.

## Global Constraints

- **Source of truth:** three specs, with **layered and narrow** supersession — each supersedes only a slice of its predecessor, and the earlier documents remain the rationale record:
  1. Baseline `docs/superpowers/specs/2026-07-10-autonomy-tevv-architecture-design.md` — everything domain-side.
  2. **OSMO variant** `docs/superpowers/specs/2026-07-13-autonomy-tevv-osmo-orchestration-design.md` — supersedes the baseline for **orchestration** (§7, parts of §8/§9/§11/§13).
  3. **Jenkins CI plane** `docs/superpowers/specs/2026-08-18-autonomy-tevv-jenkins-ci-design.md` — supersedes both for the **trigger/CI plane** (baseline §3 trigger plane and §11 surface 1; variant §3 trigger plane and §7 submission). It changes nothing about orchestration or the data plane.

  Where they conflict, the later spec wins **within its own slice only**. Stay consistent with both review dispositions (`docs/superpowers/specs/reviews/2026-07-10-…` and `…/2026-07-13-gemini-3.1-pro-osmo-review-disposition.md`).
- **Headers:** sequential Markdown headers, no skipped levels (enforced by validator).
- **Diagrams:** native Mermaid only; flowcharts `graph TD` or `graph LR`; `stateDiagram-v2` allowed for state machines. No third-party layout tags.
- **Data models:** Markdown tables with columns Type, Nullable, Indexing Strategy, Scale/Retention notes.
- **ADR format:** exactly the sections `## Context`, `## Options`, `## Decision`, `## Consequences` (enforced by validator).
- **Handoff format (tasks/):** exactly the five H1 headers from repo CLAUDE.md §3: `# Task Context & System Summary`, `# Upstream Specifications`, `# Explicit Instruction Prompt`, `# Acceptance Criteria`, `# Expected Output Schema/Format`. Naming: `YYYYMMDD_[target_agent]_[short_feature_name].md`. Handoffs must be self-contained (copy-paste upstream schemas/spec blocks — never "see other file" for normative content).
- **No placeholders:** no TBD/TODO in any deliverable. Deferred decisions live only in spec §16.
- **Validation before every commit:** `bash tools/validate_docs.sh` must print `OK: all checks passed` (docker checks may WARN-skip only if docker is genuinely unavailable).
- **Commits:** small, per task, message prefixes `feat(tools):`, `feat(schemas):`, `docs(arch):`, `docs(adr):`, `docs(diagrams):`, `docs(tasks):`, `docs:`. End every commit message with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- **Terminology consistency:** tiers `L0|L1|L2|L3`; states `PENDING|PREPARING|READY|RUNNING|EVALUATING|PASSED|FAILED|INFRA_FAILED|ABORTED`; platform axes `sim|autopilot|middleware|comms` with profiles `cosys-airsim-ue5|isaac-pegasus`, `px4|ardupilot`, `ros2-mavros|direct-mavlink`, `zenoh|fastdds-ds`; identity `(run_id, attempt)`; DB schema name `tevv`; column `trigger_kind` (NOT `trigger` — reserved in PostgreSQL); orchestrator column `workflow_ref` (NOT `argo_workflow_id`).
- **OSMO terminology (variant spec):** one OSMO workflow per run (name = run_id), one group with `ignoreNonleadStatus: false`, run-conductor is the `lead` task; scheduling = KAI pools + priorities `HIGH|NORMAL|LOW` (L1 PR smoke HIGH, L0/L2 NORMAL, L3/sweeps LOW-preemptible); lead exit-code contract `0=PASSED, 1=FAILED (never retried), 42=INFRA_FAILED reschedule ≤2, 137=SIGKILL/OOM, 3006=preempted`; inter-task addressing via `{{host:<task>}}`; artifact prefix `tevv-runs/<yyyy>/<mm>/<run_id>/<attempt>/…` (attempt-scoped; manual upload = attempt 1).
- **CI-plane terminology (Jenkins spec):** CI is **Jenkins** — controller + dynamic Kubernetes **agent pods** (never "master"/"slave"); image builds use **BuildKit** via an ephemeral `buildkitd` **sidecar inside each agent pod**, driven by `buildctl` — never a Docker socket, never a shared cluster builder (a long-lived builder would add a fifth accumulation store under BuildKit's own GC). Kaniko is **not** used: upstream was archived 2025-06-03. The sidecar requires a namespaced Pod Security Admission / Kyverno exception for `seccompProfile: Unconfined` + `appArmorProfile: Unconfined`; that exception is scoped to the CI namespace and must never reach run-plane namespaces. Pipeline stages are `resolve | build | unit | submit`. The compiler is the **sole reader** of `omega.yaml`/`tevv.yaml`; CI consumes `ci.json` emitted by `tevv-compile --emit-ci` and never parses contract YAML itself. Coupling is **fire-and-forget**: Jenkins terminates at `submit`, so a green build means *submitted*, not *passed*; the run-conductor owns the verdict and PR feedback. Matrix modes are `product` (default, today's cross-product) and `sample` (Monte Carlo, requires `seed`). Per-component test blocks are `l0_tests` (produce registry verdicts) and `unit_tests` (**CI-only, never a registry verdict** — `verifies:` is prohibited inside `unit_tests`). Agent pods set `schedulerName: kai-scheduler` and carry explicit `ephemeral-storage` requests/limits, exactly as run pods do.

---

### Task 1: Validation harness

**Files:**
- Create: `tools/validate_docs.sh`
- Create: `tools/validate_yaml.py`
- Create: `tools/requirements-docs.txt`
- Create: `tools/fixtures/bad-header.md`
- Test: the script itself, run in both fixture (fail) and repo (pass) modes

**Interfaces:**
- Produces: `bash tools/validate_docs.sh` (no args = validate whole repo; file args = validate only those files' header sequence). Exit 0 + `OK: all checks passed` on success, exit 1 with `FAIL: …` lines otherwise. Every later task runs this before committing.
- Produces: `python3 tools/validate_yaml.py <schema.json> <file.yaml>...` — exit 0 iff all YAML files validate against the JSON Schema.

- [ ] **Step 1: Write the fixture that must fail (the "failing test")**

`tools/fixtures/bad-header.md`:

```markdown
# Title

### Skipped a level — h3 directly under h1
```

- [ ] **Step 2: Write `tools/requirements-docs.txt`**

```text
jsonschema>=4.21
PyYAML>=6.0
```

- [ ] **Step 3: Write `tools/validate_yaml.py`**

```python
#!/usr/bin/env python3
"""Validate YAML files against a JSON Schema. Usage: validate_yaml.py schema.json file.yaml [...]"""
import json
import sys

import jsonschema
import yaml

def main() -> int:
    if len(sys.argv) < 3:
        print("usage: validate_yaml.py <schema.json> <file.yaml>...")
        return 2
    with open(sys.argv[1]) as fh:
        schema = json.load(fh)
    rc = 0
    for path in sys.argv[2:]:
        with open(path) as fh:
            doc = yaml.safe_load(fh)
        try:
            jsonschema.validate(doc, schema)
            print(f"OK: {path}")
        except jsonschema.ValidationError as exc:
            print(f"FAIL: {path}: {exc.message} (at {'/'.join(str(p) for p in exc.absolute_path)})")
            rc = 1
    return rc

if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Write `tools/validate_docs.sh`**

```bash
#!/usr/bin/env bash
# Validates the architecture doc set conventions.
# No args: validate the whole repo. With file args: header-sequence check on those files only.
set -uo pipefail
FAIL=0
err() { echo "FAIL: $*"; FAIL=1; }

check_headers() { # $1 = markdown file
  awk '
    /^```/ { code = !code; next }
    !code && /^#{1,6} / {
      n = match($0, /[^#]/) - 1
      if (prev > 0 && n > prev + 1) {
        printf "%s:%d skipped header level (h%d after h%d)\n", FILENAME, FNR, n, prev
        bad = 1
      }
      prev = n
    }
    END { exit bad }
  ' "$1" || err "$1: header sequence"
}

if [ "$#" -gt 0 ]; then
  for f in "$@"; do check_headers "$f"; done
  [ "$FAIL" -eq 0 ] && echo "OK: all checks passed"
  exit "$FAIL"
fi

# --- discover mode: whole repo ---
# docs/superpowers/ (spec, plan, review — process artifacts) and .claude/ are excluded:
# the plan contains illustrative nested fences that are not real mermaid, and the
# validator guards the deliverable doc set, not the process docs.
MD_FILES=$(find . -name '*.md' -not -path './.git/*' -not -path './.superpowers/*' -not -path './docs/superpowers/*' -not -path './.claude/*' -not -path './tools/fixtures/*' | sort)

# 1. Header sequence everywhere
for f in $MD_FILES; do check_headers "$f"; done

# 2. Handoff template (tasks/, except README.md)
for f in tasks/*.md; do
  [ -e "$f" ] || continue
  [ "$(basename "$f")" = "README.md" ] && continue
  while IFS= read -r h; do
    grep -qxF "$h" "$f" || err "$f: missing handoff header '$h'"
  done <<'EOF'
# Task Context & System Summary
# Upstream Specifications
# Explicit Instruction Prompt
# Acceptance Criteria
# Expected Output Schema/Format
EOF
done

# 3. ADR structure
for f in architecture/adr/*.md; do
  [ -e "$f" ] || continue
  for h in "## Context" "## Options" "## Decision" "## Consequences"; do
    grep -qxF "$h" "$f" || err "$f: missing ADR section '$h'"
  done
done

# 4. Mermaid validation (standalone files + fenced blocks) via dockerized mmdc
check_mermaid() { # $1 = repo-relative path to a .mermaid file
  # GHCR, not Docker Hub: hub pulls fail on this machine (registry auth), ghcr works.
  if ! docker run --rm -u "$(id -u)" -v "$PWD:/data" ghcr.io/mermaid-js/mermaid-cli/mermaid-cli:latest \
      -i "/data/$1" -o "/data/.mmdc-out.svg" >/dev/null 2>&1; then
    err "$1: mermaid syntax"
  fi
  rm -f .mmdc-out.svg
}
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  for f in diagrams/*.mermaid; do [ -e "$f" ] && check_mermaid "$f"; done
  TMPD=".mmdc-blocks.$$"; mkdir -p "$TMPD"
  for f in $MD_FILES; do
    awk -v out="$TMPD" -v src="$(basename "$f" .md)" '
      /^```mermaid$/ { code = 1; n++; file = sprintf("%s/%s-%d.mermaid", out, src, n); next }
      code && /^```$/ { code = 0; next }
      code { print > file }
    ' "$f"
  done
  for b in "$TMPD"/*.mermaid; do [ -e "$b" ] && check_mermaid "$b"; done
  rm -rf "$TMPD"
else
  echo "WARN: docker unavailable — mermaid validation skipped"
fi

# 5. YAML examples vs schemas
if [ -f schemas/omega.schema.json ] && compgen -G 'schemas/examples/*.omega.yaml' >/dev/null; then
  python3 tools/validate_yaml.py schemas/omega.schema.json schemas/examples/*.omega.yaml || err "omega examples vs schema"
fi
if [ -f schemas/tevv.schema.json ] && compgen -G 'schemas/examples/*.tevv.yaml' >/dev/null; then
  python3 tools/validate_yaml.py schemas/tevv.schema.json schemas/examples/*.tevv.yaml || err "tevv examples vs schema"
fi

# 6. Sync: inline copies in 00-system-overview must match standalone diagram sources
if [ -f architecture/00-system-overview.md ]; then
  for d in system-architecture-osmo system-data-flow-osmo; do
    awk -v marker="<!-- sync:diagrams/${d}.mermaid -->" '
      $0 == marker { want = 1; next }
      want && /^```mermaid$/ { code = 1; next }
      code && /^```$/ { exit }
      code { print }
    ' architecture/00-system-overview.md > ".sync-check.$$"
    if [ -s ".sync-check.$$" ]; then
      diff -q ".sync-check.$$" "diagrams/${d}.mermaid" >/dev/null || err "00-system-overview inline ${d} diverged from diagrams/${d}.mermaid"
    else
      err "00-system-overview missing sync marker/block for diagrams/${d}.mermaid"
    fi
    rm -f ".sync-check.$$"
  done
fi

[ "$FAIL" -eq 0 ] && echo "OK: all checks passed"
exit "$FAIL"
```

- [ ] **Step 5: Install python deps and make executable**

Run: `pip install --user -r tools/requirements-docs.txt && chmod +x tools/validate_docs.sh tools/validate_yaml.py`
Expected: jsonschema + PyYAML installed (or already satisfied).

- [ ] **Step 6: Run the failing case**

Run: `bash tools/validate_docs.sh tools/fixtures/bad-header.md; echo "exit=$?"`
Expected: `FAIL: tools/fixtures/bad-header.md: header sequence` … `exit=1`

- [ ] **Step 7: Run discover mode on the repo**

Run: `bash tools/validate_docs.sh; echo "exit=$?"`
Expected: `OK: all checks passed`, `exit=0` (docker pulls `minlag/mermaid-cli` on first use; the two existing `diagrams/*.mermaid` files must pass. If they fail, fix the diagram syntax — not the validator — and re-run.)

- [ ] **Step 8: Commit**

```bash
git add tools/
git commit -m "feat(tools): add doc-set validation harness (headers, ADR/handoff templates, mermaid, schema examples)

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: omega.yaml JSON Schema + worked examples

**Files:**
- Create: `schemas/omega.schema.json`
- Create: `schemas/examples/l1-planner-substack-smoke.omega.yaml`
- Create: `schemas/examples/invalid-unknown-sim.omega.yaml.rejected` (negative fixture, extension keeps it out of validator globs)
- Test: `tools/validate_yaml.py` positive + negative runs

**Interfaces:**
- Consumes: `tools/validate_yaml.py` (Task 1).
- Produces: `schemas/omega.schema.json` — top-level required keys `suite, tier, scenario, platform, stack, mission, evaluation`; optional `verifies, faults, record, matrix`. `matrix` has two mutually exclusive forms: `mode: product` (default, cross-product of explicit lists) and `mode: sample` (Monte Carlo — Jenkins CI spec §6.1). Docs (Task 8) and handoffs (Task 12) reference these exact field names.

- [ ] **Step 1: Write the schema**

`schemas/omega.schema.json`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://tevv.local/schemas/omega.schema.json",
  "title": "TEVV omega suite definition",
  "type": "object",
  "additionalProperties": false,
  "required": ["suite", "tier", "scenario", "platform", "stack", "mission", "evaluation"],
  "properties": {
    "suite": { "type": "string", "pattern": "^[a-z0-9][a-z0-9-]{2,63}$" },
    "tier": { "enum": ["L0", "L1", "L2", "L3"] },
    "verifies": { "type": "array", "items": { "type": "string", "pattern": "^REQ-[A-Z]+-[0-9]{3}$" }, "uniqueItems": true },
    "scenario": {
      "type": "object",
      "additionalProperties": false,
      "required": ["world", "drones", "origin"],
      "properties": {
        "world": { "type": "string" },
        "drones": { "type": "integer", "minimum": 1, "maximum": 16 },
        "origin": { "type": "string", "pattern": "^preset:[a-z0-9-]+$" }
      }
    },
    "platform": {
      "type": "object",
      "additionalProperties": false,
      "required": ["sim", "autopilot", "middleware", "comms"],
      "properties": {
        "sim": { "enum": ["cosys-airsim-ue5", "isaac-pegasus"] },
        "autopilot": { "enum": ["px4", "ardupilot"] },
        "middleware": { "enum": ["ros2-mavros", "direct-mavlink"] },
        "comms": { "enum": ["zenoh", "fastdds-ds"] }
      }
    },
    "stack": {
      "type": "array",
      "minItems": 1,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["component", "ref"],
        "properties": {
          "component": { "type": "string" },
          "ref": { "type": "string", "pattern": "^(\\$\\{TRIGGER_SHA\\}|[a-f0-9]{7,40}|pinned@sha256:[a-f0-9]{4,64}(…)?)$" },
          "params": { "type": "object" }
        }
      }
    },
    "mission": {
      "type": "object",
      "additionalProperties": false,
      "required": ["type", "timeout_sec"],
      "properties": {
        "type": { "enum": ["waypoints", "goal", "exploration", "bt-mission"] },
        "file": { "type": "string" },
        "timeout_sec": { "type": "number", "exclusiveMinimum": 0 }
      }
    },
    "faults": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["at", "inject"],
        "properties": {
          "at": { "type": "string", "pattern": "^(t\\+[0-9]+(\\.[0-9]+)?s|waypoint:[0-9]+|run_state:[a-z_]+)$" },
          "inject": { "type": "string", "pattern": "^[a-z][a-z0-9_]+$" },
          "scope": { "type": "string", "pattern": "^(drone:[0-9]+|all)$" },
          "duration": { "type": "string", "pattern": "^[0-9]+(\\.[0-9]+)?s$" },
          "params": { "type": "object" }
        }
      }
    },
    "evaluation": {
      "type": "object",
      "additionalProperties": false,
      "required": ["gates"],
      "properties": {
        "gates": {
          "type": "object",
          "minProperties": 1,
          "additionalProperties": {
            "oneOf": [
              { "type": "boolean" },
              { "type": "number" },
              {
                "type": "object",
                "additionalProperties": false,
                "properties": {
                  "max": { "type": "number" },
                  "min": { "type": "number" },
                  "during": { "type": "string" },
                  "after": { "type": "string" }
                },
                "minProperties": 1
              }
            ]
          }
        }
      }
    },
    "record": {
      "type": "object",
      "additionalProperties": false,
      "properties": { "topics": { "type": "array", "items": { "type": "string" }, "minItems": 1 } }
    },
    "matrix": {
      "oneOf": [
        {
          "$comment": "product mode (default) - cross-product of explicit lists; baseline spec 5.2 step 5",
          "type": "object",
          "properties": { "mode": { "const": "product" } },
          "additionalProperties": { "type": "array", "minItems": 2 }
        },
        {
          "$comment": "sample mode - Monte Carlo; Jenkins CI spec 6.1. seed is mandatory: manifest.json must make every run reproducible.",
          "type": "object",
          "additionalProperties": false,
          "required": ["mode", "samples", "seed", "axes"],
          "properties": {
            "mode": { "const": "sample" },
            "samples": { "type": "integer", "minimum": 2, "maximum": 5000 },
            "seed": { "type": "integer", "minimum": 0 },
            "axes": {
              "type": "object",
              "minProperties": 1,
              "additionalProperties": { "$ref": "#/$defs/samplingAxis" }
            }
          }
        }
      ]
    }
  },
  "$defs": {
    "samplingAxis": {
      "oneOf": [
        { "type": "object", "additionalProperties": false, "required": ["dist", "mu", "sigma"],
          "properties": { "dist": { "const": "normal" }, "mu": { "type": "number" },
                          "sigma": { "type": "number", "exclusiveMinimum": 0 },
                          "clamp": { "type": "array", "items": { "type": "number" }, "minItems": 2, "maxItems": 2 } } },
        { "type": "object", "additionalProperties": false, "required": ["dist", "lo", "hi"],
          "properties": { "dist": { "enum": ["uniform", "loguniform"] }, "lo": { "type": "number" }, "hi": { "type": "number" },
                          "clamp": { "type": "array", "items": { "type": "number" }, "minItems": 2, "maxItems": 2 } } },
        { "type": "object", "additionalProperties": false, "required": ["dist", "values"],
          "properties": { "dist": { "const": "choice" }, "values": { "type": "array", "minItems": 2 },
                          "weights": { "type": "array", "items": { "type": "number", "minimum": 0 }, "minItems": 2 } } }
      ]
    }
  }
}
```

This exact fragment was verified against `jsonschema` 4.25.1 at plan-amendment time: the seven cases in Steps 5–7 below all behave as stated. Note that `oneOf` produces branch-ambiguous error text — a bad `sample` document often reports the *product* branch's complaint. That is expected; assert on exit code, not message wording.

- [ ] **Step 2: Write the positive example (verbatim from spec §5.3)**

`schemas/examples/l1-planner-substack-smoke.omega.yaml` — copy the complete example from spec §5.3 exactly (suite `l1-planner-substack-smoke`, tier L1, verifies three REQ ids, xfs scenario, the 4 platform axes, 2-component stack, waypoints mission, 3 faults, 5 gates incl. `during:`/`after:`, record topics, matrix with `local-planner.params.max_vel` and `faults[2].params.drop_pct`).

- [ ] **Step 3: Write the negative fixture**

`schemas/examples/invalid-unknown-sim.omega.yaml.rejected` — same document but `platform.sim: gazebo-classic`.

- [ ] **Step 4: Run positive test**

Run: `python3 tools/validate_yaml.py schemas/omega.schema.json schemas/examples/l1-planner-substack-smoke.omega.yaml`
Expected: `OK: schemas/examples/l1-planner-substack-smoke.omega.yaml`, exit 0.

- [ ] **Step 5: Run negative test**

Run: `python3 tools/validate_yaml.py schemas/omega.schema.json schemas/examples/invalid-unknown-sim.omega.yaml.rejected; echo "exit=$?"`
Expected: `FAIL: … 'gazebo-classic' is not one of …`, `exit=1`.

- [ ] **Step 6: Write the sampled-matrix fixtures**

`schemas/examples/l3-wind-montecarlo.omega.yaml` — same document as the L1 example but `suite: l3-wind-montecarlo`, `tier: L3`, and this matrix block replacing the product one:

```yaml
matrix:
  mode: sample
  samples: 200
  seed: 42
  axes:
    faults[0].params.vector_ms[0]:
      dist: normal
      mu: 8.0
      sigma: 2.0
      clamp: [0.0, 20.0]
    scenario.origin.yaw_deg:
      dist: uniform
      lo: 0
      hi: 360
    local-planner.params.max_vel:
      dist: choice
      values: [2.0, 4.0, 6.0]
      weights: [0.25, 0.5, 0.25]
```

`schemas/examples/invalid-sample-no-seed.omega.yaml.rejected` — the same document with the `seed: 42` line deleted.

- [ ] **Step 7: Run the sampled-matrix tests**

Run: `python3 tools/validate_yaml.py schemas/omega.schema.json schemas/examples/l3-wind-montecarlo.omega.yaml`
Expected: `OK: schemas/examples/l3-wind-montecarlo.omega.yaml`, exit 0.

Run: `python3 tools/validate_yaml.py schemas/omega.schema.json schemas/examples/invalid-sample-no-seed.omega.yaml.rejected; echo "exit=$?"`
Expected: a `FAIL:` line and `exit=1`. **Do not assert on the message text** — `oneOf` reports whichever branch it likes.

Also confirm backward compatibility explicitly: the L1 example from Step 2 has no `mode` key and must still validate. If it does not, the `product` branch is wrong and today's suites would all break.

- [ ] **Step 8: Full validator + commit**

Run: `bash tools/validate_docs.sh` → `OK: all checks passed`

```bash
git add schemas/
git commit -m "feat(schemas): add omega.yaml JSON Schema with worked and negative examples

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: tevv.yaml JSON Schema + worked example

**Files:**
- Create: `schemas/tevv.schema.json`
- Create: `schemas/examples/local-planner.tevv.yaml`
- Create: `schemas/examples/invalid-unit-verifies.tevv.yaml.rejected` (negative fixture — the `verifies:` prohibition)
- Test: positive + negative validation runs

**Interfaces:**
- Consumes: `tools/validate_yaml.py`.
- Produces: `schemas/tevv.schema.json` — required keys `component, type, image, interfaces`; optional `params_schema, defaults, l0_tests, unit_tests, resources`. Interface entries carry `{name, kind, type, qos{reliability, durability, depth}}` — Task 8/9 docs and Task 12 handoffs use these exact names. `unit_tests[]` entries carry `{name, command, timeout_sec, resources, artifacts, required}` and are consumed by Task 14's `ci.json` `unit[]` array.

- [ ] **Step 1: Write the schema**

`schemas/tevv.schema.json`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://tevv.local/schemas/tevv.schema.json",
  "title": "TEVV per-repo component self-description",
  "type": "object",
  "additionalProperties": false,
  "required": ["component", "type", "image", "interfaces"],
  "properties": {
    "component": { "type": "string", "pattern": "^[a-z0-9][a-z0-9-]{1,63}$" },
    "type": { "enum": ["perception", "slam", "global_planner", "local_planner", "bt", "model"] },
    "image": {
      "type": "object",
      "additionalProperties": false,
      "required": ["repository", "entrypoint"],
      "properties": {
        "repository": { "type": "string" },
        "entrypoint": { "type": "string" }
      }
    },
    "params_schema": { "type": "object" },
    "defaults": { "type": "object" },
    "interfaces": {
      "type": "object",
      "additionalProperties": false,
      "required": ["provides", "requires"],
      "properties": {
        "provides": { "$ref": "#/$defs/ifaceList" },
        "requires": { "$ref": "#/$defs/ifaceList" }
      }
    },
    "l0_tests": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["name", "dataset", "evaluator", "gates"],
        "properties": {
          "name": { "type": "string" },
          "dataset": { "type": "string", "pattern": "^ds-[a-z0-9-]+$" },
          "evaluator": { "type": "string" },
          "gates": { "type": "object", "minProperties": 1 }
        }
      }
    },
    "unit_tests": {
      "$comment": "CI-only. Runs in a Jenkins agent pod; reports as build status + JUnit, NEVER a registry verdict. 'verifies' is deliberately absent from properties: additionalProperties=false then rejects it, which is the mechanical enforcement of baseline spec 4 (only orchestrator-produced results carry requirements traceability). Do not add 'verifies' here. See Jenkins CI spec 6.2.",
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["name", "command"],
        "properties": {
          "name": { "type": "string", "pattern": "^[a-z0-9][a-z0-9-]{1,63}$" },
          "command": { "type": "array", "items": { "type": "string" }, "minItems": 1 },
          "timeout_sec": { "type": "number", "exclusiveMinimum": 0 },
          "resources": {
            "type": "object",
            "additionalProperties": false,
            "properties": {
              "cpu": { "type": "string" },
              "memory": { "type": "string" },
              "ephemeral-storage": { "type": "string" }
            }
          },
          "artifacts": { "type": "array", "items": { "type": "string" } },
          "required": { "type": "boolean" }
        }
      }
    },
    "resources": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "node_class": { "enum": ["cpu", "gpu-inference"] },
        "cpu": { "type": "string" },
        "memory": { "type": "string" }
      }
    }
  },
  "$defs": {
    "ifaceList": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["name", "kind", "type"],
        "properties": {
          "name": { "type": "string" },
          "kind": { "enum": ["topic", "service", "action"] },
          "type": { "type": "string" },
          "qos": {
            "type": "object",
            "additionalProperties": false,
            "properties": {
              "reliability": { "enum": ["reliable", "best_effort"] },
              "durability": { "enum": ["volatile", "transient_local"] },
              "depth": { "type": "integer", "minimum": 1 }
            }
          }
        }
      }
    }
  }
}
```

- [ ] **Step 2: Write the example**

`schemas/examples/local-planner.tevv.yaml`:

```yaml
component: local-planner
type: local_planner
image:
  repository: harbor.local/autonomy/local-planner
  entrypoint: ros2 launch local_planner planner.launch.py
defaults:
  max_vel: 4.0
interfaces:
  provides:
    - name: /cmd/trajectory
      kind: topic
      type: trajectory_msgs/msg/JointTrajectory
      qos: { reliability: reliable, durability: volatile, depth: 10 }
  requires:
    - name: /goal
      kind: topic
      type: geometry_msgs/msg/PoseStamped
      qos: { reliability: reliable, durability: transient_local, depth: 1 }
    - name: /local_costmap
      kind: topic
      type: nav_msgs/msg/OccupancyGrid
      qos: { reliability: best_effort, durability: volatile, depth: 1 }
unit_tests:
  - name: gtest-core
    command: ["colcon", "test", "--packages-select", "local_planner"]
    timeout_sec: 600
    resources: { cpu: "4", memory: "8Gi", ephemeral-storage: "20Gi" }
    artifacts: ["build/**/test_results/**/*.xml"]
    required: true
l0_tests:
  - name: replay-corridor
    dataset: ds-corridor-costmaps-v1
    evaluator: harbor.local/tevv/eval-planner-replay
    gates:
      trajectory_jerk_max: { max: 8.0 }
      replan_latency_ms: { max: 50 }
resources:
  node_class: cpu
  cpu: "2"
  memory: 2Gi
```

- [ ] **Step 3: Run validation**

Run: `python3 tools/validate_yaml.py schemas/tevv.schema.json schemas/examples/local-planner.tevv.yaml`
Expected: `OK: …`, exit 0.

- [ ] **Step 4: Write the `verifies:` negative fixture**

This fixture guards the single most important boundary in the CI plane: a unit test must never be able to claim requirements coverage, because unit results never reach the registry (baseline spec §4, Jenkins CI spec §6.2). Without this fixture the rule is a comment; with it, the rule is enforced.

`schemas/examples/invalid-unit-verifies.tevv.yaml.rejected` — copy `local-planner.tevv.yaml` and add one line inside the `gtest-core` entry:

```yaml
unit_tests:
  - name: gtest-core
    command: ["colcon", "test", "--packages-select", "local_planner"]
    verifies: [REQ-NAV-012]     # <-- must be rejected
    timeout_sec: 600
```

- [ ] **Step 5: Run the prohibition test**

Run: `python3 tools/validate_yaml.py schemas/tevv.schema.json schemas/examples/invalid-unit-verifies.tevv.yaml.rejected; echo "exit=$?"`
Expected: `FAIL: … Additional properties are not allowed ('verifies' was unexpected)`, `exit=1`.

Verified against `jsonschema` 4.25.1 at plan-amendment time: `additionalProperties: false` alone produces exactly that message. Do **not** add a `not`/`required` clause or a `"verifies": false` property to "strengthen" it — both were tested and neither improves the error; the boolean-false form actively degrades it to `False schema does not allow [...]`.

- [ ] **Step 6: Full validator + commit**

Run: `bash tools/validate_docs.sh` → `OK: all checks passed`

```bash
git add schemas/
git commit -m "feat(schemas): add tevv.yaml component schema with local-planner example

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Registry DDL, tested against real Postgres

**Files:**
- Create: `schemas/registry.sql`
- Test: apply to dockerized postgres:16; assert table count, PK behavior, view existence

**Interfaces:**
- Consumes: spec §9.1 table list; `(run_id, attempt)` identity from §8.
- Produces: schema `tevv` with tables `suites, requirements, runs, components, gate_results, metrics_summary, fault_events, artifacts, datasets` and views `v_run_summary, v_verification_matrix, v_component_history`. Column names here are normative for Task 10 (data-plane doc) and Task 12 (P0 handoffs). Note `trigger_kind`, not `trigger`; `workflow_ref` (orchestrator-neutral, holds the OSMO workflow id), not `argo_workflow_id`.

- [ ] **Step 1: Write the DDL**

`schemas/registry.sql`:

```sql
-- TEVV run registry — system of record. Spec §9.1; identity model spec §8 (run_id, attempt).
create schema if not exists tevv;

create table tevv.suites (
    suite_id        text primary key,
    name            text not null,
    tier            text not null check (tier in ('L0','L1','L2','L3')),
    omega_repo      text not null,
    omega_path      text not null,
    omega_digest    text not null,
    requirement_ids text[] not null default '{}',
    created_at      timestamptz not null default now()
);

create table tevv.requirements (
    req_id     text primary key,
    title      text not null,
    source_doc text not null,
    synced_at  timestamptz not null default now()
);

create table tevv.runs (
    run_id           text not null,
    attempt          smallint not null default 1 check (attempt >= 1),
    suite_id         text not null references tevv.suites(suite_id),
    tier             text not null check (tier in ('L0','L1','L2','L3')),
    trigger_kind     text not null check (trigger_kind in ('commit','pr-smoke','merge','nightly','manual','sweep')),
    trigger_ref      text,
    platform         jsonb not null,
    matrix_point     jsonb,
    status           text not null default 'PENDING'
                     check (status in ('PENDING','PREPARING','READY','RUNNING','EVALUATING',
                                       'PASSED','FAILED','INFRA_FAILED','ABORTED')),
    verdict          text check (verdict in ('pass','fail','error','aborted')),
    failure_reason   text check (failure_reason in ('readiness_timeout','sim_crash','pod_crash',
                                                    'infra','mission_timeout','evaluator_error')),
    started_at       timestamptz,
    ended_at         timestamptz,
    workflow_ref     text,  -- orchestrator workflow id (OSMO workflow id / Argo workflow name)
    manifest_uri     text not null,
    created_at       timestamptz not null default now(),
    primary key (run_id, attempt)
);
create index runs_suite_started_idx on tevv.runs (suite_id, started_at);
create index runs_started_idx       on tevv.runs (started_at);
create index runs_trigger_ref_idx  on tevv.runs (trigger_ref);
create index runs_verdict_idx      on tevv.runs (verdict);
create index runs_failure_idx      on tevv.runs (failure_reason);
create index runs_platform_gin     on tevv.runs using gin (platform);
create index runs_matrix_gin       on tevv.runs using gin (matrix_point);

create table tevv.components (
    run_id       text not null,
    attempt      smallint not null,
    component_id text not null,
    repo         text not null,
    sha          text,
    image_digest text not null,
    params       jsonb,
    primary key (run_id, attempt, component_id),
    foreign key (run_id, attempt) references tevv.runs (run_id, attempt) on delete cascade
);

create table tevv.gate_results (
    run_id          text not null,
    attempt         smallint not null,
    gate            text not null,
    expected        jsonb not null,
    actual          jsonb,
    passed          boolean not null,
    fault_scope     text,
    requirement_ids text[] not null default '{}',
    primary key (run_id, attempt, gate),
    foreign key (run_id, attempt) references tevv.runs (run_id, attempt) on delete cascade
);

create table tevv.metrics_summary (
    run_id  text not null,
    attempt smallint not null,
    metric  text not null,
    value   double precision not null,
    unit    text,
    primary key (run_id, attempt, metric),
    foreign key (run_id, attempt) references tevv.runs (run_id, attempt) on delete cascade
);

create table tevv.fault_events (
    run_id      text not null,
    attempt     smallint not null,
    seq         integer not null,
    fault_type  text not null,
    backend     text not null check (backend in ('sim','autopilot','link','ros')),
    t_start_sim double precision not null,
    t_end_sim   double precision,
    params      jsonb,
    primary key (run_id, attempt, seq),
    foreign key (run_id, attempt) references tevv.runs (run_id, attempt) on delete cascade
);

create table tevv.artifacts (
    run_id          text not null,
    attempt         smallint not null,
    kind            text not null check (kind in ('rosbag','video','eval_json','manifest',
                                                  'log_export','autopilot_log','diagnostics')),
    s3_uri          text not null,
    size_bytes      bigint,
    checksum        text,
    retention_class text not null check (retention_class in ('pr-smoke','standard','release')),
    status          text not null default 'present' check (status in ('present','expired')),
    primary key (run_id, attempt, s3_uri),
    foreign key (run_id, attempt) references tevv.runs (run_id, attempt) on delete cascade
);

create table tevv.datasets (
    dataset_id  text primary key,
    s3_uri      text not null,
    checksum    text not null,
    description text,
    provenance  text,
    created_at  timestamptz not null default now()
);

-- Latest attempt per logical run.
create view tevv.v_run_summary as
select r.*, (r.ended_at - r.started_at) as duration
from tevv.runs r
where r.attempt = (select max(r2.attempt) from tevv.runs r2 where r2.run_id = r.run_id);

-- Requirements → suites → latest gate evidence. The generated verification matrix (spec §2.3, §9.1).
create view tevv.v_verification_matrix as
select req.req_id, req.title, s.suite_id, s.tier,
       g.gate, g.passed, r.run_id, r.attempt, r.ended_at
from tevv.requirements req
join tevv.suites s on req.req_id = any (s.requirement_ids)
left join tevv.v_run_summary r on r.suite_id = s.suite_id
left join tevv.gate_results g on (g.run_id, g.attempt) = (r.run_id, r.attempt)
                             and req.req_id = any (g.requirement_ids);

-- Which component versions flew in which runs (algo A/B queries).
create view tevv.v_component_history as
select c.component_id, c.repo, c.sha, c.image_digest,
       r.run_id, r.attempt, r.suite_id, r.verdict, r.started_at
from tevv.components c
join tevv.runs r using (run_id, attempt);
```

- [ ] **Step 2: Start throwaway Postgres and apply**

```bash
docker run -d --name tevv-ddl-test -e POSTGRES_PASSWORD=test postgres:16
until docker exec tevv-ddl-test pg_isready -U postgres >/dev/null 2>&1; do sleep 1; done
docker exec -i tevv-ddl-test psql -U postgres -v ON_ERROR_STOP=1 < schemas/registry.sql
```

Expected: stream of `CREATE SCHEMA/TABLE/INDEX/VIEW`, exit 0.

- [ ] **Step 3: Assert structure**

```bash
docker exec tevv-ddl-test psql -U postgres -tAc \
  "select count(*) from information_schema.tables where table_schema='tevv' and table_type='BASE TABLE'"
docker exec tevv-ddl-test psql -U postgres -tAc \
  "select count(*) from information_schema.views where table_schema='tevv'"
```

Expected: `9` then `3`.

- [ ] **Step 4: Assert (run_id, attempt) identity behaves**

```bash
docker exec tevv-ddl-test psql -U postgres -v ON_ERROR_STOP=1 -c "
insert into tevv.suites (suite_id,name,tier,omega_repo,omega_path,omega_digest)
values ('s1','smoke','L1','r','p','d');
insert into tevv.runs (run_id,attempt,suite_id,tier,trigger_kind,platform,manifest_uri)
values ('01J1',1,'s1','L1','pr-smoke','{}','s3://tevv-runs/2026/07/01J1/manifest.json'),
       ('01J1',2,'s1','L1','pr-smoke','{}','s3://tevv-runs/2026/07/01J1/manifest.json');"
docker exec tevv-ddl-test psql -U postgres -c "
insert into tevv.runs (run_id,attempt,suite_id,tier,trigger_kind,platform,manifest_uri)
values ('01J1',2,'s1','L1','pr-smoke','{}','s3://tevv-runs/2026/07/01J1/manifest.json');" ; echo "dup-exit=$?"
docker exec tevv-ddl-test psql -U postgres -tAc "select attempt from tevv.v_run_summary where run_id='01J1'"
```

Expected: first insert OK; duplicate prints `duplicate key value violates unique constraint "runs_pkey"` and `dup-exit=1`; view returns `2` (latest attempt only).

- [ ] **Step 5: Teardown, validate, commit**

```bash
docker rm -f tevv-ddl-test
bash tools/validate_docs.sh
git add schemas/registry.sql
git commit -m "feat(schemas): add tevv registry DDL (9 tables, 3 views, (run_id,attempt) identity)

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: Nine ADRs

**Files:**
- Create: `architecture/adr/ADR-001-pipeline-first-approach.md`
- Create: `architecture/adr/ADR-002-retire-elasticsearch.md`
- Create: `architecture/adr/ADR-003-mcap-bag-format.md`
- Create: `architecture/adr/ADR-004-namespace-per-run.md`
- Create: `architecture/adr/ADR-005-comms-in-k8s.md`
- Create: `architecture/adr/ADR-006-postgres-now-clickhouse-later.md`
- Create: `architecture/adr/ADR-007-tiered-triggers.md`
- Create: `architecture/adr/ADR-008-osmo-orchestrator.md`
- Create: `architecture/adr/ADR-009-jenkins-ci.md`
- Test: validator ADR-structure check

**Interfaces:**
- Consumes: baseline spec §2.2 decision table, §5.4, §7, §9, §10; OSMO variant spec (all sections); Jenkins CI spec §2 and §14.1; both review disposition docs.
- Produces: ADR numbers cited by every architecture doc (Tasks 7–11) exactly as filenames above.

- [ ] **Step 1: Write all eight ADRs**

Each file: H1 title, one-line Status/Date line (`Status: accepted · Date: 2026-07-10`), then exactly `## Context`, `## Options`, `## Decision`, `## Consequences`. Content per ADR (keep each ≤ 60 lines):

1. **ADR-001 pipeline-first:** Context = need automated+manual TEVV on on-prem K8s at pilot scale. Options = A pipeline-first (Argo-native, thin registry), B control-plane service (REST API owns runs), C federated GitOps (per-repo pipelines, shared schema). Decision = A; conductor-as-Argo-step refinement from the external review; the thin run-registry is the only persistent new component. Consequences as baseline. **Status note in the ADR:** orchestration mechanics superseded by ADR-008 (OSMO) — the domain principles (thin domain platform, registry as system of record, compiler as single entry) stand; the Argo mechanics remain the documented fallback (`--target k8s`).
2. **ADR-002 retire ES:** Context = ES served run-summaries + would serve log search; documented disk-watermark incident silently dropped results; JVM/ILM operational cost. Options = keep ELK; ES for logs only; retire. Decision = retire — run summaries are relational (joins to requirements/gates/components), Loki covers label-scoped log search, Grafana replaces Kibana, Alloy replaces Logstash. Consequences = `ingest_to_es.py` becomes registry writer, TEVV dashboard re-points to Postgres; escape hatch = Alloy tee to corporate SIEM.
3. **ADR-003 MCAP:** Options = rosbag2 sqlite3 default vs MCAP plugin. Decision = MCAP (self-describing, Foxglove streams via HTTP Range from MinIO presigned URLs, no download-then-open). Consequences = recorder uses `--storage mcap` + split-by-size for rolling upload; CORS + presigned URL requirement (spec §10).
4. **ADR-004 namespace-per-run:** Options = shared namespace + labels; namespace-per-run; vcluster. Decision (baseline) = namespace-per-run `tevv-run-<run_id>`. **Status note in the ADR:** superseded for cluster runs by ADR-008 — OSMO runs groups in a shared workflow namespace, so isolation moves to group-UUID-scoped NetworkPolicies (peer-allow within group, egress to data plane, default-deny) delivered via group templates; containment rationale (unicast rendezvous) unchanged and doubly enforced.
5. **ADR-005 comms-in-k8s:** Context = DDS multicast discovery does not survive K8s pod networking. Options = host networking; multicast CNI; unicast-rooted profiles. Decision = per-run comms infra: zenoh router OR FastDDS discovery server behind a per-run ClusterIP Service, participants dial-wait via init containers; profile chosen per omega `platform.comms`; P1 benchmark picks the default (spec §16). Consequences = comms axis is first-class; certification suite covers both.
6. **ADR-006 postgres-now:** Options = Postgres only; ClickHouse only; both now; Postgres now + ClickHouse behind telemetry-writer interface later. Decision = the last; triggers: cross-run queries >~5 s or >~1e7 telemetry rows/day. Consequences = summaries relational from day one; high-rate timeseries stays in MCAP until the trigger fires.
7. **ADR-007 tiered triggers:** Context = 1–2 GPU sim slots vs per-commit sim ambition. Options = per-commit sim (queue absorbs); per-commit with auto-cancel; manual-only; tiered by event. Decision = tiered (commit → L0 CPU-only, PR → L1 smoke, merge → L2, nightly/on-demand → L3/sweeps) + auto-cancel of superseded PR runs. **Mechanism per ADR-008:** KAI pools + priorities (HIGH/NORMAL/LOW-preemptible) replace the baseline's partitioned semaphores; auto-cancel = registry lookup by `trigger_ref`+suite → exact-ID OSMO cancel. Consequences = GPU slots reserved for closed-loop value; trigger table is config, not code.
8. **ADR-008 osmo-orchestrator:** Context = evaluation of NVIDIA OSMO (v6.3.x, Apache-2.0, self-hosted) against the Argo baseline; gang-scheduled groups + `{{host:}}` DNS + KAI preemption fit the run topology natively; externally reviewed 2026-07-13. Options = keep Argo baseline; adopt OSMO; adopt OSMO with Argo as fallback target. Decision = the last: OSMO is the orchestration layer (control plane + backend agent + KAI; `tevv submit` replaces Argo Events; conductor slims to sim-time authority as group lead; `ignoreNonleadStatus: false`; exit-code contract 0/1/42/137/3006; registry-derived attempts with attempt-scoped artifact prefix), gated on the P1 spike (variant spec §13) with `--target k8s` as the exit ramp. Consequences = real preemption and gang scheduling for free, plus OSMO UI/port-forward; cost = operating Postgres+Redis+control-plane+agent+KAI, undocumented Kyverno/air-gap posture (certification-suite gates), no webhook triggering (thin submit client owns it). Supersedes ADR-001 orchestration mechanics and ADR-004 for cluster runs.
9. **ADR-009 jenkins-ci:** Context = GitHub Actions self-hosted runners are the current CI plane, but the declared end state is an air-gapped artifact store and, by implication, an on-prem SCM — which GitHub Actions structurally cannot follow, since GitHub webhooks cannot reach an air-gapped Jenkins. Prior operational experience with self-hosted runners is unbounded accumulation of images, workspaces and logs. Options = (a) retain GitHub Actions and defer the air-gap conflict; (b) Jenkins with static agents on the controller box; (c) Jenkins with dynamic Kubernetes pod agents. Decision = (c): ephemeral pods structurally eliminate workspace and log accumulation on agents, the BuildKit sidecar keeps per-build layers out of node image stores and dies with the pod, and Jenkins is self-hosted and SCM-agnostic so it survives the air-gap transition. Builder = **BuildKit, not Kaniko** (archived 2025-06-03, maintainers retired; an unmaintained builder holding registry push credentials is an unacceptable posture), placed as a per-pod sidecar rather than a shared cluster builder so ephemerality survives, at the cost of warm-cache speed. Coupled decisions: fire-and-forget coupling (Jenkins ends at `submit`; the conductor owns the verdict — Jenkins CI spec §9.2); `tevv-compile` as sole reader of contract YAML with CI consuming `ci.json` (§5); agent pods scheduled by KAI under a capped queue (§7.1). Consequences = **CI relocates from outside the cluster to inside it**, creating scheduler, disk and image-cache contention absent from the prior design and requiring Jenkins CI spec §7 and §8 in full; the BuildKit substitution costs a namespaced pod-security exception and the loss of Kaniko's 336h cache TTL, which the registry retention policy must now supply by configuration; a new stateful SPOF is co-located with kube-master and the OSMO control plane, mitigated by JCasC rebuildability; baseline §7's ephemeral-storage rule widens to build pods. Supersedes the GHA assumption in baseline §3 and variant §3. ADR-007 (tiered triggers) and ADR-008 (orchestration) are unaffected.

- [ ] **Step 2: Validate + commit**

Run: `bash tools/validate_docs.sh` → `OK: all checks passed` (ADR section check now active).

```bash
git add architecture/adr/
git commit -m "docs(adr): record ADR-001..008 (approach, ES retirement, MCAP, ns-per-run, comms, DB, triggers, OSMO)

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: Standalone diagram sources — run lifecycle, compiler flow, OSMO data flow

**Files:**
- Create: `diagrams/run-lifecycle.mermaid`
- Create: `diagrams/omega-compile-flow.mermaid`
- Create: `diagrams/system-data-flow-osmo.mermaid`
- Modify: `diagrams/system-architecture-osmo.mermaid` (GHA node → Jenkins; split controller from agent pods)
- Modify: `diagrams/system-architecture.mermaid` (same substitution in the Argo fallback record)
- Test: dockerized mmdc via validator
- Note: `diagrams/jenkins-ci-flow.mermaid` already exists (committed with the Jenkins CI spec, mmdc-verified) — do not recreate it

**Interfaces:**
- Consumes: baseline spec §8 state machine (verbatim states/transitions — unchanged by the variant), §5.2 pipeline; OSMO variant spec §5/§7/§8.
- Produces: sources embedded verbatim by Task 9 (07-lifecycle), Task 8 (03-compiler), and Task 7 (00-overview sync-checks `system-architecture-osmo` + `system-data-flow-osmo`). **The two `Modify:` steps must complete before Task 7**, because Task 7 embeds `system-architecture-osmo.mermaid` under a sync check — embedding the pre-Jenkins version would make the check pass against a stale diagram.

- [ ] **Step 1: Write `diagrams/run-lifecycle.mermaid`**

Copy the `stateDiagram-v2` block from spec §8 verbatim (states PENDING→…→PASSED/FAILED, side exits INFRA_FAILED with retry ≤2 back to PENDING, ABORTED from PENDING/RUNNING), prefixed with two `%%` comment lines naming source-of-truth spec section.

- [ ] **Step 2: Write `diagrams/omega-compile-flow.mermaid`**

```text
%% tevv-compile pipeline — spec §5.2
graph LR
  IN1["omega.yaml<br/>(suite)"] --> V
  IN2["tevv.yaml ×N<br/>(components)"] --> V
  IN3["trigger params<br/>(sha, tier, matrix, priority)"] --> V
  V["schema validation<br/>(JSON Schema)"] --> R["component resolution<br/>pin Harbor digests"]
  R --> I["interface check<br/>wiring + QoS compatibility"]
  I --> F["fault capability check<br/>vs profile fault_capabilities"]
  F --> M["matrix expansion<br/>run_ids allocated"]
  M --> B1["run bundle ×N<br/>--target osmo (primary)"]
  M --> B2["run bundle ×N<br/>--target compose"]
  M --> B3["run bundle ×N<br/>--target k8s (Argo fallback)"]
  B1 --> OUT1["OSMO workflow YAML: group, lead conductor,<br/>ignoreNonleadStatus false, exitActions, host tokens<br/>+ sim/autopilot/comms config + evaluation.yaml + manifest.json"]
  B2 --> OUT2["compose files + same configs<br/>+ manifest.json"]
  B3 --> OUT3["K8s manifests/Argo params + same configs<br/>+ manifest.json"]
```

- [ ] **Step 3: Write `diagrams/system-data-flow-osmo.mermaid`**

```text
%% End-to-end automated run flow — OSMO variant
%% Source of truth: docs/superpowers/specs/2026-07-13-autonomy-tevv-osmo-orchestration-design.md §5, §7, §8
graph LR
  PUSH["git push / PR"] --> CI["GitHub Actions<br/>build, unit test, push to Harbor"]
  CI --> SUB["tevv submit<br/>compile; cancel superseded runs via registry<br/>lookup + exact-ID OSMO cancel; POST workflows"]
  SUB -->|"PENDING rows (run_id, attempt=1)"| REG[("tevv registry<br/>system of record")]
  SUB --> API["OSMO API + workflow engine"]
  API --> Q["KAI queue<br/>pools, priorities HIGH | NORMAL | LOW"]
  Q -->|"gang-schedule group<br/>ignoreNonleadStatus: false"| GRP["run group in shared namespace<br/>conductor (lead) + sim + SITL ×N<br/>+ middleware + comms + stack + harness"]
  GRP -->|"go-barrier → mission → faults<br/>conductor stamps states"| RUN["RUNNING (sim time authoritative)"]
  RUN -->|"rolling segment upload<br/>…/&lt;run_id&gt;/&lt;attempt&gt;/bags/"| S3[("MinIO")]
  RUN --> EVAL["evaluators read MCAP<br/>gate results → registry"]
  EVAL -->|"lead exit 0 | 1"| VERD["PASSED | FAILED<br/>(FAILED never retried)"]
  RUN -->|"exit 42 | 137 | 3006 | non-lead death"| RESCH["OSMO reschedule ≤ 2<br/>attempt + 1; janitor classifies<br/>from OSMO task statuses"]
  RESCH --> Q
  VERD --> REG
  REG --> PRFB["PR status + verdict comment<br/>posted by conductor, janitor backstop"]
  REG --> GRAF["Grafana dashboards +<br/>Foxglove deep links (workflow_ref)"]
```

- [ ] **Step 4: Amend `diagrams/system-architecture-osmo.mermaid` for the Jenkins CI plane**

Three exact edits. All were applied and mmdc-verified at plan-amendment time.

Replace the `GHA` node inside `subgraph TP`:

```text
    JC["Jenkins controller<br/>multibranch + JCasC, webhook in,<br/>milestone() supersedes older builds"]
```

Insert a new node immediately **before** the `TC["tevv-compile container …` line (agent pods are in-cluster — that relocation is the whole point of the Jenkins spec):

```text
  JAG["Jenkins agent pods — cpu nodes, KAI queue ci<br/>buildctl + buildkitd sidecar → digest,<br/>unit tests, tevv submit; schedulerName kai-scheduler"]
```

Replace the two `GHA` edges with three:

```text
  JC -->|spawns ephemeral agent pods| JAG
  JAG -->|push images by digest| HARBOR
  JAG --> TC
```

Verify no `GHA` token remains: `grep -c GHA diagrams/system-architecture-osmo.mermaid` must print `0`.

- [ ] **Step 5: Amend `diagrams/system-architecture.mermaid` (Argo fallback record)**

Same substitution, keeping the Argo webhook path. Replace the `GHA` node with two nodes:

```text
    JC["Jenkins controller<br/>multibranch + JCasC, webhook in"]
    JAG["Jenkins agent pods (cpu nodes)<br/>buildctl + buildkitd sidecar, unit tests, fire webhook"]
```

Replace the two `GHA` edges with three:

```text
  JC -->|spawns ephemeral agent pods| JAG
  JAG -->|push images| HARBOR
  JAG -->|webhook| AE
```

Verify: `grep -c GHA diagrams/system-architecture.mermaid` must print `0`.

- [ ] **Step 6: Validate + commit**

Run: `bash tools/validate_docs.sh` → `OK: all checks passed` (three new diagrams plus both amended ones compile under mmdc).

```bash
git add diagrams/
git commit -m "docs(diagrams): add run-lifecycle, omega-compile-flow, OSMO data-flow; swap GHA for Jenkins

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: Architecture docs 00 + 01 (overview, taxonomy)

**Files:**
- Create: `architecture/00-system-overview.md`
- Create: `architecture/01-test-taxonomy.md`
- Test: validator (incl. the 00-overview↔diagrams sync check, which activates in this task)

**Interfaces:**
- Consumes: spec §1–§4, §13; `diagrams/system-architecture.mermaid`, `diagrams/system-data-flow.mermaid`.
- Produces: doc numbering/titles other docs cross-link (`00-system-overview.md`, `01-test-taxonomy.md`).

- [ ] **Step 1: Write `architecture/00-system-overview.md`**

Required structure (H1 title; H2 sections): `## Purpose`, `## Planes`, `## System architecture diagram`, `## End-to-end data flow`, `## Manual path`, `## Phasing`, `## Document map`. Must cover: the planes per **variant spec §3** (trigger plane = submission clients + `tevv submit`; OSMO control plane; compute cluster with backend agent + KAI + run plane; data plane; presentation plane) with one paragraph each; the two **OSMO** diagrams embedded as fenced `mermaid` blocks, each **preceded by its sync marker comment** exactly:

```markdown
<!-- sync:diagrams/system-architecture-osmo.mermaid -->
```mermaid
(verbatim contents of diagrams/system-architecture-osmo.mermaid)
```
```

(and likewise `<!-- sync:diagrams/system-data-flow-osmo.mermaid -->`); a short "Argo-native baseline" note pointing at `diagrams/system-architecture.mermaid`/`system-data-flow.mermaid` and the baseline spec as the documented fallback (`--target k8s`, ADR-008 exit ramp); manual path incl. `tevv upload` and records-nothing-by-default; the P0–P3 table from **variant §13** (P1 = OSMO control plane + spike; spike items listed verbatim), **merged with the CI-plane column from Jenkins CI spec §13** so each phase shows orchestration and CI side by side (P0 = pull-through cache + Jenkins controller + `--emit-ci` + build stage; P1 = `unit_tests` schema, suite resolution, submit stage, agent KAI queue + taints, sim-image pinning; P2 = sampled matrix, registry retention with the `components` veto; P3 = SCM relocation on-prem and the air-gap posture for the webhook surface) — with the note that P0 of the CI plane depends on **nothing** from the OSMO spike, because the CI plane is orchestrator-agnostic and a fallback to Argo changes only the submit stage's target; a document map table linking docs 01–10 + ADR index + all three specs + both review dispositions. Every design statement must match the specs (variant wins on orchestration); cite ADR-001/ADR-007/ADR-008 where the shape/trigger/orchestrator decisions appear.

- [ ] **Step 2: Write `architecture/01-test-taxonomy.md`**

Required structure: `## Tiers`, `## Modality per tier`, `## Trigger mapping`, `## Compute classes`, `## Requirement traceability`. Must cover: the L0–L3 table verbatim from baseline spec §4 (scope/modality/trigger/compute); the tier→pool/priority mapping from variant §4 (L1 HIGH, L0/L2 NORMAL, L3/sweeps LOW-preemptible; preempted runs reschedule as new attempts); why **Jenkins agent pods only build + run unit tests** (all verdicts go through the orchestrator into one registry — baseline §4, and the boundary is now schema-enforced: `verifies:` is prohibited inside `unit_tests`, Jenkins CI spec §6.2); that Jenkins is **fire-and-forget** — a green build means *submitted*, not *passed*, and the conductor owns the verdict (Jenkins CI spec §9.2); node classes `gpu-sim`/`gpu-inference`/`cpu` as KAI pool node classes; how suites declare `verifies:` REQ ids and how `tevv.v_verification_matrix` generates the matrix (reference `schemas/registry.sql` view by name); a `graph LR` mermaid mapping git events → tiers (commit→L0, PR→L1, merge→L2, nightly/on-demand→L3).

- [ ] **Step 3: Validate + commit**

Run: `bash tools/validate_docs.sh` → `OK: all checks passed` (sync check must pass — if it fails, the inline block diverged from the standalone file; fix by re-copying verbatim).

```bash
git add architecture/
git commit -m "docs(arch): add 00-system-overview and 01-test-taxonomy

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: Architecture docs 02 + 03 (omega contract, compiler)

**Files:**
- Create: `architecture/02-omega-contract.md`
- Create: `architecture/03-scenario-compiler.md`
- Test: validator

**Interfaces:**
- Consumes: spec §5, §6; `schemas/omega.schema.json`, `schemas/tevv.schema.json`, example YAMLs; `diagrams/omega-compile-flow.mermaid`.
- Produces: the normative field-name documentation later handoffs quote.

- [ ] **Step 1: Write `architecture/02-omega-contract.md`**

Required structure: `## Config model`, `## omega.yaml reference`, `## tevv.yaml reference`, `## Fault timeline`, `## Evaluation gates`, `## Matrix expansion`, `## Platform axes and contract surface`, `## Time base`. `## Matrix expansion` must document **both modes**: `product` (default, cross-product of explicit lists) and `sample` (Monte Carlo — `samples`, mandatory `seed`, the four distributions `normal|uniform|loguniform|choice`, the `samples` cap), and must state the two provenance rules from Jenkins CI spec §6.1: the per-run seed is **derived** as `run_seed = H(seed, run_index)` so sampling is append-only reproducible (raising `samples` never perturbs earlier draws), and each run's resolved draw lands in `manifest.json` **and** `runs.matrix_point` — which is already `jsonb` + GIN in `schemas/registry.sql`, so sampled sweeps are queryable with no registry schema change. The `## tevv.yaml reference` section must document `unit_tests` alongside `l0_tests` and state the boundary explicitly: `l0_tests` produce registry verdicts, `unit_tests` never do, and `verifies:` inside `unit_tests` is a schema error by design. Must cover: three-layer merge order (omega → tevv → trigger params); field-by-field reference tables generated from the two schemas (field, type, required, meaning — every property in `schemas/*.schema.json` appears, names identical); the worked example embedded (`schemas/examples/l1-planner-substack-smoke.omega.yaml` verbatim in a yaml fence); fault anchors (`t+Ns`, `waypoint:N`, `run_state:*`), the four backends table from spec §6 (userspace mavlink-proxy for link; tc netem deferred → cite spec §16); fault-scoped gates `during:`/`after:`; the 4 axes table with per-target rendezvous rendering — comms profiles resolve to a per-run ClusterIP Service under `--target k8s` but to `{{host:<task>}}` DNS tokens under `--target osmo` (cite ADR-005, variant §5); contract topic surface list; time-base requirement (sim-time authoritative, lockstep, RTF floor → INFRA_FAILED, wall-clock only for phase budgets); origin preset resolving OriginGeopoint + autopilot home together.

- [ ] **Step 2: Write `architecture/03-scenario-compiler.md`**

Required structure: `## Role`, `## Pipeline`, `## Run bundles`, `## Targets (osmo, compose, k8s)`, `## Provenance manifest`, `## Validation stages and phasing`, `## Relationship to generate_scenario.py`. Must cover: embed `diagrams/omega-compile-flow.mermaid` inline (plain copy, no sync marker needed — the sync check covers only 00-overview); bundle contents list from baseline §5.2 (sim config via existing Jinja partials, autopilot params, mavlink-router/MAVROS/pymavlink config, comms config, evaluation.yaml + bag allowlist, manifest.json with every input digest + compiler version + rendered OSMO workflow digest); the **`--target osmo` renderer mapping table from variant §5** (workflow per run named run_id; single group, `ignoreNonleadStatus: false`; lead conductor; `{{workflow_id}}` injected for `workflow_ref`; `{{host:}}` rendezvous; `files:` injection; exitActions per the §8 exit-code contract; no labels — suite/tier/pr/sha live in the registry only); `--target compose` makes today's four scenarios presets; `--target k8s` retained as the Argo fallback (ADR-008); a `## Resolve mode (--emit-ci)` section covering Jenkins CI spec §5 — `--emit-ci` runs pipeline stages 1–4 (schema validation, component resolution with digest pinning, interface/QoS checks, fault-capability checks) and emits `ci.json` **without** matrix expansion or bundle rendering, and the rule that `tevv-compile` is the **sole reader** of `omega.yaml`/`tevv.yaml` (CI never parses contract YAML, because raw omega is pre-merge and pre-override — a stage reading it acts on values the run will not use, with no failure signal); reference `schemas/ci.schema.json` (Task 14) by name for the field list; validation order with phasing (schema+digest+wiring/type in P1, QoS compatibility in P2); compiler is a versioned container with unit+golden tests, `--self-test`, and the compose/osmo equivalence test (variant §12); explicit lineage from `M-S-Simulation-Runtime-Stack/tools/generate_scenario.py` (what is kept: Jinja templates, `--check` drift, `--self-test`; what changes: multi-target emission, schema validation, digest pinning).

- [ ] **Step 3: Validate + commit**

Run: `bash tools/validate_docs.sh` → `OK: all checks passed`

```bash
git add architecture/
git commit -m "docs(arch): add 02-omega-contract and 03-scenario-compiler

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: Architecture docs 04 + 07 (run plane, lifecycle/failures)

**Files:**
- Create: `architecture/04-run-plane.md`
- Create: `architecture/07-lifecycle-failures.md`
- Test: validator

**Interfaces:**
- Consumes: variant spec §7, §8 (primary); baseline §7, §8 for what carries forward; `diagrams/run-lifecycle.mermaid`; ADR-004, ADR-005, ADR-008.
- Produces: conductor responsibilities list quoted by P1-era handoffs.

- [ ] **Step 1: Write `architecture/04-run-plane.md`**

Required structure: `## Submission`, `## OSMO control plane`, `## Run group and tasks`, `## Run-conductor (lead task)`, `## Node classes and scheduling`, `## Concurrency, preemption, and auto-cancel`, `## Isolation in the shared namespace`, `## Comms inside a run`. Must cover: `tevv submit` = compile + registry PENDING rows + per-run POST to the OSMO API, identical from CI job / CLI / CronJob (OSMO has no webhook or cron triggering — cite variant §7); OSMO control-plane components (API server, engine, Postgres `osmo` db + Redis, backend agent over WebSocket, KAI) and what each owns; the group spec (one workflow per run named run_id, single gang-scheduled group, `ignoreNonleadStatus: false` with the solo-restart-hazard rationale from the review disposition, container-ready barrier); full task list from baseline §7 + ephemeral-storage limits rule; **conductor slimmed to sim-time authority** — keeps semantic go-barrier, watchdog, phase budgets, evaluation orchestration, registry writes, PR feedback; sheds namespace creation, bundle apply, teardown, retries (owned by OSMO) — "container running ≠ sim answering RPC"; KAI pools/priorities table (tier mapping from variant §4) replacing partitioned semaphores, with the preemption-as-reschedule semantics; auto-cancel = registry lookup by `trigger_ref`+suite → exact-ID OSMO cancel (no workflow labels exist — cite the OSMO review disposition finding 2); isolation: shared workflow namespace, group-UUID-scoped NetworkPolicy (peer-allow within group / egress to data plane / default-deny; delivered via group templates with `{{WF_GROUP_UUID}}`), OSMO's internet-egress policy disabled; comms rendezvous via `{{host:}}` tokens + dial-wait (zenoh router | FastDDS DS — cite ADR-005); a `graph TD` mermaid of one run group's tasks and their data-plane egress.

- [ ] **Step 2: Write `architecture/07-lifecycle-failures.md`**

Required structure: `## State machine`, `## The FAILED vs INFRA_FAILED invariant`, `## Transition ownership`, `## Exit-code contract`, `## Attempts and retries`, `## Go-barrier`, `## Watchdog and probe rule`, `## Cleanup and reconciliation`, `## Failure taxonomy and platform SLO`, `## PR feedback`. Must cover: embed `diagrams/run-lifecycle.mermaid` verbatim (states unchanged by the variant); the invariant sentence; the **transition-ownership table from variant §8** (KAI schedules, conductor stamps, OSMO exitActions reschedule, janitor reconciles); the **lead exit-code contract table verbatim from variant §8** (0/1/42/137/3006/non-lead-death rows) and how exitActions encode "FAILED is never retried"; attempts: **registry-derived** — conductor allocates `attempt = max+1` (no OSMO retry token exists — cite the OSMO review disposition finding 1), serves `{run_id, attempt}` to harness tasks over `{{host:<conductor>}}`, artifacts land under the attempt-scoped prefix; barrier checklist (sim RPC, SITL heartbeats, MAVROS connected, contract topic rates, `/clock` ticking, TF sane) layered on top of OSMO's container-ready barrier; watchdog measured against sim time + RTF floor; the probe rule (readiness for ordering only, **no liveness probes on sim-time-dependent pods**, conductor is liveness authority); how this design absorbs the AirSim-bridge no-reconnect defect (mid-run sim death fails the gang cleanly under `ignoreNonleadStatus: false`; reschedule = fresh attempt, no in-run reconnect logic); cleanup: conductor SIGTERM-grace diagnostics snapshot; janitor reconciler closes rows from OSMO task statuses when the lead is killed (preempted vs crashed → `failure_reason`), replacing the baseline namespace sweeper; the `failure_reason` table incl. `mission_timeout → FAILED` and derived `flaky_suspect` quarantine; INFRA_FAILED-rate SLO (<5%) counting attempts incl. preemptions; PR feedback content (status per suite, gate table, deep links incl. OSMO workflow URL via `workflow_ref`) posted by the conductor with janitor backstop.

- [ ] **Step 3: Validate + commit**

Run: `bash tools/validate_docs.sh` → `OK: all checks passed`

```bash
git add architecture/
git commit -m "docs(arch): add 04-run-plane and 07-lifecycle-failures

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 10: Architecture docs 05 + 06 (data plane, observability)

**Files:**
- Create: `architecture/05-data-plane.md`
- Create: `architecture/06-observability.md`
- Test: validator

**Interfaces:**
- Consumes: spec §9, §10; `schemas/registry.sql` (table/column names are normative); ADR-002/003/006.
- Produces: bucket layout + label taxonomy quoted by P0 handoffs.

- [ ] **Step 1: Write `architecture/05-data-plane.md`**

Required structure: `## Registry`, `## OSMO backing services`, `## Object store layout and retention`, `## Recorder storage`, `## Logging topology`, `## Metrics`, `## Growth path`. Must cover: every table of `schemas/registry.sql` documented in a Markdown table (Type / Nullable / Indexing Strategy / Scale-Retention per repo formatting rules — column names copied from the DDL, incl. `trigger_kind` and `workflow_ref`); the three views and what queries they serve; CloudNativePG single instance + WAL to MinIO; OSMO backing services (`osmo` db on the same CNPG cluster with separate role, Redis, `workflow_log`/`workflow_data` buckets) with the rule that OSMO state is internal — no TEVV component reads OSMO tables (variant §9); the **attempt-scoped layout** `tevv-runs/<yyyy>/<mm>/<run_id>/<attempt>/{bags,eval,video,logs,manifest.json}` with the reschedule-overwrite rationale (review disposition finding 1) and manual upload = attempt 1; the MinIO tree (baseline §9.2 amended with the attempt segment) + retention classes mapped to `artifacts.retention_class` values (`pr-smoke`, `standard`, `release`); recorder rolling-segment upload design and why upload-at-exit was rejected (cite review disposition finding 2); logging: stdout-only rule, Alloy relabeling, static labels `{component, tier}` + `run_id` as Loki structured metadata (Loki ≥3), packaged Grafana query, failure log export to S3, autopilot ulog/BIN artifacts, no-/rosout-aggregator rationale, manual-path local Loki + upload archive; metrics: run-scoped exporters + PodMonitor-first-in-PREPARING, run_id label budget, gates-from-MCAP-only rule, Pushgateway = batch final states only; post-process = **pluggable evaluator containers** (declared per suite / per-repo tevv.yaml) that read the fresh MCAP from MinIO and write gate results + metric summaries — devs add metrics by shipping an evaluator image, never by patching the platform (spec §9.4); telemetry-writer interface + ClickHouse triggers (cite ADR-006).

- [ ] **Step 2: Write `architecture/06-observability.md`**

Required structure: `## Grafana`, `## OSMO UI`, `## Foxglove and Lichtblick`, `## Pixel streaming`, `## Dashboards inventory`. Must cover: three datasources and the four dashboards (live run, post-run report, N-run/sweep comparison, platform health incl. INFRA_FAILED SLO trend, queue depth, pool utilization); the OSMO web UI/CLI as supplementary live view (task states, queue position, log streaming) with deep links from `workflow_ref` — never a verdict source; live foxglove-bridge per run behind ingress `/runs/<id>/ws` via a group-template Service, plus `osmo workflow port-forward` for the dev loop; post-run MCAP via presigned MinIO URLs + bucket CORS exposing `Range` (cite baseline review disposition finding 10 + ADR-003); pixel streaming optional/UE-plugin-gated, WebRTC-not-through-ingress note pointing at baseline §16 deferred decision; Grafana annotations from `tevv.fault_events`.

- [ ] **Step 3: Validate + commit**

Run: `bash tools/validate_docs.sh` → `OK: all checks passed`

```bash
git add architecture/
git commit -m "docs(arch): add 05-data-plane and 06-observability

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 11: Architecture docs 08 + 09 (security/DevSecOps, platform self-test)

**Files:**
- Create: `architecture/08-security-devsecops.md`
- Create: `architecture/09-platform-self-test.md`
- Test: validator

**Interfaces:**
- Consumes: spec §11, §12.
- Produces: the four integration surfaces referenced by tasks/README (Task 12).

- [ ] **Step 1: Write `architecture/08-security-devsecops.md`**

Required structure: `## Integration surfaces`, `## Artifact boundary (Harbor)`, `## Admission and network policy`, `## Secrets and identity`, `## MLSecOps pattern`, `## CI plane surface`, `## Air-gap readiness`. The new `## CI plane surface` section covers Jenkins CI spec §10: the **new inbound webhook endpoint** (HMAC-verified — GitHub Actions required no inbound surface, so this is net-new exposure and is the component whose exposure must be revisited at air-gap time); Jenkins credentials resolved through **External Secrets Operator, never `JENKINS_HOME`** (registry push, K8s ServiceAccount, OSMO API token, registry Postgres role), with Jenkins behind the same OIDC SSO as Grafana/MinIO/OSMO; that agent pods still carry **no Docker socket, no `privileged: true` and no host mounts**, but that the rootless `buildkitd` sidecar **does** require a documented relaxation — `seccompProfile: Unconfined` + `appArmorProfile: Unconfined` and `--oci-worker-no-process-sandbox`, whose upstream warning states it lets build containers kill or ptrace arbitrary processes in the BuildKit host namespace (bounded here to the sidecar's own container, since the pod is single-tenant and ephemeral). Document the grant as a **namespaced** PSA exception plus a Kyverno rule scoped to the CI namespace and to the `buildkitd` image by digest, and state explicitly that run-plane namespaces keep the unmodified `restricted` posture. Name the `userns` variant as the alternative and why it is deferred (needs the `UserNamespacesSupport` feature gate on kubelet **and** kube-apiserver); and — stated as an explicit open risk, not glossed — that **Kyverno signed-image admission is currently unenforceable** if the cluster pulls from Docker Hub rather than an internal registry, because cosign signatures, Trivy scans and syft SBOMs are produced at push to that registry (Jenkins CI spec §12; the pull-through cache is the remedy). `## Air-gap readiness` must additionally note that GitHub webhooks cannot reach an air-gapped Jenkins, so the SCM relocation is a sequenced prerequisite rather than a surprise. Must cover: the four surfaces (inbound = `tevv submit` + the OSMO REST API as the programmatic entry, replacing the webhook payload schema; outbound CloudEvents on transitions/verdicts incl. model-promotion gating example; read-only Postgres role + the three views by exact name; artifact surface = MinIO layout + manifest.json) and the REST-façade deferral trigger; Harbor pipeline (Trivy, syft SBOM, cosign) + Kyverno warn→enforce **including the certification-suite gate that agent-created pods pass admission** (undocumented upstream — variant §11); NetworkPolicy: group-UUID peer-allow / data-plane egress / default-deny, OSMO's bundled internet-egress policy disabled; External Secrets Operator, schema-rejected inline secrets, OIDC SSO (OSMO behind the same IdP — verify flow at P1, roadmap caveat), submit/cancel-own RBAC; models-as-components (digest refs, lineage in `tevv.components`, L0 dataset evals, drift gates); build-time supply chain (Harbor pull-through proxy + internal apt/PyPI mirrors) and air-gap: OSMO install docs assume connectivity — air-gapped control-plane install is an explicit adoption gate (variant §16).

- [ ] **Step 2: Write `architecture/09-platform-self-test.md`**

Required structure: `## Compiler tests`, `## Canary suite`, `## Scheduler and orchestrator tests`, `## Harness component certification`, `## Registry and dashboard fixtures`, `## Platform pass/fail criteria`. Must cover: unit + golden bundles (incl. `--target osmo` fixtures) + `--self-test` + compose/osmo equivalence (variant §12); canary (tiny scenario, pinned stack) runs before nightly matrix **and after every OSMO/KAI upgrade**, INFRA_FAILED ⇒ pause + page; scheduler-behavior test (a HIGH submission preempts a running LOW canary; the preempted run reschedules as attempt+1 and completes) and the P1-spike checks as regression tests (attempt isolation, group-failure reschedule under `ignoreNonleadStatus: false`, intra-group NetworkPolicy connectivity, Kyverno admission of agent-created pods); conductor/fault-injector/recorder/registry-writer versioned + certified against canary; migration tests + seed fixtures (mock-generator pattern); explicit platform gates: INFRA_FAILED rate <5%, canary green, certification suite green per profile combo.

- [ ] **Step 3: Validate + commit**

Run: `bash tools/validate_docs.sh` → `OK: all checks passed`

```bash
git add architecture/
git commit -m "docs(arch): add 08-security-devsecops and 09-platform-self-test

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 12: P0 handoff packages + tasks index

**Files:**
- Create: `tasks/README.md`
- Create: `tasks/20260710_opus_p0_registry_schema.md`
- Create: `tasks/20260710_opus_p0_tevv_cli_upload.md`
- Create: `tasks/20260710_opus_p0_data_plane_baseline.md`
- Test: validator handoff-template check

**Interfaces:**
- Consumes: everything above — handoffs copy-paste normative blocks (DDL, schemas, doc excerpts). They must be executable by an agent with zero access to this conversation.
- Produces: the P0 work packages named in `tasks/README.md`.

- [ ] **Step 1: Write `tasks/README.md`**

Index table: file, phase, target agent, deliverable, upstream sources, status (`ready`). Second table "Planned (not yet written)": P1 handoffs — OSMO control plane + KAI deployment (P1 spike runner, variant §13), compiler `--target osmo` renderer **and `--emit-ci` resolve mode**, slimmed run-conductor (lead task), `tevv submit` client, L1 smoke suite — plus the **P0 Jenkins deployment + shared-library handoff written in Task 16** (listed in the `ready` table, not the planned one) — each with its source spec/doc sections and the phase gate that unblocks writing it (P0 proof demonstrated; spike gates OSMO adoption). A note: every handoff follows CLAUDE.md §3 (five H1 headers, self-contained).

- [ ] **Step 2: Write `tasks/20260710_opus_p0_registry_schema.md`**

Five H1 sections with: **Context** — registry = system of record, P0 goal "manual compose run lands in registry"; **Upstream Specifications** — paste `schemas/registry.sql` in full + the `(run_id, attempt)` invariant paragraph from `architecture/07-lifecycle-failures.md`; **Explicit Instruction Prompt** — "Implement a Python package `tevv_registry` providing: `apply_migrations(dsn)` (idempotent, applies registry.sql as migration 0001 via a `tevv.schema_migrations` table), a typed writer API (`record_run`, `record_gate_results`, `record_metrics`, `record_artifacts`, `record_fault_events`, `finalize_run(status, verdict, failure_reason)`), and read helpers for the three views. Use psycopg 3; no ORM. Include pytest suite against a dockerized postgres:16 (testcontainers or plain docker fixture)."; **Acceptance Criteria** — migrations idempotent (second apply = no-op); duplicate `(run_id, attempt)` rejected; `v_run_summary` returns latest attempt only; writer round-trips the example omega's gates; ≥90% branch coverage on the writer; ruff + mypy clean; **Expected Output** — repo layout `tevv_registry/` (package, migrations/, tests/, pyproject.toml) delivered as a directory of code.

- [ ] **Step 3: Write `tasks/20260710_opus_p0_tevv_cli_upload.md`**

Five H1 sections: **Context** — manual compose runs become records only via `tevv upload`; quick-and-dirty records nothing; **Upstream Specifications** — paste MinIO layout tree + retention classes from `architecture/05-data-plane.md`, the `tevv_registry` writer API signatures from the previous handoff, and the metrics-collector output contract (`/metrics/outputs/metrics.json` + `evaluated.json` fields as produced by the existing stack); **Explicit Instruction Prompt** — "Implement `tevv` CLI (Python, click) with `tevv upload --run-dir <dir> --suite <id> [--trigger manual]`: parse metrics.json/evaluated.json, upload bags (`*.mcap`, or warn+skip on legacy sqlite3 `.db3`), logs archive, and manifest to `tevv-runs/<yyyy>/<mm>/<run_id>/1/…` on MinIO (boto3, path-style; manual runs are always attempt 1 — the attempt segment is mandatory in the key layout), then write registry rows via `tevv_registry`. Generate run_id ULID if absent. Synthesize a minimal manifest.json if the run dir lacks one (record inputs as unknown — `runs.manifest_uri` is NOT NULL). Print the run's Grafana URL."; **Acceptance Criteria** — end-to-end test against dockerized MinIO + Postgres fixtures: upload of a fixture run-dir produces exactly the documented S3 keys + registry rows; re-upload same run-dir is idempotent (same run_id, no duplicate rows); missing evaluated.json → registry row with status EVALUATING and a warning; **Expected Output** — `tevv_cli/` package + tests + pyproject.toml.

- [ ] **Step 4: Write `tasks/20260710_opus_p0_data_plane_baseline.md`**

Five H1 sections: **Context** — P0 data-plane baseline on the on-prem cluster; **Upstream Specifications** — paste logging topology rules (stdout-only, Alloy relabel map `tevv.io/*` → labels `{component, tier}` + structured-metadata `run_id`), MinIO bucket/lifecycle/CORS requirements (14 d `pr-smoke` prefix lifecycle, `Range`-exposing CORS), Grafana datasource list, CloudNativePG single-instance + WAL-archive-to-MinIO requirement — all from `architecture/05-data-plane.md`/`06-observability.md`; **Explicit Instruction Prompt** — "Write the Kubernetes manifests/Helm values (plain YAML, kustomize overlays allowed) for: CloudNativePG cluster (1 instance, WAL archive to MinIO bucket `tevv-pg-wal`), MinIO tenant or standalone deployment with buckets `tevv-runs`, `tevv-datasets`, `tevv-pg-wal` + lifecycle + CORS config, Loki (≥3.0, structured metadata enabled) + Alloy DaemonSet config, Grafana with the three datasources provisioned. Everything pulls from `harbor.local` image references."; **Acceptance Criteria** — `kubectl apply --dry-run=server` clean on a kind cluster; a smoke script proves: psql reachable + registry.sql applies; `mc` can put/get under both buckets; a test pod's stdout line with `tevv.io/*` labels arrives in Loki queryable by structured-metadata run_id; Grafana datasources healthy; **Expected Output** — `deploy/` directory of manifests + `deploy/README.md` runbook.

- [ ] **Step 5: Validate + commit**

Run: `bash tools/validate_docs.sh` → `OK: all checks passed` (handoff five-header check now active for the three files).

```bash
git add tasks/
git commit -m "docs(tasks): add P0 handoff packages (registry, tevv upload CLI, data-plane baseline) + index

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 13: Repo README + final coverage pass

**Files:**
- Create: `README.md`
- Modify: none expected (fixes only if coverage pass finds gaps)
- Test: full validator + manual coverage checklist

**Interfaces:**
- Consumes: everything.
- Produces: repo entry point.

- [ ] **Step 1: Write `README.md`**

Required structure: `## What this repo is`, `## Directory map`, `## Reading order`, `## Validation`, `## Provenance`. Must cover: one-paragraph platform summary (OSMO-orchestrated, Argo fallback); table mapping every top-level dir to its role (architecture/, architecture/adr/, diagrams/, schemas/, tasks/, docs/superpowers/, tools/); reading order (baseline spec → OSMO variant spec → **Jenkins CI spec** → 00 → 01 → … → 09 → **10-ci-plane** → ADRs), with a one-line note that supersession is layered and narrow (variant owns orchestration, Jenkins spec owns the trigger/CI plane, baseline owns everything else and remains the rationale record); how to run `bash tools/validate_docs.sh` and what it checks; provenance links (all three specs, both review dispositions, plan) and the note that the verification matrix is generated by `tevv.v_verification_matrix`, never hand-edited.

- [ ] **Step 2: Coverage pass against the spec**

For each section of **both** specs (baseline §1–§16 and OSMO variant §1–§16), name the artifact that carries it (00–09 docs, ADRs, schemas, handoffs) — variant sections override baseline where they conflict, and superseded baseline sections map to the ADR that records the supersession (ADR-008). Record the mapping as a table in the README under `## Provenance`. If any spec requirement has no artifact, fix the gap now (add the missing content to the right doc) before committing.

- [ ] **Step 3: Full validation**

Run: `bash tools/validate_docs.sh; echo "exit=$?"`
Expected: `OK: all checks passed`, `exit=0`.

- [ ] **Step 4: Commit**

```bash
git add README.md architecture/ tasks/
git commit -m "docs: add repo README with spec coverage map; close doc-set gaps

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 14: `ci.json` resolve contract schema

> **Execution order:** run this immediately after Task 3. Task 8's compiler doc references `schemas/ci.schema.json` by name, and Task 16's handoff embeds it.

**Files:**
- Create: `schemas/ci.schema.json`
- Create: `schemas/examples/local-planner-pr.ci.json`
- Create: `schemas/examples/invalid-ci-unit-verifies.ci.json.rejected`
- Test: positive + negative validation runs

**Interfaces:**
- Consumes: `tools/validate_yaml.py` (Task 1 — it loads YAML, and JSON is valid YAML, so it validates `.json` inputs unchanged); `schemas/tevv.schema.json` `unit_tests` (Task 3).
- Produces: `schemas/ci.schema.json` — the contract between `tevv-compile --emit-ci` and every CI stage. Top-level required keys `schema_version, compiler_version, trigger, integration_ref, build, unit, suites, submit`. Task 8 (03-compiler), Task 15 (10-ci-plane) and Task 16 (handoff) reference these exact names.

**Why this schema exists** (Jenkins CI spec §5): raw `omega.yaml` is the *unmerged, unvalidated, unresolved* document — it exists before `tevv.yaml` defaults are merged and before trigger params override anything. A CI stage that parses it acts on values the run will not use, with no failure signal. `ci.json` is the resolved, validated projection, and it is the only thing CI reads.

- [ ] **Step 1: Write the schema**

`schemas/ci.schema.json`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://tevv.local/schemas/ci.schema.json",
  "title": "TEVV resolved CI contract (tevv-compile --emit-ci)",
  "$comment": "Emitted by tevv-compile; consumed by CI stages. CI never parses omega.yaml or tevv.yaml directly - see Jenkins CI spec 5.",
  "type": "object",
  "additionalProperties": false,
  "required": ["schema_version", "compiler_version", "trigger", "integration_ref", "build", "unit", "suites", "submit"],
  "properties": {
    "schema_version": { "type": "string", "pattern": "^[0-9]+$" },
    "compiler_version": { "type": "string", "pattern": "^[a-z0-9./-]+@sha256:[a-f0-9]{64}$" },
    "integration_ref": { "type": "string", "minLength": 7 },
    "trigger": {
      "type": "object",
      "additionalProperties": false,
      "required": ["repo", "sha", "tier", "reason"],
      "properties": {
        "repo": { "type": "string" },
        "sha": { "type": "string", "pattern": "^[a-f0-9]{40}$" },
        "pr": { "type": ["integer", "null"], "minimum": 1 },
        "tier": { "enum": ["L0", "L1", "L2", "L3"] },
        "reason": { "enum": ["commit", "pr_open", "pr_update", "merge", "nightly", "manual"] }
      }
    },
    "build": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["component", "context", "dockerfile", "image_repo", "tag", "cache_repo"],
        "properties": {
          "component": { "type": "string" },
          "context": { "type": "string" },
          "dockerfile": { "type": "string" },
          "image_repo": { "type": "string" },
          "tag": { "type": "string" },
          "cache_repo": { "type": "string" },
          "resources": { "$ref": "#/$defs/podResources" }
        }
      }
    },
    "unit": {
      "$comment": "Resolved from tevv.yaml unit_tests. CI-only: never produces a registry verdict, carries no requirement ids.",
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["component", "name", "image", "command"],
        "properties": {
          "component": { "type": "string" },
          "name": { "type": "string" },
          "image": { "type": "string" },
          "command": { "type": "array", "items": { "type": "string" }, "minItems": 1 },
          "timeout_sec": { "type": "number", "exclusiveMinimum": 0 },
          "resources": { "$ref": "#/$defs/podResources" },
          "artifacts": { "type": "array", "items": { "type": "string" } },
          "required": { "type": "boolean" }
        }
      }
    },
    "suites": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["suite", "omega_digest", "estimated_runs"],
        "properties": {
          "suite": { "type": "string" },
          "omega_digest": { "type": "string", "pattern": "^sha256:[a-f0-9]{64}$" },
          "estimated_runs": { "type": "integer", "minimum": 1 }
        }
      }
    },
    "submit": {
      "type": "object",
      "additionalProperties": false,
      "required": ["osmo_endpoint", "secret_refs", "supersede_key"],
      "properties": {
        "osmo_endpoint": { "type": "string", "format": "uri" },
        "secret_refs": {
          "type": "object",
          "additionalProperties": false,
          "required": ["osmo_token", "registry_dsn"],
          "properties": {
            "osmo_token": { "type": "string" },
            "registry_dsn": { "type": "string" }
          }
        },
        "supersede_key": {
          "type": "object",
          "additionalProperties": false,
          "required": ["trigger_ref", "suites"],
          "properties": {
            "trigger_ref": { "type": "string" },
            "suites": { "type": "array", "items": { "type": "string" }, "minItems": 1 }
          }
        }
      }
    }
  },
  "$defs": {
    "podResources": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "cpu": { "type": "string" },
        "memory": { "type": "string" },
        "ephemeral-storage": { "type": "string" }
      }
    }
  }
}
```

- [ ] **Step 2: Write the positive example**

`schemas/examples/local-planner-pr.ci.json`:

```json
{
  "schema_version": "1",
  "compiler_version": "harbor.local/tevv/tevv-compile@sha256:3f786850e387550fdab836ed7e6dc881de23001b3f8bbf3d2b5f1b4d8e9c0a71",
  "integration_ref": "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678",
  "trigger": {
    "repo": "autonomy/local-planner",
    "sha": "9f8e7d6c5b4a39281706f5e4d3c2b1a098765432",
    "pr": 123,
    "tier": "L1",
    "reason": "pr_update"
  },
  "build": [
    {
      "component": "local-planner",
      "context": ".",
      "dockerfile": "docker/Dockerfile",
      "image_repo": "harbor.local/autonomy/local-planner",
      "tag": "sha-9f8e7d6",
      "cache_repo": "harbor.local/tevv/cache",
      "resources": { "cpu": "4", "memory": "8Gi", "ephemeral-storage": "20Gi" }
    }
  ],
  "unit": [
    {
      "component": "local-planner",
      "name": "gtest-core",
      "image": "harbor.local/autonomy/local-planner:sha-9f8e7d6",
      "command": ["colcon", "test", "--packages-select", "local_planner"],
      "timeout_sec": 600,
      "resources": { "cpu": "4", "memory": "8Gi", "ephemeral-storage": "20Gi" },
      "artifacts": ["build/**/test_results/**/*.xml"],
      "required": true
    }
  ],
  "suites": [
    {
      "suite": "l1-planner-substack-smoke",
      "omega_digest": "sha256:c7be1ed902fb8dd4d48997c6452f5d7e509fbcdbe2808b16bcf4edce4c07d14e",
      "estimated_runs": 9
    }
  ],
  "submit": {
    "osmo_endpoint": "https://osmo.tevv.local/api/v1",
    "secret_refs": { "osmo_token": "eso:tevv/osmo-api-token", "registry_dsn": "eso:tevv/registry-dsn" },
    "supersede_key": { "trigger_ref": "PR-123", "suites": ["l1-planner-substack-smoke"] }
  }
}
```

- [ ] **Step 3: Write the negative fixture**

`schemas/examples/invalid-ci-unit-verifies.ci.json.rejected` — copy the positive example and add `"verifies": ["REQ-NAV-012"]` inside the single `unit[]` entry. This is the same boundary Task 3 guards on the input side; guarding it on the resolved side too means a compiler bug cannot smuggle requirement ids into a CI-only result.

- [ ] **Step 4: Run positive test**

Run: `python3 tools/validate_yaml.py schemas/ci.schema.json schemas/examples/local-planner-pr.ci.json`
Expected: `OK: schemas/examples/local-planner-pr.ci.json`, exit 0.

- [ ] **Step 5: Run negative test**

Run: `python3 tools/validate_yaml.py schemas/ci.schema.json schemas/examples/invalid-ci-unit-verifies.ci.json.rejected; echo "exit=$?"`
Expected: `FAIL: … Additional properties are not allowed ('verifies' was unexpected)`, `exit=1`.

Both, plus five further negative cases (missing `integration_ref`, short `sha`, bad `tier`, unpinned `compiler_version`, stray top-level key), were verified against `jsonschema` 4.25.1 at plan-amendment time.

- [ ] **Step 6: Full validator + commit**

Run: `bash tools/validate_docs.sh` → `OK: all checks passed`

```bash
git add schemas/
git commit -m "feat(schemas): add ci.json resolve contract schema with worked and negative examples

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 15: Architecture doc 10 — CI plane

**Files:**
- Create: `architecture/10-ci-plane.md`
- Test: validator (header sequence + mermaid block)

**Interfaces:**
- Consumes: Jenkins CI spec (all sections); `diagrams/jenkins-ci-flow.mermaid` (already committed); `schemas/ci.schema.json` (Task 14); ADR-009 (Task 5).
- Produces: the CI-plane reference that Task 16's handoff quotes and Task 13's README lists in the reading order.

- [ ] **Step 1: Write `architecture/10-ci-plane.md`**

Required structure: `## Role and boundary`, `## Jenkins topology`, `## The ci.json resolve contract`, `## Pipeline stages`, `## Coupling and PR feedback`, `## Supersession`, `## Suite resolution`, `## Node placement and scheduling`, `## Image and disk lifecycle`, `## Alerting`, `## Limiting factors`.

Must cover:

- **Role and boundary** — embed `diagrams/jenkins-ci-flow.mermaid` inline (plain copy, no sync marker; the sync check covers only 00-overview). State the framing from Jenkins CI spec §1: the vendor swap is twelve lines, but **CI relocating from outside the cluster to inside it** is the architectural change, and every risk below follows from that relocation. State baseline §4's boundary verbatim and note it is now schema-enforced (Task 3, Task 14).
- **Jenkins topology** — controller on the control-plane node with `JENKINS_HOME` on a device **separate from etcd** (§4.1), and why: etcd is fsync-latency-bound, build-log and artifact writes drive fsync p99 into leader-election flapping, which surfaces as OSMO backend-agent WebSocket drops and `INFRA_FAILED` runs — a platform whose own CI manufactures INFRA_FAILED violates the <5% SLO it exists to defend. JCasC + Job DSL so the controller is rebuildable from git (it is a SPOF co-located with kube-master and the OSMO control plane). Dynamic pod agents building with **BuildKit via an ephemeral `buildkitd` sidecar** — no Docker socket, no privileged container, and no build state surviving the pod. State why the sidecar was chosen over a shared cluster builder (a long-lived `buildkitd` keeps a local cache under BuildKit's own GC, adding a fifth accumulation store) and why Kaniko was abandoned (archived upstream 2025-06-03). State the security cost honestly: the sidecar needs a namespaced PSA/Kyverno exception for Unconfined seccomp/AppArmor, scoped to the CI namespace only. Shared library so a component `Jenkinsfile` is ~5 lines across N repos.
- **The ci.json resolve contract** — the field table from Jenkins CI spec §5, referencing `schemas/ci.schema.json` by name; the rule that `tevv-compile` is the sole reader of contract YAML.
- **Pipeline stages** — `resolve | build | unit | submit`, one table row each (agent, action), per §9.1.
- **Coupling and PR feedback** — fire-and-forget; a green build means *submitted*, not *passed*; the conductor posts commit status and the verdict comment with the janitor backstop. Record **why blocking was rejected**: it holds an executor for the whole run, and variant §8 makes LOW-priority sweeps preemptible and fully reschedulable underneath a poller — an unbounded wait; the webhook-callback remedy is unavailable because OSMO has no webhooks, so the conductor would need a Jenkins-specific dependency, destroying the vendor-agnosticism variant §7 protects. Note the mitigation: the submit stage writes OSMO/Grafana/registry deep links into the Jenkins build description.
- **Supersession** — the two-layer table from §9.3 (`milestone()` for builds, registry query for runs) and why neither covers the other's window.
- **Suite resolution** — the five-step rule from §9.4, including the **pinned `integration_ref`** recorded in `ci.json` and `manifest.json`; state that without it a moving integration repo makes component-repo PR results non-reproducible.
- **Node placement and scheduling** — the KAI blind spot (§7.1): Jenkins pods are scheduled by the default kube-scheduler unless `schedulerName: kai-scheduler` is set, so KAI's accounting believes capacity exists that builds already consumed, producing barrier timeouts recorded as INFRA_FAILED. The placement table from §7.2 (controller, agents, run pods, `gpu-sim` taint) and the three jobs the taint does at once. The RTX 2080 Ti recommendation (§7.3): `gpu-inference` at most, nothing scheduled pending measurement.
- **Image and disk lifecycle** — what pod agents fix and what merely relocates (§8.1); why default kubelet image GC is backwards here (LRU evicts the tens-of-GB sim image to make room for build layers, and the next run pays a cold pull inside the OSMO startup barrier with the GPU already reserved); the §8.3 feedback loop as a fenced `text` block; the four-store table (§8.4); the seven controls in dependency order (§8.5) with the pull-through cache first and the reason it comes first — a cache miss becomes a LAN pull, which is what makes aggressive GC safe. Document control 6 in full: age-based registry policy **vetoed** by a query over `tevv.components` for digests still referenced by non-expired runs, with the invariant *image retention ≥ artifact retention for the same run*.
- **Alerting** — the four-signal table from §8.6, and the standing obligation it discharges: baseline §2.1 records that Elasticsearch disk-watermark incidents already dropped run results silently.
- **Limiting factors** — the ranked table from §11, stating plainly that items 1 and 4 did not exist under GitHub Actions and are created by the relocation, and that migrating CI buys **zero** additional run throughput.

- [ ] **Step 2: Validate + commit**

Run: `bash tools/validate_docs.sh` → `OK: all checks passed`

```bash
git add architecture/10-ci-plane.md
git commit -m "docs(arch): add 10-ci-plane (Jenkins topology, ci.json, image lifecycle)

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 16: Jenkins CI plane handoff package

**Files:**
- Create: `tasks/20260818_opus_p0_jenkins_ci_plane.md`
- Modify: `tasks/README.md` (add the row to the `ready` table)
- Test: validator handoff-template check

**Interfaces:**
- Consumes: Jenkins CI spec §4–§10; `architecture/10-ci-plane.md` (Task 15); `schemas/ci.schema.json` (Task 14); ADR-009 (Task 5).
- Produces: an executable P0 work package. Per CLAUDE.md §3 it must be **self-contained** — copy normative blocks in, never "see other file".

- [ ] **Step 1: Write the handoff**

`tasks/20260818_opus_p0_jenkins_ci_plane.md`, with exactly the five H1 headers from CLAUDE.md §3.

`# Task Context & System Summary` — the TEVV platform in one paragraph; CI's boundary (build + unit only, every verdict through the orchestrator); that this package delivers **P0 only**: pull-through cache, Jenkins controller, shared library, `resolve` and `build` stages. The `unit` and `submit` stages are P1 and are explicitly out of scope here.

`# Upstream Specifications` — copy in verbatim: the `ci.json` field table (§5); `schemas/ci.schema.json` in full; the placement table (§7.2); the seven disk controls (§8.5); the four alert signals (§8.6).

`# Explicit Instruction Prompt` — declarative commands.

**First, pin the builder.** BuildKit is a moving target and the platform pulls only digest-pinned images, so resolve the current released rootless tag and its digest through the pull-through cache, and record both in the manifest as a comment. Do not use `latest` or `master-rootless`. Substitute the resolved values for `BUILDKIT_REF` below. This is a pin-at-implementation-time instruction, not a placeholder: hard-coding a version chosen at plan-writing time would ship a stale builder.

Then write this pod template. Its load-bearing lines are `schedulerName`, `nodeSelector`, the two `emptyDir.sizeLimit` values, and the sidecar's `securityContext`:

```groovy
// vars/tevvComponentPipeline.groovy — shared library
def podYaml() { return '''
apiVersion: v1
kind: Pod
spec:
  schedulerName: kai-scheduler            # closes the KAI accounting hole (spec 7.1)
  nodeSelector:
    tevv.io/node-class: cpu               # never gpu-sim (spec 7.2)
  containers:
  - name: buildkitd
    image: BUILDKIT_REF                   # pinned rootless release @sha256:... via pull-through cache
    args:
      - --addr
      - unix:///run/user/1000/buildkit/buildkitd.sock
      - --oci-worker-no-process-sandbox    # required for rootless on k8s; see security note
    securityContext:                       # namespaced PSA/Kyverno exception, CI namespace only
      seccompProfile:
        type: Unconfined
      appArmorProfile:
        type: Unconfined
      runAsUser: 1000
      runAsGroup: 1000
    readinessProbe:
      exec:
        command: ["buildctl", "debug", "workers"]
      initialDelaySeconds: 5
      periodSeconds: 30
    resources:
      requests: { cpu: "2", memory: "4Gi", ephemeral-storage: "20Gi" }
      limits:   { cpu: "4", memory: "8Gi", ephemeral-storage: "20Gi" }
    volumeMounts:
    - name: buildkitd-sock
      mountPath: /run/user/1000/buildkit
    - name: buildkitd-state
      mountPath: /home/user/.local/share/buildkit
    - name: registry-auth                  # buildkitd performs the push, so creds live here
      mountPath: /home/user/.docker
      readOnly: true
  - name: build
    image: BUILDCTL_REF                    # pinned image carrying buildctl + jq
    command: ["sh", "-c", "sleep 99d"]
    securityContext:
      runAsUser: 1000
      allowPrivilegeEscalation: false
    env:
    - name: BUILDKIT_HOST
      value: unix:///run/user/1000/buildkit/buildkitd.sock
    resources:
      requests: { cpu: "1", memory: "2Gi", ephemeral-storage: "20Gi" }
      limits:   { cpu: "2", memory: "4Gi", ephemeral-storage: "20Gi" }
    volumeMounts:
    - name: buildkitd-sock
      mountPath: /run/user/1000/buildkit
    - name: workspace
      mountPath: /workspace
  volumes:
  - name: buildkitd-sock
    emptyDir: {}
  - name: buildkitd-state                  # dies with the pod - this is the point (spec 8.1)
    emptyDir:
      sizeLimit: 20Gi                      # spec 8.5 control 4
  - name: workspace
    emptyDir:
      sizeLimit: 10Gi
  - name: registry-auth
    secret:
      secretName: tevv-registry-push       # delivered by External Secrets Operator
'''
}
```

The `buildkitd-state` volume is an `emptyDir`, never a PVC. A PVC here would silently convert the sidecar into a persistent builder and reintroduce the fifth accumulation store the sidecar exists to avoid (spec §8.4).

And this build invocation. Every flag was verified against BuildKit's README at plan-amendment time:

```bash
buildctl build \
  --frontend=dockerfile.v0 \
  --local context="${CI_CONTEXT}" \
  --local dockerfile="$(dirname "${CI_DOCKERFILE}")" \
  --opt filename="$(basename "${CI_DOCKERFILE}")" \
  --output type=image,name="${CI_IMAGE_REPO}:${CI_TAG}",push=true \
  --export-cache type=registry,ref="${CI_CACHE_REPO}",mode=max \
  --import-cache type=registry,ref="${CI_CACHE_REPO}" \
  --metadata-file /workspace/metadata.json

# the digest the registry row and manifest.json must record
jq -r '."containerimage.digest"' /workspace/metadata.json
```

`--export-cache type=registry` is the control that keeps per-build layers off node disk by pushing them to the registry instead. Two notes for the implementer:

- `mode=max` exports intermediate layers, so the cache repo grows considerably faster than the image repo. That is the intended trade — node disk is the scarce, run-affecting resource; registry storage on the NAS is governed by policy.
- **BuildKit's registry cache has no TTL.** Kaniko's `--cache-ttl` defaulted to 336h, which happened to match the PR-smoke retention class in baseline §9.2, so its cache self-expired for free. That is gone. The cache repository must carry its own plain 14-day age policy, and the `tevv.components` veto must **not** be applied to it — the cache holds no provenance and nothing references it.

`# Acceptance Criteria` — as a bulleted list:
- `tools/validate_yaml.py schemas/ci.schema.json <emitted ci.json>` exits 0 for a real PR build.
- No pipeline stage reads `omega.yaml` or `tevv.yaml`; `grep -rn "omega.yaml\|tevv.yaml" <shared-library>` returns no parsing code.
- Every agent pod manifest carries `schedulerName: kai-scheduler`, a cpu `nodeSelector`, and explicit `ephemeral-storage` requests **and** limits. A pod without all three is a review rejection.
- The `buildkitd-state` volume is an `emptyDir` with a `sizeLimit`. **A PVC there is a review rejection** — it silently converts the sidecar into a persistent builder and reintroduces the accumulation store the design removes.
- The Unconfined seccomp/AppArmor grant is namespaced: `kubectl get ns -o yaml` shows the exception label on the CI namespace only, and applying the same pod spec to a run-plane namespace is **rejected** by admission. Demonstrate both.
- The builder image is digest-pinned; `grep -rn "buildkit.*:latest\|master-rootless"` returns nothing.
- The registry cache repo has its own 14-day age policy and is **excluded** from the `tevv.components` veto — show the policy config for both repos.
- Cluster and Jenkins pull **only** through the pull-through cache: `docker.io` appears in no image reference in any manifest or Jenkinsfile.
- `JENKINS_HOME` resolves to a device that is not etcd's; show `df` output for both.
- `buildDiscarder` is configured in JCasC; a controller with unbounded build retention is a review rejection.
- Node-filesystem alerting fires at 70%, verified by a synthetic fill test on a non-production node.
- The controller is rebuildable: destroy it, redeploy from JCasC, and the same jobs appear with no manual UI step.

`# Expected Output Schema/Format` — a directory of Kubernetes manifests, JCasC YAML, and a Groovy shared library, plus a `README.md` documenting apply order. No prose deliverable.

- [ ] **Step 2: Add the row to `tasks/README.md`**

Add to the `ready` table: file `20260818_opus_p0_jenkins_ci_plane.md`, phase P0, target agent opus, deliverable "Jenkins controller + shared library + pull-through cache + build stage", upstream "Jenkins CI spec §4–§10, `architecture/10-ci-plane.md`, `schemas/ci.schema.json`", status `ready`.

- [ ] **Step 3: Validate + commit**

Run: `bash tools/validate_docs.sh` → `OK: all checks passed` (handoff-template check passes on the five H1 headers).

```bash
git add tasks/
git commit -m "docs(tasks): add P0 Jenkins CI plane handoff package

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Self-review record

- **Spec coverage:** §1–§4 → Task 7; §5 → Tasks 2/3/8; §6 → Tasks 2/8; §7–§8 → Tasks 4(identity)/6/9; §9 → Tasks 4/10; §10 → Task 10; §11 → Task 11; §12 → Task 11; §13 → Tasks 7/8 (phasing appears in 00-overview and compiler-validation phasing); §14 → the plan itself (all files); §15 → Task 13 coverage table; §16 → cited from Tasks 8/10/11 where deferred decisions are referenced. No uncovered sections.
- **Placeholder scan:** no TBD/TODO/"similar to Task N"; every code/schema/DDL step carries full content; doc tasks carry complete required-section lists and content obligations rather than prose (the prose is the deliverable).
- **Type consistency:** `trigger_kind` used in DDL, Task 10 doc obligations, and handoffs; `(run_id, attempt)` everywhere; retention classes `pr-smoke|standard|release` consistent between DDL and Task 10/12; schema field names in Task 8 doc obligations reference the Task 2/3 files as the source rather than restating divergent copies; validator function names (`check_headers`, `check_mermaid`) internal to Task 1 only.

### Cross-check addendum (post-commit spec verification)

A second pass against the spec found and fixed five defects before execution:

1. Task 2 `ref` pattern required ≥6 hex chars after `pinned@sha256:` while the spec §5.3 example (copied verbatim in Step 2) uses `ab12…` (4) — the positive test would have failed. Relaxed to `{4,64}`.
2. Task 1 discover mode scanned `docs/superpowers/**`; the plan's illustrative nested fence would have been extracted as a pseudo-mermaid block and failed mmdc. Discovery now excludes `docs/superpowers/` and `.claude/` — the validator guards the deliverable doc set only.
3. `trigger_kind` enum lacked `commit`, leaving spec §4's every-commit L0 runs with no legal value. Added.
4. `manifest_uri` was nullable, contradicting spec §9.1 (provenance anchor, Nullable = no). Now NOT NULL; Task 4 test inserts and the Task 12 `tevv upload` handoff (synthesize minimal manifest) updated to match; added plain `started_at` btree per §9.1.
5. Must-cover omissions: spec §9.4 pluggable evaluator containers added to Task 10; spec §8 bridge-no-reconnect absorption added to Task 9.

Deliberate deviation kept: `trigger_ref` stays nullable (spec §9.1 groups it with `trigger_kind` as not-null, but cron/manual runs have no PR#/SHA to reference).

### OSMO amendment record (2026-07-13)

The plan was amended after the OSMO orchestration variant spec (`2026-07-13-autonomy-tevv-osmo-orchestration-design.md`) was written, externally reviewed (5 findings, all verified against the OSMO repo — see `reviews/2026-07-13-gemini-3.1-pro-osmo-review-disposition.md`), and adopted as the primary orchestration design per its §14:

1. Sources of truth: both specs; variant wins on orchestration. Terminology gains the OSMO vocabulary block (group/lead/exit codes/pools/attempt-scoped prefix).
2. Task 1: 00-overview sync-check targets `system-architecture-osmo` + `system-data-flow-osmo`.
3. Task 4: `workflow_ref` replaces `argo_workflow_id` (orchestrator-neutral).
4. Task 5: eight ADRs — ADR-008-osmo-orchestrator added; ADR-001/004/007 carry supersession/mechanism notes.
5. Task 6: `system-data-flow-osmo.mermaid` added (content given, mmdc-verified at plan-amendment time); compile-flow diagram shows all three targets with osmo primary.
6. Tasks 7–11: doc obligations rewritten/extended per variant §3–§13 (submission model, KAI scheduling, slimmed conductor, exit-code contract, registry-derived attempts, shared-namespace isolation, OSMO backing services, attempt-scoped layout, OSMO UI, Kyverno/air-gap gates, scheduler tests).
7. Task 12: `tevv upload` keys gain the mandatory attempt segment (`…/<run_id>/1/…`); planned-P1 handoff list re-scoped to OSMO deliverables.
8. Task 13: coverage pass and README provenance span both specs and both dispositions.

P0 execution order and deliverables are otherwise unchanged — OSMO enters at P1, gated on the variant §13 spike, with `--target k8s` as the documented fallback.

### Jenkins CI plane amendment record (2026-08-18)

The plan was amended after the Jenkins CI plane spec (`2026-08-18-autonomy-tevv-jenkins-ci-design.md`, commit `d19892a`) replaced GitHub Actions self-hosted runners with Jenkins. Amending rather than writing a second plan follows the OSMO precedent (commit `8d734a5`) and avoids file collisions — both plans would otherwise create `schemas/omega.schema.json`, `schemas/tevv.schema.json`, the ADR set and the diagrams. No task had been executed at amendment time.

1. Global constraints: three specs with **layered, narrow** supersession (variant owns orchestration; Jenkins spec owns the trigger/CI plane; baseline owns the rest and stays the rationale record). New CI-plane terminology block. Executor attribution corrected from Fable 5 to **Opus 5** in all 14 commit templates, per repo CLAUDE.md §6, which requires Opus workers.
2. Task 2: `matrix` gains `mode: product | sample`. The sampled form requires `seed` and supports `normal|uniform|loguniform|choice`. Two new fixtures and two new steps. The exact JSON fragment was verified against `jsonschema` 4.25.1 across seven cases — including that today's spec §5.3 example, which has no `mode` key, still validates.
3. Task 3: `tevv.yaml` gains `unit_tests`. The `verifies:` prohibition is enforced by `additionalProperties: false` — tested, and the two "stronger" alternatives (`not`/`required`, `"verifies": false`) were also tested and rejected: neither improves the error, and the boolean form degrades it.
4. Task 5: nine ADRs — ADR-009-jenkins-ci added.
5. Task 6: both architecture diagrams swap GHA for a Jenkins controller **plus a separate agent-pods node inside the compute cluster** — the relocation is the architectural change, so the diagram must show it. Both amended diagrams were rendered under mmdc at amendment time. The two `Modify:` steps must precede Task 7, which embeds `system-architecture-osmo.mermaid` under a sync check.
6. Tasks 7, 8, 11, 12, 13: doc obligations extended — the CI boundary and fire-and-forget coupling (01), both matrix modes with seed provenance and the `unit_tests`/`l0_tests` split (02), `--emit-ci` and sole-reader rule (03), a new `## CI plane surface` section including the Kyverno-unenforceable risk (08), the Jenkins handoff in the ready table (12), and the three-spec reading order (13).
7. New Tasks 14–16: `schemas/ci.schema.json` (verified across seven cases; **execute immediately after Task 3**), `architecture/10-ci-plane.md`, and the P0 Jenkins handoff package.

8. **Builder correction (same session):** Kaniko was replaced by **BuildKit** throughout after the archival was flagged — upstream `GoogleContainerTools/kaniko` was archived 2025-06-03 and is read-only. Placement is an **ephemeral `buildkitd` sidecar per agent pod**, chosen over a shared cluster builder so ephemerality survives; a long-lived builder would add a fifth accumulation store under BuildKit's own GC. Two honest costs are recorded rather than glossed: (a) the rootless sidecar needs a **namespaced** PSA/Kyverno exception for `seccompProfile: Unconfined` + `appArmorProfile: Unconfined` and `--oci-worker-no-process-sandbox`, which makes the spec's earlier "unprivileged by construction" claim false and required rewriting §10; (b) Kaniko's `--cache-ttl` default of 336h coincided with the PR-smoke retention class, and BuildKit's registry cache has **no TTL**, so control 6 must now supply a 14-day age policy on the cache repo — with the `components` veto explicitly *not* applied to it. All `buildctl` flags were verified against BuildKit's README, and the rootless securityContext against upstream's `examples/kubernetes/deployment+service.rootless.yaml` and `cmd/buildkitd/main_oci_worker.go`.

**Not designed here, by explicit decision.** Jenkins CI spec §12 records four infrastructure deltas that conflict with baseline §11 and §9.1 — container images from Docker Hub rather than an internal registry, Postgres and MinIO on a NAS, an air-gapped Artifactory later, and GitHub-as-SCM colliding with that air-gap end state. They were deferred to a separate design pass rather than accepted. Two consequences for executors: registry hostnames stay `harbor.local` throughout the deliverable (a placeholder for whichever registry wins, not an endorsement of Harbor), and Task 11's security doc must state the Kyverno gap as an open risk rather than describing signed-image admission as working.
