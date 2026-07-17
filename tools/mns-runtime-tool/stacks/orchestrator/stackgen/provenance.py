"""Product provenance for generated ScenarioSpec stacks."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import yaml

from .errors import SimstackError
from .paths import REPO_ROOT
from .schema import SCENARIOSPEC_SCHEMA_PATH, load_scenariospec_schema


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise SimstackError(f"missing product metadata: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SimstackError(f"product metadata root must be a mapping: {path}")
    return data


def _git_ref(root: Path) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return proc.stdout.strip() or None


def _product_summary(root: Path) -> dict[str, Any]:
    data = _load_yaml(root / "mns-product.yaml")
    product_id = str(data.get("id") or "").strip()
    if not product_id:
        raise SimstackError(f"missing product id in {root / 'mns-product.yaml'}")
    return {
        "id": product_id,
        "name": str(data.get("name") or product_id),
        "version": str(data.get("version") or "unknown"),
        "root": str(root),
        "git_ref": _git_ref(root),
    }


def stack_generator_product() -> dict[str, Any]:
    summary = _product_summary(REPO_ROOT)
    product = _load_yaml(REPO_ROOT / "mns-product.yaml")
    provides = product.get("provides", {}).get("stack_generator", {})
    if isinstance(provides, dict):
        summary["provides"] = {
            "id": provides.get("id"),
            "entrypoint": provides.get("entrypoint"),
            "stack_schema": provides.get("stack_schema"),
        }
    return summary


def scenariospec_contract_product() -> dict[str, Any]:
    contract_root = SCENARIOSPEC_SCHEMA_PATH.parents[1]
    summary = _product_summary(contract_root)
    product = _load_yaml(contract_root / "mns-product.yaml")
    provides = product.get("provides", {}).get("scenariospec", {})
    schema = load_scenariospec_schema()

    if isinstance(provides, dict):
        summary["schema_id"] = str(provides.get("schema_id") or schema.get("schema_id") or "")
        summary["schema_version"] = provides.get("schema_version", schema.get("version"))
        summary["schema_path"] = str(contract_root / str(provides.get("schema_path") or "schema/ScenarioSpecSchema.yaml"))
    else:
        summary["schema_id"] = str(schema.get("schema_id") or "")
        summary["schema_version"] = schema.get("version")
        summary["schema_path"] = str(SCENARIOSPEC_SCHEMA_PATH)

    return summary


def generation_provenance() -> dict[str, Any]:
    return {
        "generated_by": stack_generator_product(),
        "contracts": {
            "scenariospec": scenariospec_contract_product(),
        },
        "validation": {
            "scenariospec": "passed",
        },
    }
