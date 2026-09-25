#!/usr/bin/env python3
"""The run registry: where a campaign's runs are, as they happen.

    python3 osmo/registry.py migrate      # apply observability/registry/schema.sql
    python3 osmo/registry.py url          # the connection string campaign.py uses

campaign.py writes here beside the files it has always written -- the
manifest and each run.json stay the evidence, and `campaign.py registry sync`
rebuilds the registry from them. So the registry is never the only copy of
anything, and a campaign must never fail because of it: when the registry
cannot be reached this warns once and every call becomes a no-op.

Where it is. MNS_REGISTRY_URL if set (MNS_REGISTRY=off turns it off);
otherwise the CNPG cluster `setup-local-osmo.sh observability` made -- its
writer credentials from the operator's `tevv-registry-app` secret, its
address the service node's InternalIP plus the NodePort, read live the way
campaign.py finds object storage, because a rebuilt cluster changes both.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

SCHEMA = Path(__file__).resolve().parent / "observability" / "registry" / "schema.sql"
NAMESPACE = "tevv"
SECRET = "tevv-registry-app"
SERVICE = "tevv-registry-nodeport"
NODE = "osmo-worker"

# Forward-only lifecycle: a poll that sees an older state never moves a run back.
ORDER = ["pending", "submitted", "running", "evaluating",
         "passed", "failed", "infra_failed", "aborted"]
EVAL_TASKS = {"vio-eval", "spawn-eval", "validate", "verdict"}
ACTIVE = {"INITIALIZING", "RUNNING", "COMPLETED", "FAILED"}


def _kubectl(*args: str) -> str:
    proc = subprocess.run(["kubectl", *args], capture_output=True, text=True, timeout=20)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"kubectl {' '.join(args)} failed")
    return proc.stdout.strip()


def url() -> str | None:
    if os.environ.get("MNS_REGISTRY", "").lower() in ("off", "0", "false", "no"):
        return None
    if os.environ.get("MNS_REGISTRY_URL"):
        return os.environ["MNS_REGISTRY_URL"]
    raw = json.loads(_kubectl("get", "secret", "-n", NAMESPACE, SECRET, "-o", "jsonpath={.data}"))
    field = {k: base64.b64decode(v).decode() for k, v in raw.items()}
    ip = _kubectl("get", "node", NODE, "-o",
                  'jsonpath={.status.addresses[?(@.type=="InternalIP")].address}')
    port = _kubectl("get", "svc", "-n", NAMESPACE, SERVICE, "-o", "jsonpath={.spec.ports[0].nodePort}")
    return (f"postgresql://{field['username']}:{field['password']}@{ip}:{port}/"
            f"{field.get('dbname', 'tevv')}?connect_timeout=5")


class Registry:
    """Every method is safe to call when the registry is off or gone."""

    def __init__(self, dsn: str | None = None, *, required: bool = False) -> None:
        self.conn = None
        self._warned = False
        try:
            # None: find the registry. "": deliberately off (a caller with none).
            dsn = url() if dsn is None else dsn
            if not dsn:
                return
            import psycopg2
            self.conn = psycopg2.connect(dsn)
            self.conn.autocommit = True
        except Exception as exc:  # noqa: BLE001 -- any failure means "off"
            if required:
                raise
            self._warn(exc)

    @property
    def on(self) -> bool:
        return self.conn is not None

    def _warn(self, exc: Exception) -> None:
        if not self._warned:
            print(f"[registry] off for this invocation: {str(exc).strip().splitlines()[0][:200]}",
                  file=sys.stderr, flush=True)
            self._warned = True
        self.conn = None

    def _exec(self, sql: str, params: Any = None, *, fetch: bool = False) -> Any:
        if self.conn is None:
            return None
        try:
            with self.conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchall() if fetch else None
        except Exception as exc:  # noqa: BLE001
            self._warn(exc)
            return None

    # ---- schema ---------------------------------------------------------
    def migrate(self) -> None:
        # Not through _exec: a schema that does not apply must say why.
        with self.conn.cursor() as cur:
            cur.execute(SCHEMA.read_text())

    # ---- campaign -------------------------------------------------------
    def upsert_campaign(self, campaign: dict[str, Any], *, spec_path: str, scenario: str,
                        tier: str | None, verifies: list[str], platform: dict[str, str] | None,
                        gates: dict[str, Any], git_sha: str | None = None,
                        campaign_id: str | None = None) -> None:
        self._exec("""
            INSERT INTO campaigns (campaign_id, name, spec_path, scenario, tier, verifies,
                                   platform, gates, git_sha)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (campaign_id) DO UPDATE SET
              name = EXCLUDED.name, spec_path = EXCLUDED.spec_path, scenario = EXCLUDED.scenario,
              tier = EXCLUDED.tier, verifies = EXCLUDED.verifies,
              platform = COALESCE(EXCLUDED.platform, campaigns.platform),
              gates = EXCLUDED.gates, git_sha = COALESCE(EXCLUDED.git_sha, campaigns.git_sha),
              updated_at = now()""",
            (campaign_id or str(campaign["id"]), campaign.get("name"), spec_path, scenario, tier,
             list(verifies or []), _j(platform), _j(gates), git_sha))

    # ---- one run --------------------------------------------------------
    def plan_attempt(self, campaign_id: str, run_key: str, *, variant: str, repeat: int,
                     seed: int | None, scenario_id: str | None, vehicle: str | None,
                     run_dir: str | None = None) -> int | None:
        """A new attempt for this run key, numbered max + 1, as pending."""
        rows = self._exec("""
            INSERT INTO runs (campaign_id, run_key, attempt, variant, repeat, seed,
                              scenario_id, vehicle, run_dir, trigger)
            SELECT %s, %s, COALESCE(max(attempt), 0) + 1, %s, %s, %s, %s, %s, %s, 'manual'
            FROM runs WHERE campaign_id = %s AND run_key = %s
            RETURNING attempt""",
            (campaign_id, run_key, variant, repeat, seed, scenario_id, vehicle, run_dir,
             campaign_id, run_key), fetch=True)
        return rows[0][0] if rows else None

    def submitted(self, campaign_id: str, run_key: str, attempt: int | None, workflow_ref: str,
                  *, images: dict[str, Any] | None = None, viz: bool | None = None,
                  at: str | None = None) -> None:
        if attempt is None:
            return
        self._exec("""
            UPDATE runs SET workflow_ref = %s, status = 'submitted',
                            submitted_at = COALESCE(submitted_at, %s::timestamptz),
                            images = %s, viz = COALESCE(%s, viz), updated_at = now()
            WHERE campaign_id = %s AND run_key = %s AND attempt = %s""",
            (workflow_ref, at, _j(images), _j({"foxglove": bool(viz)}) if viz is not None else None,
             campaign_id, run_key, attempt))

    def not_submitted(self, campaign_id: str, run_key: str, attempt: int | None,
                      reason: str, error: str) -> None:
        if attempt is None:
            return
        self._exec("""
            UPDATE runs SET status = 'failed', failure_reason = %s, error = %s,
                            ended_at = now(), updated_at = now()
            WHERE campaign_id = %s AND run_key = %s AND attempt = %s""",
            (reason, error[:2000], campaign_id, run_key, attempt))

    def progress(self, workflow_ref: str, workflow_status: str, tasks: dict[str, str]) -> None:
        """A poll of a live workflow: move the run forward, never back."""
        if any(s in ACTIVE for t, s in tasks.items() if t in EVAL_TASKS):
            status = "evaluating"
        elif any(s in ACTIVE for t, s in tasks.items() if t not in EVAL_TASKS):
            status = "running"
        else:
            status = "submitted"
        self._exec("""
            UPDATE runs SET
              status = CASE WHEN array_position(%s::text[], status) < array_position(%s::text[], %s)
                            THEN %s ELSE status END,
              started_at = CASE WHEN %s IN ('running', 'evaluating')
                                THEN COALESCE(started_at, now()) ELSE started_at END,
              eval_started_at = CASE WHEN %s = 'evaluating'
                                     THEN COALESCE(eval_started_at, now()) ELSE eval_started_at END,
              workflow_status = %s, tasks = %s, updated_at = now()
            WHERE workflow_ref = %s AND status NOT IN ('passed', 'failed', 'infra_failed', 'aborted')""",
            (ORDER, ORDER, status, status, status, status, workflow_status, _j(tasks), workflow_ref))

    def finish(self, workflow_ref: str, *, status: str, reason: str | None, error: str | None,
               workflow_status: str, tasks: dict[str, str], run_dir: str | None,
               recording_valid: bool | None, failed_checks: list[str],
               viz: dict[str, Any] | None = None, ended_at: str | None = None) -> None:
        self._exec("""
            UPDATE runs SET status = %s, failure_reason = %s, error = %s, workflow_status = %s,
                            tasks = %s, run_dir = COALESCE(%s, run_dir),
                            recording_valid = %s, failed_checks = %s,
                            viz = COALESCE(%s, viz),
                            started_at = COALESCE(started_at, submitted_at),
                            ended_at = COALESCE(%s::timestamptz, now()), updated_at = now()
            WHERE workflow_ref = %s""",
            (status, reason, (error or None) and error[:2000], workflow_status, _j(tasks), run_dir,
             recording_valid, list(failed_checks or []), _j(viz), ended_at, workflow_ref))

    def results(self, workflow_ref: str, *, gates: list[dict[str, Any]],
                metrics: list[tuple[str, float, str | None, str]],
                artifacts: list[tuple[str, str, int | None]]) -> None:
        """Gate decisions (verdict.json rows), headline metrics
        (name, value, unit, source) and artifacts (kind, uri, bytes).
        Replaces what the attempt held, so a re-sync is idempotent."""
        key = self._key(workflow_ref)
        if key is None:
            return
        for table in ("gate_results", "metrics_summary", "artifacts"):
            self._exec(f"DELETE FROM {table} WHERE campaign_id = %s AND run_key = %s AND attempt = %s",
                       key)
        for g in gates:
            # A gate a second report also measured: failing anywhere fails it.
            self._exec("""
                INSERT INTO gate_results (campaign_id, run_key, attempt, gate, value, bound, passed, detail)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (campaign_id, run_key, attempt, gate) DO UPDATE SET
                  passed = gate_results.passed AND EXCLUDED.passed,
                  value = CASE WHEN EXCLUDED.passed THEN gate_results.value ELSE EXCLUDED.value END,
                  detail = CASE WHEN EXCLUDED.passed THEN gate_results.detail ELSE EXCLUDED.detail END""",
                (*key, str(g["gate"]), g.get("value"), _j(g.get("bound")), bool(g.get("passed")),
                 g.get("detail")))
        for name, value, unit, source in metrics:
            self._exec("""
                INSERT INTO metrics_summary (campaign_id, run_key, attempt, metric, value, unit, source)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING""", (*key, name, value, unit, source))
        for kind, uri, size in artifacts:
            self._exec("""
                INSERT INTO artifacts (campaign_id, run_key, attempt, kind, uri, bytes)
                VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""", (*key, kind, uri, size))

    def add_artifact(self, workflow_ref: str, kind: str, uri: str, size: int | None = None) -> None:
        key = self._key(workflow_ref)
        if key is not None:
            self._exec("""
                INSERT INTO artifacts (campaign_id, run_key, attempt, kind, uri, bytes)
                VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""", (*key, kind, uri, size))

    def adopt(self, campaign_id: str, run_key: str, workflow_ref: str, **planned: Any) -> None:
        """For `registry sync`: make sure an attempt exists for a workflow that
        flew, numbering it after whatever the registry already holds."""
        if self._key(workflow_ref) is not None:
            return
        attempt = self.plan_attempt(campaign_id, run_key, **planned)
        self._exec("""UPDATE runs SET workflow_ref = %s, status = 'submitted'
                      WHERE campaign_id = %s AND run_key = %s AND attempt = %s""",
                   (workflow_ref, campaign_id, run_key, attempt))

    def _key(self, workflow_ref: str) -> tuple[str, str, int] | None:
        rows = self._exec("SELECT campaign_id, run_key, attempt FROM runs WHERE workflow_ref = %s",
                          (workflow_ref,), fetch=True)
        return tuple(rows[0]) if rows else None


def _j(value: Any) -> str | None:
    return None if value is None else json.dumps(value)


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "url":
        print(url() or "off")
        return 0
    if cmd == "migrate":
        reg = Registry(required=True)
        if not reg.on:
            print("[registry] off (MNS_REGISTRY)", file=sys.stderr)
            return 1
        reg.migrate()
        version = reg._exec("SELECT max(version) FROM schema_version", fetch=True)
        print(f"[registry] schema {version[0][0] if version else '?'} applied")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
