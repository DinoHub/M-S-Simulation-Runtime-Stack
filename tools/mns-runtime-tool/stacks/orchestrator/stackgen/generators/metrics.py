"""Metrics/logging contracts for generated runtime stacks."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..models import ResolvedScenario

METRICS_EVENT_SCHEMA = "metrics_emitter@2.0.0"
HOST_METRICS_DIR = Path("outputs") / "metrics"


def metrics_enabled(resolved: ResolvedScenario) -> bool:
    return resolved.metrics.enabled and resolved.runtime_profile != "editor"


def executable_saved_metrics_path(resolved: ResolvedScenario) -> str:
    executable_path = Path(resolved.airsim_executable)
    executable_dir = executable_path.parent.as_posix()
    project_name = executable_path.stem
    if executable_dir in ("", "."):
        executable_dir = "/app/Xfs"
    if not project_name:
        project_name = Path(executable_dir).name
    return f"{executable_dir}/{project_name}/Saved/metrics"


def metrics_manifest(resolved: ResolvedScenario, run_dir: Path) -> dict[str, Any]:
    host_dir = run_dir / HOST_METRICS_DIR
    enabled = metrics_enabled(resolved)
    finalize_args = ["--no-upload", "--no-load"]
    if resolved.metrics.archive_upload:
        finalize_args.remove("--no-upload")
    if resolved.metrics.archive_load_clickhouse:
        finalize_args.remove("--no-load")

    return {
        "enabled": enabled,
        "required": resolved.metrics.required,
        "requested": resolved.metrics.requested,
        "event_schema": METRICS_EVENT_SCHEMA,
        "mode": "offline_bundle",
        "host_dir": str(host_dir),
        "container_dir": executable_saved_metrics_path(resolved),
        "events_glob": str(host_dir / "run_*" / "events.jsonl"),
        "finalize_script": "stacks/scripts/finalize_metrics.sh",
        "finalize_args": finalize_args,
        "live_stream": {
            "enabled": bool(enabled and resolved.metrics.live_stream),
            "bind": resolved.metrics.stream_bind,
            "port": resolved.metrics.stream_port,
        },
        "archive": {
            "upload": resolved.metrics.archive_upload,
            "load_clickhouse": resolved.metrics.archive_load_clickhouse,
        },
        "environment": {
            "MNS_SCENARIO_ID": resolved.scenario_id,
            "MNS_METRICS_ENABLED": "true" if enabled else "false",
            "MNS_METRICS_OUTPUT": f"./{HOST_METRICS_DIR.as_posix()}",
            "MNS_METRICS_STREAM_ENABLED": "true" if enabled and resolved.metrics.live_stream else "false",
            "MNS_METRICS_STREAM_BIND": resolved.metrics.stream_bind,
            "MNS_METRICS_STREAM_PORT": str(resolved.metrics.stream_port),
            "MNS_METRICS_ARCHIVE_UPLOAD": "true" if resolved.metrics.archive_upload else "false",
            "MNS_METRICS_ARCHIVE_LOAD_CLICKHOUSE": "true" if resolved.metrics.archive_load_clickhouse else "false",
        },
    }
