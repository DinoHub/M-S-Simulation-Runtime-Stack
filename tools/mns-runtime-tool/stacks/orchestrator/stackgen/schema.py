"""Compatibility adapter for the external MnS ScenarioSpec contract package."""
from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, NamedTuple

from .errors import SimstackError
from .paths import REPO_ROOT


CONTRACT_ROOT_ENV = "MNS_SCENARIOSPEC_CONTRACT_ROOT"
DEFAULT_CONTRACT_ROOT = REPO_ROOT / "contracts" / "mns-scenariospec"


def _configured_contract_root() -> Path:
    configured = os.environ.get(CONTRACT_ROOT_ENV)
    if configured:
        return Path(configured).expanduser().resolve()
    return DEFAULT_CONTRACT_ROOT


def _schema_path() -> Path:
    return _configured_contract_root() / "schema" / "ScenarioSpecSchema.yaml"


SCENARIOSPEC_SCHEMA_PATH = _schema_path()


class _ContractBindings(NamedTuple):
    error_type: type[Exception]
    load_schema: Callable[..., dict[str, Any]]
    validate_schema: Callable[[dict[str, Any]], None]
    validate_shape: Callable[..., None]


@lru_cache(maxsize=1)
def _load_contract() -> _ContractBindings:
    contract_root = _configured_contract_root()
    package_root = contract_root / "mns_scenariospec"
    schema_path = contract_root / "schema" / "ScenarioSpecSchema.yaml"

    if not package_root.is_dir() or not schema_path.is_file():
        raise SimstackError(
            "ScenarioSpec contract checkout is missing at "
            f"{contract_root}. Run tools/pull_contracts.sh from the platform repo, "
            f"or set {CONTRACT_ROOT_ENV} to a MnS-ScenarioSpec checkout."
        )

    contract_root_text = str(contract_root)
    if contract_root_text not in sys.path:
        sys.path.insert(0, contract_root_text)

    try:
        from mns_scenariospec import (  # type: ignore[import-not-found]
            ScenarioSpecError,
            load_scenariospec_schema,
            validate_scenariospec_shape,
            validate_schema_contract,
        )
    except ImportError as exc:
        raise SimstackError(
            f"failed to import ScenarioSpec contract package from {contract_root}: {exc}"
        ) from exc

    return _ContractBindings(
        error_type=ScenarioSpecError,
        load_schema=load_scenariospec_schema,
        validate_schema=validate_schema_contract,
        validate_shape=validate_scenariospec_shape,
    )


@lru_cache(maxsize=1)
def load_scenariospec_schema() -> dict[str, Any]:
    bindings = _load_contract()
    try:
        return bindings.load_schema(_schema_path())
    except bindings.error_type as exc:
        raise SimstackError(str(exc)) from exc


def validate_schema_contract(schema: dict[str, Any]) -> None:
    bindings = _load_contract()
    try:
        bindings.validate_schema(schema)
    except bindings.error_type as exc:
        raise SimstackError(str(exc)) from exc


def validate_scenariospec_shape(spec: dict[str, Any]) -> None:
    bindings = _load_contract()
    try:
        bindings.validate_shape(spec, _schema_path())
    except bindings.error_type as exc:
        raise SimstackError(str(exc)) from exc
