# Design: campaigns in the dashboard — a read-only page, a CampaignSpec editor with the validator, and an OSMO launch

Status: **proposal** for the TEVV-Web-Dashboard, MnS-Integration-Platform and
M-S-Simulation-Runtime-Stack owners. Date: 25 Sept 2026.

## 1. Why

A TEVV campaign on OSMO works end to end today, but only from a terminal:

1. Write the CampaignSpec YAML by hand, next to the ScenarioSpec that
   ScenarioLab exported.
2. Run `osmo/campaign.py run <campaign>`.
3. Watch progress in the run registry or in Grafana, and read results in
   `runs/<key>/`, `campaign status` and the "TEVV campaign progress" and "TEVV
   run logs" dashboards.

The dashboard stops one step earlier, at the authored ScenarioSpec. It cannot
see an OSMO campaign at all: its VIO Stress page reads manifest files under
`SIM2REAL_RUNS_DIR/_campaigns`, which OSMO runs never write.

**Goal.** Give the dashboard one path from an authored ScenarioSpec to a result:
1. author a CampaignSpec over it;
2. validate it as you type;
3. launch it on OSMO;
4. watch it fly, and read the verdict.

**Non-goals.**
- Changing the compose-target launch: the existing `/api/campaign/run` stays.
- Multi-user authentication: the dashboard has none (see §10).
- Prometheus.
- Editing ScenarioSpecs: the Generate phase already does that.

## 2. What exists

### Dashboard (TEVV-Web-Dashboard `main`, ee99b9d)

| Piece | What it does | Reuse |
| --- | --- | --- |
| `/api/campaign/*` (#110): `backend/app/routers/campaign.py`, `services/campaign_jobs.py`, `models/campaign.py` | Wraps the `tevv-campaign` CLI with `--json`. Endpoints: `templates`, `init`, `validate`, `preflight`, `plan`, `run`, `jobs`, `progress`, `status`. One job slot, a command allowlist, and a 2000-line log tail. Findings come back as `{id, status, detail}`. | Validate, templates and init as they are. The job model is the pattern for the OSMO launch. |
| VIO Stress page `frontend/src/app/vio-stress/page.tsx` | Read-only scores from ClickHouse `metric_results`, joined with manifest files by `run_id`. No polling. | Stays. Campaigns link to it for segment heat maps. |
| Generate phase `interactive-mode/phases/GeneratePhase.tsx` | ScenarioSpec YAML in a `<textarea>`, parsed with `js-yaml`, with a line diff against a baseline. | The same editing approach for the CampaignSpec YAML pane. |
| `components/dashboard/Sidebar.tsx` | A hard-coded nav array. | One entry: "Campaigns". |
| Conventions | shadcn/ui, Tailwind (dark), lucide icons, Recharts; `lib/api/client.ts` fetch objects; `setInterval` polling; FastAPI with asyncpg (no ORM); no auth. API docs generated from the OpenAPI schema (#113, open). | Every new route follows #113's summary, `response_model` and model convention. |

**Gaps found:**
- **No CLI under `make dashboard`.** `docker-compose-dashboard.yml` sets no
  `TEVV_CAMPAIGN_BIN`, so under `make dashboard` the `/api/campaign/*`
  endpoints have no CLI to call.
- **A dead flag.** The unmerged branch `feat/campaign-osmo-target` calls
  `tevv-campaign run --target osmo --priority LOW`. That runner-side OSMO
  target was never merged and has been dropped. The OSMO executor is this
  repository's `osmo/campaign.py`.
- **`plan` has no `--json`, and it writes files.** It materialises
  `<root>/<run_key>/ScenarioSpec.yaml` for every run. The dashboard's
  `/api/campaign/plan` passes `--json`, which argparse rejects today.
- **`validate` takes a path, not a document.** The scenario, route and
  calibration resolve relative to the file.
- **The backend container can't reach the cluster.** It can reach neither the
  run registry (on the kind network, NodePort 30432) nor the `osmo` CLI (which
  needs `~/.osmo` and a kubeconfig).

### Platform (MnS-Integration-Platform)

- `tevv-campaign validate --json` returns
  `{checks: [{id, status: ok|fail|warn|skip, detail}], ok, campaign_id, runs_planned}`.
  - Check ids: `schema`, `scenario`, `matrix`, `route`, `calibration.*`, and
    `conditions.*`.
  - The `conditions` checks catch weather authored where the generator drops
    it, and report physics wind (`conditions.physics_wind`).
  - Its docstring says it is safe to run from a form as the user types.
- **The run matrix** is `contract.iter_campaign_runs`:
  - order: variants × seeds × repeats;
  - run key: `<variant>[-s<seed>]-r<repeat>`.
- **The schema** is `contracts/mns-scenariospec/schema/CampaignSpecSchema.yaml`.
  It is a custom meta-format, not JSON Schema, and its semantic rules live in
  code. A browser form therefore validates on the server.

### Runtime stack (this repository)

- **`osmo/campaign.py`** is the OSMO executor.
  - Subcommands: `run`, `watch`, `images`, `reindex`, `registry sync`,
    `evaluate`, `status`.
  - Exit codes: 0 ok, 1 a run or gate failed, 2 usage, 42 the platform is not
    usable.
  - It needs, on the host: a logged-in `osmo` CLI, `kubectl` for the kind
    cluster, docker on the `kind` network, and `product.sh` in the checkout
    the cluster mounts at `/workspace`.
  - It has **no cancel and no signal handling**: killing it leaves submitted
    workflows running.
- **The run registry** (`osmo/registry.py`, `osmo/observability/registry/schema.sql`):
  - tables `campaigns`, `runs` (PK campaign, run key, attempt; unique
    `workflow_ref`), `gate_results`, `metrics_summary`, `artifacts`;
  - views `v_latest_runs` and `v_campaign_progress`;
  - Grafana reads it with the `grafana_ro` role.
  - [osmo-run-flow.md](../osmo-run-flow.md) lists what each run writes where.
    [osmo-logs.md](../osmo-logs.md) covers the logs.
- **Fields OSMO ignores:** `recording.*`, `mission.source` and `mission.done`.
  See the runbook's "CampaignSpec, field by field" in
  [osmo-runbook.md](../osmo-runbook.md#campaignspec-field-by-field).

## 3. Architecture

```
browser ──> dashboard backend (container, :8001)
              ├─ /api/campaign/validate · templates · init  ──> tevv-campaign ──> product shell (docker, same mounts)
              ├─ /api/campaigns/*            (read-only)    ──> run registry (Postgres, role dashboard_ro)
              └─ /api/campaign/osmo/*        (launch)       ──> campaign executor (host service, 127.0.0.1:8770)
                                                                  └─> osmo/campaign.py ──> OSMO, kubectl, docker
```

Three decisions:

1. **Validation goes through the product shell.** A new wrapper,
   `tools/tevv-campaign` in this repository, runs
   `product.sh cli campaign "$@"`.
   - It works from inside the backend container because the docker socket and
     `MSRS_ROOT` are already mounted there at identical paths.
   - `docker-compose-dashboard.yml` sets `TEVV_CAMPAIGN_BIN` to it.
   - #110's endpoints then work under `make dashboard` unchanged, running the
     same validator the terminal runs.
2. **The dashboard reads the registry directly, read-only.**
   - Schema v2 adds a `dashboard_ro` role with SELECT, as `grafana_ro` has.
   - The backend gets `MNS_REGISTRY_URL_RO`.
   - It joins the external `kind` docker network, which is how Grafana
     reaches the registry today, under an opt-in compose profile `osmo`. A
     dashboard without a cluster still starts, and says "registry not
     reachable" the way it handles the telemetry DB being absent.
3. **Launching goes through an executor on the host, not the container.**
   - `osmo/campaign.py` needs a logged-in `osmo` session, a kubeconfig, docker
     on the kind network, and the mounted checkout. Mounting all of that into
     the dashboard would tie the dashboard to one workstation's cluster.
   - Instead, a small service, `osmo/executor.py serve`, runs on the host,
     bound to 127.0.0.1:8770. It is started by `make osmo-executor` or as a
     user systemd unit, and runs `campaign.py` as a subprocess job.
   - The backend proxies to it at `http://host.docker.internal:8770`, the same
     pattern it uses for the product shell (`PRODUCT_SHELL_URL`, :8760).

## 4. Feature A — read-only Campaigns page

**Nav.** "Campaigns" in the Sidebar, route `/campaigns`.

**List `/campaigns`.** One row per campaign from `v_campaign_progress`:
- name;
- runs, and the status mix as a bar: pending, submitted, running, evaluating,
  passed, failed, infra;
- pass %, last update;
- target and tier, from `campaigns`.

**Detail `/campaigns/[id]`:**

| Block | Source | Shows |
| --- | --- | --- |
| Runs | `v_latest_runs` | run key, attempt, status and `failure_reason`, queued s (`started_at − submitted_at`), ran s, ATE rmse, recording valid and failed checks, workflow |
| Gates | `gate_results` | a variant × gate matrix, value against bound, pass or fail |
| ATE per run | `metrics_summary` | bar chart, with the gate bound as a line |
| Attempts | `runs` | every attempt of a run key, so a re-fly doesn't hide the one before |
| Artifacts | `artifacts` | bag path, eval, reports, logs (paths on this host) |

**Links out:**
- Grafana "TEVV run logs", per workflow and framed to the run's own time
  window. The progress dashboard already builds this link.
- Foxglove at `ws://<GPU node>:30765`, only while a viz run's flight is live.
  The executor exposes it; the recorder no longer holds a run for a viewer.
- The VIO Stress page, for segment heat maps.

**Backend `routers/campaigns.py`:**
- `GET /api/campaigns`
- `GET /api/campaigns/{id}`
- `GET /api/campaigns/{id}/runs`
- `GET /api/campaigns/{id}/gates`
- `GET /api/campaigns/{id}/runs/{run_key}/attempts`

It uses a second asyncpg pool, following `services/database.py`, with models
per #113.

**Polling.** Every 10 s on the detail page while any run is not terminal;
otherwise none.

## 5. Feature B — CampaignSpec editor with the validator

**Entry points:**
- "New campaign from this scenario" on the Generate phase, over the authored
  ScenarioSpec. It calls `POST /api/campaign/init` with a template from
  `GET /api/campaign/templates`, then sets `scenario:` to that spec.
- "New campaign" on `/campaigns`.
- "Edit" on a campaign's detail page.

**Editor `/campaigns/[id]/edit`.** A form and the YAML side by side, bound to
one document with `js-yaml`, as the Generate phase does. Editing either side
updates the other. The form's sections follow the schema:

| Section | Fields |
| --- | --- |
| Scenario | `scenario` (picker over authored ScenarioSpecs) |
| Variants | `id`, `name`, overrides: visual weather (`environment.weather.enabled`, `wind` 0-10), **physics wind** (`wind_mps`, `wind_from_deg`), time of day |
| Matrix | `seeds`, `repeats` |
| Mission | `autopilot` (must match the ScenarioSpec's `runtime.profile`), `trajectory` (picker over `routes/`), `timeout_s` |
| Evaluation | `evaluator`, `inputs.est_topic`, `gates` (rows: metric, `max`/`min`), `thresholds` |
| Omega | `extensions.mns.omega.tier`, `verifies` |

**Target badges.** Each field says whether an OSMO run honours it or ignores
it, from the runbook's field table. For example, `recording.topics` shows
"ignored on OSMO: the recorder's topic list is fixed in the workflow".

**Live validation:**
- The draft is saved, debounced about 800 ms, to
  `scenarios/<name>/.CampaignSpec.draft.yaml`. It must sit in the same
  directory because validate resolves the scenario, route and calibration
  relative to the file. A `.gitignore` pattern keeps drafts out of git.
- Then `POST /api/campaign/validate` runs on the draft.
- The checks show grouped: schema, scenario, matrix, route, calibration,
  conditions. `fail` blocks saving, `warn` and `skip` show but don't block, and
  `conditions.physics_wind` confirms the vehicle will feel the wind.
- **Save** writes `CampaignSpec.yaml`. Committing to git stays the user's job,
  and the page says so.

**Plan preview.** Shows the N runs and each run's merged overrides.
- Platform ask (§8): `tevv-campaign plan --json --no-write`, computed with
  `iter_campaign_runs` without materialising anything.
- Until then, the preview shows `runs_planned` from validate, plus the keys
  computed in the browser by the same rule, `<variant>[-s<seed>]-r<repeat>`.

## 6. Feature C — launch on OSMO

**"Run on OSMO"** sits in the editor. It is enabled only when validate is `ok`
and the spec is saved.

**Preflight.** Executor `GET /preflight`, shown as a checklist. Launch is
blocked on any fail.

| Check | How |
| --- | --- |
| OSMO session | `osmo pool list` answers |
| Cluster | the `kubectl` context reaches both workers |
| Images | `campaign.py images`: every pinned image is on the GPU node, and the evaluation images are on the service node |
| Evaluation platform | pool `default` has platform `cpu` (`osmo/cpu-platform.sh`) |
| Registry | reachable, and at the expected schema version |
| GPU | free, or how many gangs are queued ahead |

**Confirm dialog:**
- number of runs; the `--only` subset;
- Foxglove on or off. A viewer never holds a run open, so a watched run ends
  when its recording does.
- priority: LOW by default, NORMAL for a tier-L1 campaign.
- estimated duration from registry history: the median flight group per level
  and autopilot (XFS PX4 about 128 s, XFS ArduPilot about 180 s), plus about
  45 s of evaluation, plus queue.

**Launch:**
- `POST /api/campaign/osmo/jobs` returns a job id.
- The page moves to `/campaigns/[id]`. Its rows walk pending, submitted,
  running, evaluating, then a terminal status, as the registry updates.
- A drawer shows the executor job's log tail.

**Cancel.** "Stop campaign" calls `DELETE /api/campaign/osmo/jobs/{id}`, which
signals the executor.
- Runtime-stack ask (§8): `campaign.py` handles SIGTERM. It runs
  `osmo workflow cancel` on every workflow it submitted that has not finished,
  and marks them `aborted` in the registry.
- Today a killed executor leaves workflows running.

**What is recorded.** Registry schema v2 adds
`campaign_launches(launch_id, campaign_id, spec_sha256, spec_yaml, target,
options jsonb, executor_job, created_by, created_at)`. The exact spec that
flew is kept even if the YAML is edited afterwards, and each `runs` row's
attempt is tied to the launch that produced it.

**Executor API** (`osmo/executor.py`, host, 127.0.0.1:8770):

| Route | Does |
| --- | --- |
| `GET /health` | alive, version |
| `GET /preflight` | the table above, as `{checks: [{id, status, detail}], ok}` |
| `POST /jobs` | `{campaign: <path under $MSRS_ROOT/scenarios>, only: [...], viz: bool, priority}` → a job. The path is allowlisted, and there is no shell. |
| `GET /jobs`, `GET /jobs/{id}` | state, exit code, log tail, submitted workflow ids |
| `DELETE /jobs/{id}` | SIGTERM, which cancels outstanding workflows |

One job at a time by default, since this is one GPU. A second request gets 409
with the running job.

## 7. Data model changes (registry schema v2)

- Role `dashboard_ro`, with SELECT on all tables and views, like `grafana_ro`.
  Its password lives in secret `tevv-registry-dashboard`.
- Table `campaign_launches` (§6), and `runs.launch_id`, nullable, so terminal
  and backfilled runs have none.
- Applied by `python3 osmo/registry.py migrate`, which is idempotent as in v1.

## 8. Changes by repository

**TEVV-Web-Dashboard**
- Frontend:
  - pages `/campaigns`, `/campaigns/[id]` and `/campaigns/[id]/edit`;
  - the Sidebar entry;
  - "New campaign from this scenario" on the Generate phase;
  - `campaignsAPI` and `campaignOsmoAPI` in `lib/api/client.ts`.
- Backend:
  - `routers/campaigns.py` (registry read) and `routers/campaign_osmo.py`
    (executor proxy);
  - a second asyncpg pool;
  - models per #113.
- Tests:
  - router tests with a fake registry and a fake executor, following
    `test_campaign_router.py`;
  - vitest for the form ↔ YAML binding and the target badges.
- Retire the `--target osmo` call on `feat/campaign-osmo-target`.

**M-S-Simulation-Runtime-Stack**
- `tools/tevv-campaign`, the product-shell wrapper.
- `osmo/executor.py` and `make osmo-executor`.
- `campaign.py` cancel on SIGTERM.
- Registry schema v2.
- `docker-compose-dashboard.yml`:
  - `TEVV_CAMPAIGN_BIN`;
  - a profile `osmo` that joins the external `kind` network;
  - `MNS_REGISTRY_URL_RO`;
  - `CAMPAIGN_EXECUTOR_URL`.
- `.gitignore` for draft specs.
- A runbook section.

**MnS-Integration-Platform**
- `tevv-campaign plan --json --no-write`.
- Optional: `validate` from a document as well as a path. The contract's
  `validate_campaign_path(..., document=)` already accepts one, and this would
  make the draft file unnecessary.

## 9. Delivery and acceptance

| Phase | Delivers | Accepted when |
| --- | --- | --- |
| 1 | Read-only Campaigns page, registry v2 role, compose profile | `vio-osmo-condo`, `vio-osmo-xfs` and `vio-osmo-xfs-ardupilot` list with the same counts and pass rates as Grafana's progress dashboard; each run's gates match its `verdict.json`; with no cluster the page says "registry not reachable" and the rest of the dashboard works |
| 2 | Editor and validator, `tools/tevv-campaign`, draft handling | a physics-wind variant shows `conditions.physics_wind ok`; weather under `environment.conditions` shows the fail; a spec saved from the form, reloaded and saved again is byte-identical |
| 3 | OSMO launch: executor, preflight, cancel, `campaign_launches` | launching `vio-osmo-xfs --only calm-r1` from the UI flies it, the row walks pending → passed live, and the verdict and ATE show; a mid-run cancel leaves no workflow running on OSMO and the row `aborted`; `campaign_launches` holds the spec that flew |

## 10. Risks and open questions

- **No authentication.** Anyone who reaches the dashboard on :3001 could launch
  on the cluster. Mitigations in this design:
  - the executor binds to localhost;
  - launching is off unless `DASHBOARD_ALLOW_LAUNCH=true`;
  - campaign paths are allowlisted, with no shell;
  - every launch is recorded.

  Real authentication is out of scope and is the prerequisite for any shared
  deployment.
- **One GPU.** One executor job at a time, and the page shows queue depth. KAI
  would queue extra gangs anyway, but a second campaign's runs would interleave
  in the registry.
- **Draft files in `scenarios/`.** They are gitignored, removed on save, and
  swept after 24 h.
- **The dashboard depends on the cluster** only through the opt-in `osmo`
  profile. The rest of the dashboard never waits on it.
- **Open:**
  - Should the editor ever write to git, as a commit or a PR?
  - On a shared cluster, does the executor move behind OSMO's own API, and
    the registry to a managed Postgres?
  - Should the VIO Stress page's ClickHouse view merge into `/campaigns/[id]`
    once registry and ClickHouse agree on `run_id` (the workflow id)?
