"""ScenarioSpec schema loading and shape validation."""
from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


CONTRACT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCHEMA_PATH = CONTRACT_ROOT / "schema" / "ScenarioSpecSchema.yaml"
SCENARIOSPEC_FILENAMES = (
    "ScenarioSpec.yaml",
    "ScenarioSpec.yml",
    "scenario_spec.yaml",
    "scenario_spec.yml",
    "UserDefinition.yaml",
    "UserDefinition.yml",
)
SCENARIOSPEC_INCLUDE_GLOB_SUFFIXES = {".yaml", ".yml", ".json"}
LIST_SECTIONS = {"vehicles", "objects", "assets", "asset_packs", "random_spawns", "entities", "zones", "routes"}
UI_METADATA_KEYS = {"editor", "ui", "label", "control", "widget", "panel", "tab", "layout"}
PRIMITIVE_TYPES = {"any", "bool", "datetime", "float", "include_ref", "int", "number", "path", "string"}


class ScenarioSpecError(ValueError):
    """Raised when a ScenarioSpec schema or document is invalid."""


def load_yaml_document(path: Path) -> Any:
    if not path.is_file():
        raise ScenarioSpecError(f"missing YAML file: {path}")
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_yaml(path: Path) -> dict[str, Any]:
    data = load_yaml_document(path) or {}
    if not isinstance(data, dict):
        raise ScenarioSpecError(f"expected mapping in {path}")
    return data


@lru_cache(maxsize=8)
def load_scenariospec_schema(schema_path: str | Path | None = None) -> dict[str, Any]:
    path = Path(schema_path).expanduser().resolve() if schema_path else DEFAULT_SCHEMA_PATH
    if not path.is_file():
        raise ScenarioSpecError(f"ScenarioSpec schema file is missing: {path}")
    data = load_yaml(path)
    validate_schema_contract(data)
    return data


def validate_schema_contract(schema: dict[str, Any]) -> None:
    if schema.get("schema") != "mns.scenariospec.schema.v1":
        raise ScenarioSpecError("ScenarioSpec schema must declare schema: mns.scenariospec.schema.v1")
    if not schema.get("root_message"):
        raise ScenarioSpecError("ScenarioSpec schema must declare root_message")

    messages = schema.get("messages")
    if not isinstance(messages, dict) or not messages:
        raise ScenarioSpecError("ScenarioSpec schema must declare messages")
    enums = schema.get("enums") or {}
    if not isinstance(enums, dict):
        raise ScenarioSpecError("ScenarioSpec schema enums must be a mapping")
    root_message = str(schema["root_message"])
    if root_message not in messages:
        raise ScenarioSpecError(f"ScenarioSpec schema root_message is not defined: {root_message}")

    ui_path = _find_ui_metadata_key(schema)
    if ui_path:
        raise ScenarioSpecError(f"ScenarioSpec schema must not contain editor/UI metadata: {ui_path}")

    known_types = set(PRIMITIVE_TYPES) | set(messages.keys()) | set(enums.keys())
    for message_name, raw_message in messages.items():
        if not isinstance(raw_message, dict):
            raise ScenarioSpecError(f"schema message {message_name} must be a mapping")
        fields = raw_message.get("fields") or []
        if not isinstance(fields, list):
            raise ScenarioSpecError(f"schema message {message_name}.fields must be a list")

        seen_ids: set[int] = set()
        seen_names: set[str] = set()
        for raw_field in fields:
            if not isinstance(raw_field, dict):
                raise ScenarioSpecError(f"schema message {message_name} fields must be mappings")
            field_id = raw_field.get("id")
            field_name = raw_field.get("name")
            field_type = raw_field.get("type", "any")
            if not isinstance(field_id, int) or field_id <= 0:
                raise ScenarioSpecError(f"schema message {message_name} field {field_name or '<unnamed>'} must have a positive integer id")
            if field_id in seen_ids:
                raise ScenarioSpecError(f"schema message {message_name} repeats field id {field_id}")
            seen_ids.add(field_id)
            if not isinstance(field_name, str) or not field_name:
                raise ScenarioSpecError(f"schema message {message_name} field id {field_id} must have a name")
            if field_name in seen_names:
                raise ScenarioSpecError(f"schema message {message_name} repeats field name {field_name}")
            seen_names.add(field_name)
            for type_name in _referenced_type_names(str(field_type)):
                if type_name not in known_types:
                    raise ScenarioSpecError(f"schema message {message_name}.{field_name} references unknown type {type_name}")


def discover_scenariospec_path(path: str | Path) -> Path:
    candidate = Path(path).expanduser().resolve()
    if candidate.is_file():
        data = load_yaml(candidate)
        schema = str(data.get("schema", ""))
        if schema.startswith("mns.scenario."):
            return candidate
        if candidate.name in SCENARIOSPEC_FILENAMES:
            raise ScenarioSpecError(f"{candidate} must declare schema: mns.scenario.v1")
        raise ScenarioSpecError(f"{candidate} is not a ScenarioSpec file")

    if not candidate.is_dir():
        raise ScenarioSpecError(f"ScenarioSpec path does not exist: {candidate}")

    for filename in SCENARIOSPEC_FILENAMES:
        spec_path = candidate / filename
        if spec_path.is_file():
            data = load_yaml(spec_path)
            schema = str(data.get("schema", ""))
            if not schema.startswith("mns.scenario."):
                raise ScenarioSpecError(f"{spec_path} must declare schema: mns.scenario.v1")
            return spec_path
    raise ScenarioSpecError(f"no ScenarioSpec.yaml found in {candidate}")


def load_scenariospec_document(path: str | Path, schema_path: str | Path | None = None) -> dict[str, Any]:
    spec_path = discover_scenariospec_path(path)
    spec = load_yaml(spec_path)
    schema_id = str(spec.get("schema", ""))
    if not schema_id.startswith("mns.scenario."):
        raise ScenarioSpecError(f"{spec_path} must declare schema: mns.scenario.v1")
    schema = load_scenariospec_schema(schema_path)
    return _expand_scenariospec_includes(spec_path, spec, schema)


def validate_scenariospec_path(path: str | Path, schema_path: str | Path | None = None) -> dict[str, Any]:
    spec = load_scenariospec_document(path, schema_path)
    validate_scenariospec_shape(spec, schema_path)
    return spec


def validate_scenariospec_shape(spec: dict[str, Any], schema_path: str | Path | None = None) -> None:
    schema = load_scenariospec_schema(schema_path)
    root_message = str(schema["root_message"])
    _validate_message(spec, root_message, schema, root_message)


def _expand_scenariospec_includes(spec_path: Path, spec: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    includes = spec.get("includes")
    if includes is None:
        return copy.deepcopy(spec)

    base_dir = spec_path.parent
    expanded = copy.deepcopy({key: value for key, value in spec.items() if key != "includes"})
    include_aliases = {str(k): str(v) for k, v in (schema.get("include_aliases") or {}).items()}
    include_docs: list[dict[str, Any]] = []

    if isinstance(includes, list):
        for index, raw_ref in enumerate(includes):
            include_docs.append(_load_include_ref(base_dir, None, raw_ref, f"includes[{index}]", include_aliases))
    elif isinstance(includes, dict):
        for raw_key, raw_ref in includes.items():
            key = str(raw_key)
            section = include_aliases.get(key, key)
            include_docs.append(_load_include_ref(base_dir, section, raw_ref, f"includes.{key}", include_aliases))
    else:
        raise ScenarioSpecError("includes must be a mapping or list when provided")

    for doc in include_docs:
        expanded = _merge_scenariospec_dicts(expanded, doc)
    return expanded


def _load_include_ref(base_dir: Path, section: str | None, raw_ref: Any, label: str, include_aliases: dict[str, str]) -> dict[str, Any]:
    if isinstance(raw_ref, list):
        merged: dict[str, Any] = {}
        for index, item in enumerate(raw_ref):
            merged = _merge_scenariospec_dicts(merged, _load_include_ref(base_dir, section, item, f"{label}[{index}]", include_aliases))
        return merged
    if isinstance(raw_ref, dict):
        return _include_document_to_spec(section, copy.deepcopy(raw_ref), label, include_aliases)
    if not isinstance(raw_ref, str):
        raise ScenarioSpecError(f"{label} must be a path, mapping, or list")

    path = _resolve_ref(base_dir, raw_ref)
    if path.is_dir():
        if section not in LIST_SECTIONS:
            raise ScenarioSpecError(f"{label} points to a directory, but {section or 'this include'} is not a list section")
        merged = {section: []}
        files = sorted(
            item for item in path.rglob("*")
            if item.is_file() and item.suffix.lower() in SCENARIOSPEC_INCLUDE_GLOB_SUFFIXES
        )
        if not files:
            raise ScenarioSpecError(f"{label} directory contains no YAML/JSON include files: {path}")
        for item in files:
            merged = _merge_scenariospec_dicts(
                merged,
                _include_document_to_spec(section, _load_structured_document(item), str(item), include_aliases),
            )
        return merged
    if path.is_file():
        return _include_document_to_spec(section, _load_structured_document(path), str(path), include_aliases)
    raise ScenarioSpecError(f"{label} include path does not exist: {path}")


def _include_document_to_spec(section: str | None, data: Any, label: str, include_aliases: dict[str, str]) -> dict[str, Any]:
    if data is None:
        return {}
    if section in LIST_SECTIONS and isinstance(data, list):
        return {section: copy.deepcopy(data)}
    if not isinstance(data, dict):
        raise ScenarioSpecError(f"{label} must contain a mapping" + (f" or list for {section}" if section in LIST_SECTIONS else ""))

    if section in LIST_SECTIONS:
        if section in data or _looks_like_scenariospec_document(data, include_aliases):
            return copy.deepcopy(data)
        return {section: [copy.deepcopy(data)]}
    if section == "vehicle_models":
        if "vehicle_models" in data:
            return {"vehicle_models": copy.deepcopy(data["vehicle_models"])}
        if "vehicle_models_by_id" in data:
            return {"vehicle_models": copy.deepcopy(data["vehicle_models_by_id"])}
        return {"vehicle_models": copy.deepcopy(data)}
    if section is not None:
        if section in data or _looks_like_scenariospec_document(data, include_aliases):
            return copy.deepcopy(data)
        return {section: copy.deepcopy(data)}
    if _looks_like_scenariospec_document(data, include_aliases) or any(key in data for key in ("id", "name")):
        return copy.deepcopy(data)
    raise ScenarioSpecError(f"{label} must contain ScenarioSpec top-level fields")


def _looks_like_scenariospec_document(data: dict[str, Any], include_aliases: dict[str, str]) -> bool:
    document_keys = set(include_aliases.values()) | {
        "schema",
        "id",
        "name",
        "stack_name",
        "includes",
        "seed",
    }
    return any(key in document_keys for key in data)


def _load_structured_document(path: Path) -> Any:
    if path.suffix.lower() == ".json":
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    return load_yaml_document(path)


def _resolve_ref(base_dir: Path, raw_ref: str) -> Path:
    path = Path(raw_ref).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


def _merge_scenariospec_dicts(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key == "schema" and result.get("schema") and value == result.get("schema"):
            continue
        existing = result.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            result[key] = _merge_scenariospec_dicts(existing, value)
        elif isinstance(existing, list) and isinstance(value, list):
            result[key] = copy.deepcopy(existing) + copy.deepcopy(value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _validate_message(value: Any, message_name: str, schema: dict[str, Any], path: str) -> None:
    if not isinstance(value, dict):
        raise ScenarioSpecError(f"{path} must be a mapping")
    messages = schema["messages"]
    raw_message = messages.get(message_name)
    if not isinstance(raw_message, dict):
        raise ScenarioSpecError(f"schema message is not defined: {message_name}")
    fields = raw_message.get("fields") or []

    for field in fields:
        name = str(field["name"])
        required = bool(field.get("required", False))
        if name not in value:
            if required:
                raise ScenarioSpecError(f"{path}.{name} is required")
            continue
        field_value = value[name]
        if field_value is None:
            continue
        const = field.get("const")
        if const is not None and str(field_value) != str(const):
            raise ScenarioSpecError(f"{path}.{name} must be {const}")
        _validate_type(field_value, str(field.get("type", "any")), schema, f"{path}.{name}")

    if raw_message.get("unknown_fields") == "allow":
        return
    known = {str(field["name"]) for field in fields}
    for name in value.keys():
        if name not in known:
            raise ScenarioSpecError(f"{path}.{name} is not defined in the ScenarioSpec schema")


def _validate_type(value: Any, type_expr: str, schema: dict[str, Any], path: str) -> None:
    type_expr = type_expr.strip()
    if type_expr == "any":
        return
    if type_expr.startswith("oneof<") and type_expr.endswith(">"):
        errors: list[str] = []
        for item in _split_generic_args(type_expr[len("oneof<"):-1]):
            try:
                _validate_type(value, item, schema, path)
                return
            except ScenarioSpecError as exc:
                errors.append(str(exc))
        raise ScenarioSpecError(f"{path} does not match any allowed type: {'; '.join(errors)}")
    if type_expr.startswith("repeated<") and type_expr.endswith(">"):
        inner = type_expr[len("repeated<"):-1].strip()
        if not isinstance(value, list):
            raise ScenarioSpecError(f"{path} must be a list")
        for index, item in enumerate(value):
            _validate_type(item, inner, schema, f"{path}[{index}]")
        return
    if type_expr.startswith("map<") and type_expr.endswith(">"):
        args = _split_generic_args(type_expr[len("map<"):-1])
        if len(args) != 2:
            raise ScenarioSpecError(f"schema type {type_expr} must have key and value types")
        key_type, value_type = args
        if not isinstance(value, dict):
            raise ScenarioSpecError(f"{path} must be a mapping")
        for key, item in value.items():
            _validate_type(key, key_type, schema, f"{path}.<key>")
            _validate_type(item, value_type, schema, f"{path}.{key}")
        return
    if type_expr.startswith("catalog_ref<") and type_expr.endswith(">"):
        _validate_type(value, "string", schema, path)
        return

    if type_expr in ("string", "datetime", "path", "include_ref"):
        if not isinstance(value, str):
            raise ScenarioSpecError(f"{path} must be a string")
        return
    if type_expr == "bool":
        if not isinstance(value, bool):
            raise ScenarioSpecError(f"{path} must be a bool")
        return
    if type_expr == "int":
        if not isinstance(value, int) or isinstance(value, bool):
            raise ScenarioSpecError(f"{path} must be an int")
        return
    if type_expr in ("float", "number"):
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ScenarioSpecError(f"{path} must be a number")
        return

    messages = schema["messages"]
    if type_expr in messages:
        _validate_message(value, type_expr, schema, path)
        return

    enums = schema.get("enums") or {}
    if type_expr in enums:
        values = enums[type_expr].get("values") if isinstance(enums[type_expr], dict) else None
        if not isinstance(value, str):
            raise ScenarioSpecError(f"{path} must be a string")
        if isinstance(values, list) and value not in {str(item) for item in values}:
            raise ScenarioSpecError(f"{path} must be one of: {', '.join(str(item) for item in values)}")
        return

    raise ScenarioSpecError(f"schema references unknown type {type_expr} at {path}")


def _split_generic_args(text: str) -> list[str]:
    args: list[str] = []
    depth = 0
    start = 0
    for index, char in enumerate(text):
        if char == "<":
            depth += 1
        elif char == ">":
            depth -= 1
        elif char == "," and depth == 0:
            args.append(text[start:index].strip())
            start = index + 1
    args.append(text[start:].strip())
    return [arg for arg in args if arg]


def _referenced_type_names(type_expr: str) -> set[str]:
    type_expr = type_expr.strip()
    if type_expr.startswith("catalog_ref<") and type_expr.endswith(">"):
        return {"string"}
    if type_expr.startswith("repeated<") and type_expr.endswith(">"):
        return _referenced_type_names(type_expr[len("repeated<"):-1])
    if type_expr.startswith("oneof<") and type_expr.endswith(">"):
        refs: set[str] = set()
        for item in _split_generic_args(type_expr[len("oneof<"):-1]):
            refs.update(_referenced_type_names(item))
        return refs
    if type_expr.startswith("map<") and type_expr.endswith(">"):
        refs = set()
        for item in _split_generic_args(type_expr[len("map<"):-1]):
            refs.update(_referenced_type_names(item))
        return refs
    return {type_expr}


def _find_ui_metadata_key(value: Any, path: str = "schema") -> str | None:
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            next_path = f"{path}.{key_text}"
            if key_text in UI_METADATA_KEYS:
                return next_path
            found = _find_ui_metadata_key(item, next_path)
            if found:
                return found
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found = _find_ui_metadata_key(item, f"{path}[{index}]")
            if found:
                return found
    return None
