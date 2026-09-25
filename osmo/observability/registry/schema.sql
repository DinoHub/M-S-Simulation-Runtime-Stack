-- The TEVV run registry, schema 1.
--
-- Applied by `python3 osmo/registry.py migrate` as the database's owner
-- (tevv), so every object here is owned by the writer and readable by
-- grafana_ro. Idempotent: every statement is IF NOT EXISTS or OR REPLACE, so
-- re-running it on a live registry changes nothing.
--
-- What a row is. A run is one point of a campaign's matrix (campaign_id,
-- run_key), flown one or more times; each flight is an attempt, numbered by
-- the registry (max + 1), and an attempt is exactly one OSMO workflow
-- (workflow_ref, the join key to ClickHouse metric_results.run_id and to the
-- workflow_id pod label Loki carries). Trimmed from the architecture spec's
-- section 9.1: no suites or requirements tables yet -- a campaign's omega tier
-- and verifies ride on the campaign row until something needs them joined.
--
-- Status and reason are text with CHECK constraints rather than enum types, so
-- a later schema can add a value with one ALTER instead of a type rewrite.

CREATE TABLE IF NOT EXISTS schema_version (
    version     integer PRIMARY KEY,
    applied_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS campaigns (
    campaign_id  text PRIMARY KEY,
    name         text,
    spec_path    text,
    scenario     text,
    target       text NOT NULL DEFAULT 'osmo',
    tier         text,
    verifies     text[] NOT NULL DEFAULT '{}',
    platform     jsonb,
    gates        jsonb,
    git_sha      text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS runs (
    campaign_id      text NOT NULL REFERENCES campaigns ON DELETE CASCADE,
    run_key          text NOT NULL,
    attempt          smallint NOT NULL,
    variant          text,
    repeat           smallint,
    seed             bigint,
    scenario_id      text,
    vehicle          text,
    workflow_ref     text UNIQUE,
    workflow_status  text,
    tasks            jsonb NOT NULL DEFAULT '{}',
    status           text NOT NULL DEFAULT 'pending' CHECK (status IN (
                         'pending', 'submitted', 'running', 'evaluating',
                         'passed', 'failed', 'infra_failed', 'aborted')),
    failure_reason   text CHECK (failure_reason IN (
                         'stack_generation', 'image_pull', 'no_pilot', 'pod_crash',
                         'mission_failed', 'no_estimate', 'spawn_failed',
                         'gate_failed', 'no_evidence',
                         'infra', 'cancelled')),
    error            text,
    -- The recording checks (validate task). Advisory: they describe the
    -- evidence, the verdict's gates decide the status.
    recording_valid  boolean,
    failed_checks    text[] NOT NULL DEFAULT '{}',
    trigger          text,              -- manual | pr | schedule, later
    trigger_ref      text,              -- PR number or SHA, later
    images           jsonb,
    viz              jsonb,
    run_dir          text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    submitted_at     timestamptz,
    started_at       timestamptz,
    eval_started_at  timestamptz,
    ended_at         timestamptz,
    updated_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (campaign_id, run_key, attempt)
);
CREATE INDEX IF NOT EXISTS runs_status_idx ON runs (status);
CREATE INDEX IF NOT EXISTS runs_created_idx ON runs (campaign_id, created_at DESC);

-- One row per gate the verdict applied, as verdict.json reported it.
CREATE TABLE IF NOT EXISTS gate_results (
    campaign_id  text NOT NULL,
    run_key      text NOT NULL,
    attempt      smallint NOT NULL,
    gate         text NOT NULL,
    value        double precision,
    bound        jsonb,
    passed       boolean NOT NULL,
    detail       text,
    PRIMARY KEY (campaign_id, run_key, attempt, gate),
    FOREIGN KEY (campaign_id, run_key, attempt) REFERENCES runs ON DELETE CASCADE
);

-- The headline numbers per run, for comparing across a campaign without
-- opening ClickHouse. The full metric set stays in ClickHouse metric_results.
CREATE TABLE IF NOT EXISTS metrics_summary (
    campaign_id  text NOT NULL,
    run_key      text NOT NULL,
    attempt      smallint NOT NULL,
    metric       text NOT NULL,
    value        double precision NOT NULL,
    unit         text,
    source       text NOT NULL,          -- which evaluator measured it
    PRIMARY KEY (campaign_id, run_key, attempt, metric),
    FOREIGN KEY (campaign_id, run_key, attempt) REFERENCES runs ON DELETE CASCADE
);

-- Where a run's evidence is: host paths today, object-store URIs later.
CREATE TABLE IF NOT EXISTS artifacts (
    campaign_id  text NOT NULL,
    run_key      text NOT NULL,
    attempt      smallint NOT NULL,
    kind         text NOT NULL,          -- bag | eval | report | logs
    uri          text NOT NULL,
    bytes        bigint,
    PRIMARY KEY (campaign_id, run_key, attempt, kind, uri),
    FOREIGN KEY (campaign_id, run_key, attempt) REFERENCES runs ON DELETE CASCADE
);

-- A run's latest attempt: what the campaign's progress and scorecard count.
CREATE OR REPLACE VIEW v_latest_runs AS
SELECT DISTINCT ON (campaign_id, run_key) *
FROM runs
ORDER BY campaign_id, run_key, attempt DESC;

CREATE OR REPLACE VIEW v_campaign_progress AS
SELECT c.campaign_id,
       c.name,
       count(r.run_key)                                                    AS runs,
       count(*) FILTER (WHERE r.status = 'pending')                        AS pending,
       count(*) FILTER (WHERE r.status = 'submitted')                      AS submitted,
       count(*) FILTER (WHERE r.status = 'running')                        AS running,
       count(*) FILTER (WHERE r.status = 'evaluating')                     AS evaluating,
       count(*) FILTER (WHERE r.status = 'passed')                         AS passed,
       count(*) FILTER (WHERE r.status = 'failed')                         AS failed,
       count(*) FILTER (WHERE r.status IN ('infra_failed', 'aborted'))     AS infra,
       round(100.0 * count(*) FILTER (WHERE r.status = 'passed')
             / nullif(count(*) FILTER (WHERE r.status IN ('passed', 'failed')), 0), 1)
                                                                           AS pass_rate_pct,
       max(r.updated_at)                                                   AS last_update
FROM campaigns c
LEFT JOIN v_latest_runs r USING (campaign_id)
GROUP BY c.campaign_id, c.name;

-- Grafana's role is created by the operator (registry.yaml, managed roles);
-- this only grants it reads, now and on anything added later.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grafana_ro') THEN
        GRANT USAGE ON SCHEMA public TO grafana_ro;
        GRANT SELECT ON ALL TABLES IN SCHEMA public TO grafana_ro;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO grafana_ro;
    END IF;
END $$;

INSERT INTO schema_version (version) VALUES (1) ON CONFLICT DO NOTHING;
