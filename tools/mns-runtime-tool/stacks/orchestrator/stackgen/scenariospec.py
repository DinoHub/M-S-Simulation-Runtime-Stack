"""Resolve ScenarioSpec user definitions into generator-ready models."""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import yaml

from .autopilots import get_autopilot_profile
from .errors import SimstackError
from .models import MetricsConfig, ObjectClutter, ResolvedScenario, Vehicle
from .paths import ORCHESTRATOR_ROOT
from .scenario_runtime import DEFAULT_STATIC_OBSTACLE_CLASS, ScenarioRuntimeConfig, merge_dicts
from .schema import validate_scenariospec_shape
from .sensors import SensorConfigError, render_vehicle_sensors


SCENARIOSPEC_FILENAMES = (
    "ScenarioSpec.yaml",
    "ScenarioSpec.yml",
    "scenario_spec.yaml",
    "scenario_spec.yml",
    "UserDefinition.yaml",
    "UserDefinition.yml",
)

SCENARIOSPEC_TOP_LEVEL_KEYS = {
    "schema",
    "id",
    "name",
    "stack_name",
    "catalogs",
    "environment",
    "coordinate_frame",
    "runtime",
    "scenario_runtime",
    "conditions",
    "object_clutter",
    "random_spawns",
    "vehicles",
    "objects",
    "assets",
    "asset_packs",
    "entities",
    "zones",
    "routes",
    "sensor_profiles",
    "vehicle_models",
    "extensions",
    "seed",
}

INCLUDE_SECTION_ALIASES = {
    "environment": "environment",
    "coordinate_frame": "coordinate_frame",
    "runtime": "runtime",
    "scenario_runtime": "scenario_runtime",
    "conditions": "conditions",
    "weather": "conditions",
    "time_of_day": "conditions",
    "object_clutter": "object_clutter",
    "random_spawns": "random_spawns",
    "random_spawn": "random_spawns",
    "spawn_volumes": "random_spawns",
    "vehicles": "vehicles",
    "vehicle": "vehicles",
    "objects": "objects",
    "object": "objects",
    "assets": "assets",
    "asset": "assets",
    "asset_packs": "asset_packs",
    "entities": "entities",
    "zones": "zones",
    "routes": "routes",
    "sensor_profiles": "sensor_profiles",
    "vehicle_models": "vehicle_models",
    "extensions": "extensions",
    "catalogs": "catalogs",
}

LIST_SECTIONS = {"vehicles", "objects", "assets", "asset_packs", "random_spawns", "entities", "zones", "routes"}
SCENARIOSPEC_INCLUDE_GLOB_SUFFIXES = {".yaml", ".yml", ".json"}
RUNTIME_BINDING_FILENAMES = (
    "runtime_binding.local.yaml",
    "runtime_binding.yaml",
    "mns_runtime_binding.yaml",
)
VEHICLE_CONNECTION_KEYS = {
    "autopilot_host",
    "udp_port",
    "tcp_port",
    "control_port",
    "editor_udp_port",
    "editor_control_port",
    "mavros_tcp_port",
    "mavros_udp_port",
    "mavros_local_port",
    "mavros_fcu_url",
}


def load_yaml_document(path: Path) -> Any:
    if not path.is_file():
        raise SimstackError(f"missing YAML file: {path}")
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_yaml(path: Path) -> dict[str, Any]:
    data = load_yaml_document(path) or {}
    if not isinstance(data, dict):
        raise SimstackError(f"expected mapping in {path}")
    return data


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "scenario"


def _runtime_name_suffix(value: str) -> int | None:
    match = re.search(r"(\d+)$", value.strip())
    return int(match.group(1)) if match else None


def _order_numbered_runtime_vehicles(vehicle_defs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep generated vehicle indices aligned with runtime names like Copter1."""
    keyed: list[tuple[int, int, dict[str, Any]]] = []
    for position, vehicle in enumerate(vehicle_defs):
        runtime_name = str(vehicle.get("definition", {}).get("runtime_name") or "")
        runtime_index = _runtime_name_suffix(runtime_name)
        if runtime_index is None:
            return vehicle_defs
        keyed.append((runtime_index, position, vehicle))

    runtime_indices = [item[0] for item in keyed]
    if len(set(runtime_indices)) != len(runtime_indices):
        return vehicle_defs
    return [vehicle for _, _, vehicle in sorted(keyed)]


def discover_scenariospec_path(path: str | Path) -> Path:
    candidate = Path(path).expanduser().resolve()
    if candidate.is_file():
        data = load_yaml(candidate)
        schema = str(data.get("schema", ""))
        if schema.startswith("mns.scenario."):
            return candidate
        if candidate.name in SCENARIOSPEC_FILENAMES:
            raise SimstackError(f"{candidate} must declare schema: mns.scenario.v1")
        raise SimstackError(f"{candidate} is not a ScenarioSpec file")

    if not candidate.is_dir():
        raise SimstackError(f"ScenarioSpec path does not exist: {candidate}")

    for filename in SCENARIOSPEC_FILENAMES:
        spec_path = candidate / filename
        if spec_path.is_file():
            data = load_yaml(spec_path)
            schema = str(data.get("schema", ""))
            if not schema.startswith("mns.scenario."):
                raise SimstackError(f"{spec_path} must declare schema: mns.scenario.v1")
            return spec_path
    raise SimstackError(f"no ScenarioSpec.yaml found in {candidate}")


def resolve_scenariospec_input(
    scenario_or_folder: str | Path,
    out_dir: Path | None = None,
    profile: str = "docker",
) -> ResolvedScenario:
    return resolve_scenariospec(discover_scenariospec_path(scenario_or_folder), out_dir=out_dir, profile=profile)


def resolve_scenariospec(
    spec_path: Path,
    out_dir: Path | None = None,
    profile: str = "docker",
) -> ResolvedScenario:
    spec_path = spec_path.expanduser().resolve()
    spec = load_yaml(spec_path)
    schema = str(spec.get("schema", ""))
    if not schema.startswith("mns.scenario."):
        raise SimstackError(f"{spec_path} must declare schema: mns.scenario.v1")
    if profile not in ("docker", "editor"):
        raise SimstackError("profile must be 'docker' or 'editor'")

    spec = _expand_scenariospec_includes(spec_path, spec)
    validate_scenariospec_shape(spec)
    catalogs = _load_catalogs(spec_path.parent, spec)
    runtime = _resolve_runtime(spec, catalogs)
    environment = _resolve_environment(spec, catalogs, spec_path.parent)
    vehicle_defs = _order_numbered_runtime_vehicles(_normalize_vehicles(spec))
    asset_config = _normalize_assets(spec, catalogs)
    random_spawns = _normalize_random_spawns(spec, catalogs)
    object_clutter = _resolve_object_clutter(spec, asset_config)
    scenario_runtime = _resolve_scenario_runtime(spec, environment)
    conditions = _normalize_conditions(spec, environment)
    metrics = _normalize_metrics(spec)

    autopilot_cfg = runtime["autopilot"]
    autopilot_type = str(autopilot_cfg.get("type", "ardupilot")).lower()
    autopilot_profile = get_autopilot_profile(autopilot_type)
    managed = bool(autopilot_cfg.get("managed", False))
    endpoint = str(autopilot_cfg.get("endpoint", "host"))
    endpoint_mode = endpoint.lower()
    endpoint_prefix = str(autopilot_cfg.get("hostname_prefix", autopilot_profile.default_hostname_prefix))

    vehicles: list[Vehicle] = []
    for index, vehicle in enumerate(vehicle_defs, start=1):
        data = vehicle["definition"]
        source_name = str(data.get("name") or f"Vehicle{index}")
        runtime_name = str(data.get("runtime_name") or autopilot_profile.default_runtime_name(index))
        autopilot_host = autopilot_profile.resolve_autopilot_host(
            data,
            endpoint=endpoint,
            endpoint_mode=endpoint_mode,
            endpoint_prefix=endpoint_prefix,
            runtime_profile=profile,
            managed=managed,
            index=index,
        )

        vehicle_model = _resolve_vehicle_model(catalogs, str(data.get("vehicle_type") or "quadrotor_small"), source_name)

        try:
            rendered_sensors = render_vehicle_sensors(data)
        except SensorConfigError as exc:
            raise SimstackError(f"vehicle {source_name}: {exc}") from exc

        connection = autopilot_profile.resolve_connection(
            data,
            index=index,
            runtime_profile=profile,
            endpoint_mode=endpoint_mode,
            managed=managed,
            autopilot_host=autopilot_host,
        )
        vehicles.append(Vehicle(
            source_name=source_name,
            runtime_name=runtime_name,
            index=index,
            vehicle_type=vehicle_model["id"],
            spawn=_normalize_spawn(data, index),
            sensors=rendered_sensors.sensors,
            cameras=rendered_sensors.cameras,
            ros_domain_id=int(data.get("ros_domain_id", index)),
            connection=connection,
            pawn_path=vehicle_model.get("pawn_path"),
            pawn_bp=vehicle_model.get("pawn_bp"),
            runtime_asset_pack=vehicle_model.get("runtime_asset_pack"),
        ))

    scenario_id = slugify(str(spec.get("id") or spec.get("name") or spec_path.parent.name))
    name = str(spec.get("name") or spec.get("id") or spec_path.parent.name)
    env_name = str(environment.get("name", "xfs")).lower()
    suffix = "single" if len(vehicles) == 1 else "multi"
    default_stack_name = f"{autopilot_type}-{env_name}-{slugify(name)}-{suffix}"
    stack_name = slugify(str(spec.get("stack_name") or (out_dir.name if out_dir else default_stack_name)))
    if not stack_name.startswith(f"{autopilot_type}-{env_name}-"):
        stack_name = default_stack_name
    if profile == "editor" and not stack_name.endswith("-editor"):
        stack_name = f"{stack_name}-editor"

    features = runtime["features"]
    return ResolvedScenario(
        scenario_id=scenario_id,
        name=name,
        stack_name=stack_name,
        runtime_profile=profile,
        environment_name=env_name,
        airsim_image=str(environment.get("image", "local/auto_mns:tevv-airsim-xfs-latest")),
        airsim_executable=str(environment.get("executable_path", "/app/Xfs/Xfs.sh")),
        autopilot_type=autopilot_type,
        autopilot_managed=managed,
        autopilot_endpoint=endpoint,
        autopilot_hostname_prefix=endpoint_prefix,
        qgroundcontrol=bool(features.get("qgroundcontrol", True)),
        ros2_bridge=bool(features.get("ros2_bridge", True)),
        bridge_mavros=bool(features.get("mavros", features.get("bridge_mavros", False))),
        origin=environment.get("origin") or {},
        time_of_day=conditions.get("time_of_day") or environment.get("time_of_day") or {},
        conditions=conditions,
        object_clutter=object_clutter,
        scenario_runtime=scenario_runtime,
        vehicles=vehicles,
        source_files=_scenariospec_source_files(spec_path),
        random_spawns=random_spawns,
        source_root=spec_path.parent,
        runtime_artifact=environment.get("runtime_artifact") or {},
        metrics=metrics,
    )



def _normalize_metrics(spec: dict[str, Any]) -> MetricsConfig:
    extensions = spec.get("extensions") or {}
    if extensions is None:
        extensions = {}
    if not isinstance(extensions, dict):
        raise SimstackError("extensions must be a mapping when provided")

    raw = extensions.get("mns.metrics", {})
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise SimstackError("extensions.mns.metrics must be a mapping when provided")

    collection = raw.get("collection") or {}
    if collection is None:
        collection = {}
    if not isinstance(collection, dict):
        raise SimstackError("extensions.mns.metrics.collection must be a mapping when provided")

    archive = raw.get("archive") or {}
    if archive is None:
        archive = {}
    if not isinstance(archive, dict):
        raise SimstackError("extensions.mns.metrics.archive must be a mapping when provided")

    requested_raw = raw.get("requested", [])
    if requested_raw is None:
        requested: list[str] = []
    elif isinstance(requested_raw, list):
        requested = [str(item) for item in requested_raw]
    else:
        raise SimstackError("extensions.mns.metrics.requested must be a list when provided")

    stream_port = int(collection.get("stream_port", raw.get("stream_port", 9700)))
    if not 1 <= stream_port <= 65535:
        raise SimstackError("extensions.mns.metrics.collection.stream_port must be between 1 and 65535")

    return MetricsConfig(
        enabled=_bool_value(raw.get("enabled"), True),
        required=_bool_value(raw.get("required"), False),
        requested=requested,
        live_stream=_bool_value(collection.get("live_stream", raw.get("live_stream")), False),
        stream_port=stream_port,
        stream_bind=str(collection.get("stream_bind", raw.get("stream_bind", "0.0.0.0"))),
        archive_upload=_bool_value(archive.get("upload"), False),
        archive_load_clickhouse=_bool_value(archive.get("load_clickhouse", archive.get("load")), False),
        raw=copy.deepcopy(raw),
    )


def _normalize_conditions(spec: dict[str, Any], environment: dict[str, Any]) -> dict[str, Any]:
    raw = spec.get("conditions") or {}
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise SimstackError("conditions must be a mapping when provided")

    conditions = copy.deepcopy(raw)
    env_time_of_day = environment.get("time_of_day")
    if "time_of_day" not in conditions and isinstance(env_time_of_day, dict):
        conditions["time_of_day"] = copy.deepcopy(env_time_of_day)
    env_weather = environment.get("weather")
    if "weather" not in conditions and isinstance(env_weather, dict):
        conditions["weather"] = copy.deepcopy(env_weather)

    if conditions.get("time_of_day") is None:
        conditions.pop("time_of_day", None)
    elif not isinstance(conditions.get("time_of_day"), dict):
        raise SimstackError("conditions.time_of_day must be a mapping when provided")

    if conditions.get("weather") is None:
        conditions.pop("weather", None)
    elif not isinstance(conditions.get("weather"), dict):
        raise SimstackError("conditions.weather must be a mapping when provided")

    return conditions


def _expand_scenariospec_includes(spec_path: Path, spec: dict[str, Any]) -> dict[str, Any]:
    includes = spec.get("includes")
    if includes is None:
        return copy.deepcopy(spec)

    base_dir = spec_path.parent
    expanded = copy.deepcopy({key: value for key, value in spec.items() if key != "includes"})
    include_docs: list[dict[str, Any]] = []

    if isinstance(includes, list):
        for index, raw_ref in enumerate(includes):
            include_docs.append(_load_include_ref(base_dir, None, raw_ref, f"includes[{index}]"))
    elif isinstance(includes, dict):
        for raw_key, raw_ref in includes.items():
            key = str(raw_key)
            section = INCLUDE_SECTION_ALIASES.get(key, key)
            include_docs.append(_load_include_ref(base_dir, section, raw_ref, f"includes.{key}"))
    else:
        raise SimstackError("includes must be a mapping or list when provided")

    for doc in include_docs:
        expanded = _merge_scenariospec_dicts(expanded, doc)
    return expanded


def _load_include_ref(base_dir: Path, section: str | None, raw_ref: Any, label: str) -> dict[str, Any]:
    if isinstance(raw_ref, list):
        merged: dict[str, Any] = {}
        for index, item in enumerate(raw_ref):
            merged = _merge_scenariospec_dicts(merged, _load_include_ref(base_dir, section, item, f"{label}[{index}]"))
        return merged
    if isinstance(raw_ref, dict):
        return _include_document_to_spec(section, copy.deepcopy(raw_ref), label)
    if not isinstance(raw_ref, str):
        raise SimstackError(f"{label} must be a path, mapping, or list")

    path = _resolve_ref(base_dir, raw_ref)
    if path.is_dir():
        if section not in LIST_SECTIONS:
            raise SimstackError(f"{label} points to a directory, but {section or 'this include'} is not a list section")
        merged = {section: []}
        files = sorted(
            item for item in path.rglob("*")
            if item.is_file() and item.suffix.lower() in SCENARIOSPEC_INCLUDE_GLOB_SUFFIXES
        )
        if not files:
            raise SimstackError(f"{label} directory contains no YAML/JSON include files: {path}")
        for item in files:
            merged = _merge_scenariospec_dicts(
                merged,
                _include_document_to_spec(section, _load_structured_document(item), str(item)),
            )
        return merged
    if path.is_file():
        return _include_document_to_spec(section, _load_structured_document(path), str(path))
    raise SimstackError(f"{label} include path does not exist: {path}")


def _load_structured_document(path: Path) -> Any:
    if path.suffix.lower() == ".json":
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    return load_yaml_document(path)


def _include_document_to_spec(section: str | None, data: Any, label: str) -> dict[str, Any]:
    if data is None:
        return {}
    if section in LIST_SECTIONS and isinstance(data, list):
        return {section: copy.deepcopy(data)}
    if not isinstance(data, dict):
        raise SimstackError(f"{label} must contain a mapping" + (f" or list for {section}" if section in LIST_SECTIONS else ""))

    if section in LIST_SECTIONS:
        if section in data or _looks_like_scenariospec_document(data):
            return copy.deepcopy(data)
        return {section: [copy.deepcopy(data)]}
    if section == "vehicle_models":
        if "vehicle_models" in data:
            return {"vehicle_models": copy.deepcopy(data["vehicle_models"])}
        if "vehicle_models_by_id" in data:
            return {"vehicle_models": copy.deepcopy(data["vehicle_models_by_id"])}
        return {"vehicle_models": copy.deepcopy(data)}
    if section is not None:
        if section in data or _looks_like_scenariospec_document(data):
            return copy.deepcopy(data)
        return {section: copy.deepcopy(data)}
    if _looks_like_scenariospec_document(data) or any(key in data for key in ("id", "name")):
        return copy.deepcopy(data)
    raise SimstackError(f"{label} must contain ScenarioSpec top-level fields")


def _looks_like_scenariospec_document(data: dict[str, Any]) -> bool:
    document_keys = SCENARIOSPEC_TOP_LEVEL_KEYS - {"id", "name"}
    return any(key in document_keys for key in data)


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


def _load_catalogs(base_dir: Path, spec: dict[str, Any]) -> dict[str, Any]:
    catalogs = {
        "environments": _load_catalog_section(ORCHESTRATOR_ROOT / "catalogs" / "environments.yaml", "environments"),
        "runtime_profiles": _load_catalog_section(ORCHESTRATOR_ROOT / "catalogs" / "runtime_profiles.yaml", "runtime_profiles"),
        "asset_packs": _load_catalog_section(ORCHESTRATOR_ROOT / "catalogs" / "asset_packs.yaml", "asset_packs"),
        "vehicle_models": _load_catalog_section(ORCHESTRATOR_ROOT / "catalogs" / "vehicle_models.yaml", "vehicle_models"),
    }

    # Local authoring exports keep bundle metadata beside the human-edited
    # ScenarioSpec folder. Discover those overlays by convention so users do not
    # need to keep catalog paths in the root manifest.
    conventional_catalogs = {
        "environments": base_dir / "ScenarioBundle" / "catalogs" / "environments.yaml",
        "runtime_profiles": base_dir / "ScenarioBundle" / "catalogs" / "runtime_profiles.yaml",
        "asset_packs": base_dir / "ScenarioBundle" / "catalogs" / "asset_packs.yaml",
        "vehicle_models": base_dir / "ScenarioBundle" / "catalogs" / "vehicle_models.yaml",
    }
    for key, path in conventional_catalogs.items():
        if path.is_file():
            catalogs[key] = merge_dicts(catalogs[key], _load_catalog_section(path, key))

    inline_vehicle_models = spec.get("vehicle_models")
    if inline_vehicle_models is not None:
        if not isinstance(inline_vehicle_models, dict):
            raise SimstackError("vehicle_models must be a mapping when provided")
        catalogs["vehicle_models"] = merge_dicts(catalogs["vehicle_models"], inline_vehicle_models)

    overrides = spec.get("catalogs") or {}
    if not overrides:
        return catalogs
    if not isinstance(overrides, dict):
        raise SimstackError("catalogs must be a mapping when provided")
    for key, raw_path in overrides.items():
        if key not in catalogs:
            raise SimstackError(f"unsupported catalog override: {key}")
        path = _resolve_ref(base_dir, raw_path)
        override = _load_catalog_section(path, key)
        catalogs[key] = merge_dicts(catalogs[key], override)
    return catalogs


def _load_catalog_section(path: Path, key: str) -> dict[str, Any]:
    data = load_yaml(path)
    section = data.get(key)
    if section is None and key == "vehicle_models":
        section = data.get("vehicle_models_by_id")
    section = section or {}
    if not isinstance(section, dict):
        raise SimstackError(f"{path}:{key} must be a mapping")
    return copy.deepcopy(section)


def _load_json_or_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise SimstackError(f"missing manifest file: {path}")
    if path.suffix.lower() == ".json":
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f) or {}
    else:
        data = load_yaml(path)
    if not isinstance(data, dict):
        raise SimstackError(f"expected mapping in {path}")
    return data


def _resolve_bundle_file(source_root: Path, raw_path: Any, *, label: str) -> Path:
    raw = str(raw_path).strip()
    if not raw:
        raise SimstackError(f"{label} must not be empty")
    path = Path(raw).expanduser()
    if path.is_absolute():
        if path.is_file():
            return path.resolve()
        raise SimstackError(f"{label} is not a readable file: {raw}")

    candidates = [source_root / "ScenarioBundle" / path, source_root / path, Path.cwd() / path]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
        if candidate.is_dir():
            manifest = candidate / "mns_level_pack.json"
            if manifest.is_file():
                return manifest.resolve()
    raise SimstackError(f"{label} is not a readable local file or MnS bundle directory: {raw}")


def _runtime_binding_candidates(level_pack_manifest: Path, level_id: str) -> list[Path]:
    bundle_root = level_pack_manifest.parent
    names = list(RUNTIME_BINDING_FILENAMES)
    if level_id:
        names.extend([
            f"{level_id}.runtime_binding.yaml",
            f"{level_id}.runtime_binding.yml",
            f"{level_id}.runtime_binding.json",
        ])
    candidates: list[Path] = []
    seen: set[Path] = set()
    for name in names:
        candidate = (bundle_root / name).resolve()
        if candidate not in seen:
            candidates.append(candidate)
            seen.add(candidate)
    return candidates


def _normalize_runtime_binding(raw: dict[str, Any]) -> dict[str, Any]:
    runtime = copy.deepcopy(raw)
    aliases = {
        "runtime_image": "image",
        "image_uri": "image",
        "executable": "executable_path",
        "runtime_executable": "executable_path",
        "runtime_map": "map",
        "target_id": "runtime_target_id",
    }
    for source_key, target_key in aliases.items():
        if source_key in runtime and target_key not in runtime:
            runtime[target_key] = runtime[source_key]
    allowed = {
        "environment_name",
        "image",
        "executable_path",
        "pak_id",
        "pak_name",
        "mode",
        "map",
        "runtime_target_id",
        "world_mode",
    }
    return {key: copy.deepcopy(value) for key, value in runtime.items() if key in allowed and value not in (None, "")}


def _runtime_binding_from_document(path: Path, level_id: str) -> dict[str, Any]:
    data = _load_json_or_yaml(path)
    schema = str(data.get("schema") or "")
    if schema and schema != "mns.runtime_binding.v1":
        raise SimstackError(f"{path} must declare schema: mns.runtime_binding.v1")

    bindings = data.get("bindings")
    raw: Any = data
    if isinstance(bindings, dict):
        raw = bindings.get(level_id) or bindings.get("*") or bindings.get("default") or {}
    elif data.get("level_id") and str(data.get("level_id")) != level_id:
        return {}

    if not isinstance(raw, dict):
        raise SimstackError(f"{path} runtime binding for {level_id} must be a mapping")
    if isinstance(raw.get("runtime"), dict):
        raw = raw["runtime"]
    return _normalize_runtime_binding(raw)


def _runtime_binding_for_level_pack(level_pack_manifest: Path, level_id: str) -> dict[str, Any]:
    for candidate in _runtime_binding_candidates(level_pack_manifest, level_id):
        if candidate.is_file():
            return _runtime_binding_from_document(candidate, level_id)
    return {}


def _environment_from_level_pack(path: Path, source_ref: str) -> dict[str, Any]:
    manifest = _load_json_or_yaml(path)
    schema = str(manifest.get("schema") or "")
    if schema and schema != "mns.level_pack.v1":
        raise SimstackError(f"{path} must declare schema: mns.level_pack.v1")

    level = _mapping(manifest.get("level"), f"{path}.level")
    editor = _mapping(manifest.get("editor"), f"{path}.editor")
    runtime = _mapping(manifest.get("runtime"), f"{path}.runtime")
    runtime_target = _mapping(manifest.get("runtime_target"), f"{path}.runtime_target")

    level_id = str(level.get("level_id") or manifest.get("level_id") or manifest.get("pack_id") or path.parent.name)
    runtime_binding = _runtime_binding_for_level_pack(path, level_id)
    if runtime_binding:
        runtime = merge_dicts(runtime, runtime_binding)

    map_path = runtime.get("map") or editor.get("map") or level.get("map")
    runtime_target_id = runtime.get("runtime_target_id") or runtime_target.get("id") or level.get("runtime_target_id")

    runtime_artifact = {
        "level_id": level_id,
        "display_name": str(level.get("display_name") or level.get("name") or level_id),
        "version": str(level.get("version") or manifest.get("pack_version") or "local"),
        "level_pack": source_ref,
    }
    for key, value in (
        ("map", map_path),
        ("pak_id", runtime.get("pak_id")),
        ("pak_name", runtime.get("pak_name")),
        ("runtime_target_id", runtime_target_id),
        ("mode", runtime.get("mode")),
    ):
        if value:
            runtime_artifact[key] = str(value)

    environment = {
        "name": str(runtime.get("environment_name") or level_id),
        "runtime_artifact": runtime_artifact,
    }
    if runtime.get("image"):
        environment["image"] = str(runtime["image"])
    if runtime.get("executable_path"):
        environment["executable_path"] = str(runtime["executable_path"])

    if map_path:
        environment["scenario_runtime"] = {
            "enabled": True,
            "world": {
                "mode": str(runtime.get("world_mode") or "authored"),
                "map": str(map_path),
            },
        }
    return environment


def _resolve_vehicle_model(catalogs: dict[str, Any], vehicle_type: str, source_name: str) -> dict[str, Any]:
    requested = str(vehicle_type or "quadrotor_small").strip() or "quadrotor_small"
    models = catalogs.get("vehicle_models") or {}
    if not isinstance(models, dict):
        raise SimstackError("vehicle_models catalog must be a mapping")

    match_id = ""
    match_model: dict[str, Any] | None = None
    requested_lower = requested.lower()
    for model_id, raw_model in models.items():
        if not isinstance(raw_model, dict):
            continue
        identifiers = [str(model_id), str(raw_model.get("id") or "")]
        aliases = raw_model.get("aliases") or raw_model.get("alias") or []
        if isinstance(aliases, str):
            aliases = [aliases]
        if isinstance(aliases, list):
            identifiers.extend(str(alias) for alias in aliases)
        if requested_lower in {identifier.lower() for identifier in identifiers if identifier}:
            match_id = str(raw_model.get("id") or model_id)
            match_model = copy.deepcopy(raw_model)
            break

    if match_model is None:
        known = ", ".join(sorted(str(key) for key in models.keys())) or "<none>"
        raise SimstackError(f"vehicle {source_name}: unknown vehicle_type '{requested}'. Known vehicle models: {known}")

    runtime = match_model.get("runtime") if isinstance(match_model.get("runtime"), dict) else {}
    airsim = runtime.get("airsim") if isinstance(runtime.get("airsim"), dict) else {}
    if not airsim and isinstance(match_model.get("airsim"), dict):
        airsim = match_model["airsim"]

    pawn_path = (
        airsim.get("pawn_path")
        or airsim.get("PawnPath")
        or runtime.get("pawn_path")
        or match_model.get("pawn_path")
    )
    pawn_bp = (
        airsim.get("pawn_bp")
        or airsim.get("pawn_class")
        or airsim.get("PawnBP")
        or runtime.get("pawn_bp")
        or match_model.get("pawn_bp")
    )
    if pawn_bp and not pawn_path:
        pawn_path = f"{slugify(match_id).replace('-', '_')}_pawn"
    if pawn_path and not pawn_bp:
        raise SimstackError(f"vehicle {source_name}: vehicle_type '{requested}' defines pawn_path but not pawn_bp")

    resolved: dict[str, Any] = {"id": match_id}
    if pawn_path:
        resolved["pawn_path"] = str(pawn_path)
    if pawn_bp:
        resolved["pawn_bp"] = str(pawn_bp)

    runtime_asset_pack = _vehicle_model_runtime_asset_pack(match_id, match_model, runtime)
    if runtime_asset_pack:
        resolved["runtime_asset_pack"] = runtime_asset_pack
    return resolved


def _vehicle_model_runtime_asset_pack(model_id: str, model: dict[str, Any], runtime: dict[str, Any]) -> dict[str, Any]:
    pack_id = (
        runtime.get("asset_pack")
        or runtime.get("content_pack")
        or runtime.get("pack_id")
        or model.get("asset_pack")
        or model.get("content_pack")
        or model.get("source_asset_pack")
    )
    runtime_keys = (
        "bundle_format",
        "content_mount",
        "pak_path",
        "local_pak_path",
        "source_pak",
        "host_path",
        "runtime_target_id",
        "files",
        "bundle_files",
        "sidecar_files",
    )
    runtime_pack = {key: copy.deepcopy(runtime[key]) for key in runtime_keys if key in runtime}
    if not runtime_pack:
        return {}

    pack_id = str(pack_id or f"vehicle_model_{model_id}")
    entry = {
        "id": pack_id,
        "pack_id": pack_id,
        "display_name": str(model.get("display_name") or model.get("name") or model_id),
        "runtime": runtime_pack,
    }
    editor = model.get("editor") if isinstance(model.get("editor"), dict) else {}
    if editor:
        entry["editor"] = copy.deepcopy(editor)
    elif runtime_pack.get("content_mount"):
        entry["editor"] = {"content_mount": str(runtime_pack["content_mount"])}
    return entry


def _resolve_runtime(spec: dict[str, Any], catalogs: dict[str, Any]) -> dict[str, Any]:
    raw_runtime = spec.get("runtime")
    if isinstance(raw_runtime, str):
        runtime = {"profile": raw_runtime}
    else:
        runtime = _mapping(raw_runtime, "runtime")
    profile_id = str(runtime.get("profile") or "airsim_unreal_ardupilot_docker")
    profiles = catalogs["runtime_profiles"]
    if profile_id not in profiles:
        raise SimstackError(f"unknown runtime.profile: {profile_id}")

    profile = copy.deepcopy(profiles[profile_id])
    autopilot = merge_dicts(
        _mapping(profile.get("autopilot"), f"runtime_profiles.{profile_id}.autopilot"),
        _mapping(runtime.get("autopilot"), "runtime.autopilot"),
    )
    if isinstance(spec.get("autopilot"), dict):
        autopilot = merge_dicts(autopilot, spec["autopilot"])
    elif isinstance(spec.get("autopilot"), str):
        autopilot["type"] = spec["autopilot"]

    features = merge_dicts(
        _mapping(profile.get("features"), f"runtime_profiles.{profile_id}.features"),
        _mapping(runtime.get("features"), "runtime.features"),
    )
    if isinstance(spec.get("features"), dict):
        features = merge_dicts(features, spec["features"])

    return {
        "id": profile_id,
        "autopilot": autopilot,
        "features": features,
    }


def _resolve_environment(spec: dict[str, Any], catalogs: dict[str, Any], source_root: Path) -> dict[str, Any]:
    raw = spec.get("environment") or {"id": "xfs"}
    if isinstance(raw, str):
        raw = {"id": raw}
    env_override = _mapping(raw, "environment")
    env_id = str(env_override.get("id") or env_override.get("name") or "xfs")
    base = copy.deepcopy(catalogs["environments"].get(env_id, {}))
    if not base and not any(key in env_override for key in ("image", "executable_path", "scenario_runtime")):
        raise SimstackError(f"unknown environment.id: {env_id}")

    override = {key: value for key, value in env_override.items() if key != "id"}
    environment = merge_dicts(base, override)
    level_pack_ref = environment.get("level_pack")
    if level_pack_ref:
        level_pack_path = _resolve_bundle_file(source_root, level_pack_ref, label="environment.level_pack")
        level_environment = _environment_from_level_pack(level_pack_path, str(level_pack_ref))
        environment = merge_dicts(level_environment, environment)
        if not environment.get("image"):
            raise SimstackError(
                "environment.level_pack uses a content-only MnS level pack but the current baked-image "
                f"stack generator has no runtime image binding. Add runtime_binding.local.yaml beside {level_pack_path.name} "
                "or use a legacy level pack with runtime.image."
            )
        if not environment.get("executable_path"):
            raise SimstackError(
                "environment.level_pack has a runtime image binding without executable_path. "
                "Add executable_path to runtime_binding.local.yaml for the current baked-image stack generator."
            )
    environment.setdefault("name", env_override.get("name") or env_id)

    level = environment.pop("level", None) or environment.pop("map", None)
    if level:
        level_map = level
        if isinstance(level, dict):
            runtime_artifact = _mapping(environment.get("runtime_artifact"), "environment.runtime_artifact")
            level_runtime = {
                "level_id": level.get("id") or level.get("level_id"),
                "display_name": level.get("display_name") or level.get("name"),
                "map": level.get("map") or level.get("path"),
                "pak_id": level.get("pak_id"),
                "pak_name": level.get("pak_name"),
            }
            runtime_artifact = merge_dicts(runtime_artifact, {k: v for k, v in level_runtime.items() if v})
            if runtime_artifact:
                environment["runtime_artifact"] = runtime_artifact
            level_map = level_runtime.get("map")
            if not level_map:
                raise SimstackError("environment.level must include map when provided as a mapping")
        elif not isinstance(level, str):
            raise SimstackError("environment.level must be a string or mapping when provided")

        scenario_runtime = _mapping(environment.get("scenario_runtime"), "environment.scenario_runtime")
        world = _mapping(scenario_runtime.get("world"), "environment.scenario_runtime.world")
        world.setdefault("mode", "authored")
        world["map"] = str(level_map)
        scenario_runtime["world"] = world
        scenario_runtime.setdefault("enabled", True)
        environment["scenario_runtime"] = scenario_runtime

    if isinstance(spec.get("scenario_runtime"), dict):
        environment["scenario_runtime"] = merge_dicts(_mapping(environment.get("scenario_runtime"), "environment.scenario_runtime"), spec["scenario_runtime"])
    return environment


def _normalize_vehicles(spec: dict[str, Any]) -> list[dict[str, Any]]:
    sensor_profiles = _mapping(spec.get("sensor_profiles"), "sensor_profiles")
    raw_items = _list(spec.get("vehicles"), "vehicles")
    for entity in _list(spec.get("entities"), "entities"):
        if not isinstance(entity, dict):
            raise SimstackError("each entities item must be a mapping")
        kind = str(entity.get("kind") or entity.get("type") or "").lower()
        if kind in ("vehicle", "drone", "robot", "agent"):
            raw_items.append(entity)

    if not raw_items:
        raise SimstackError("ScenarioSpec must define at least one vehicle")

    vehicles: list[dict[str, Any]] = []
    for index, item in enumerate(raw_items, start=1):
        if not isinstance(item, dict):
            raise SimstackError("each vehicles item must be a mapping")
        components = _mapping(item.get("components"), "vehicle.components")
        vehicle_component = _mapping(components.get("mns.vehicle") or components.get("vehicle"), "vehicle.components.mns.vehicle")
        stack_component = _mapping(components.get("mns.autonomy_stack") or components.get("autonomy_stack"), "vehicle.components.mns.autonomy_stack")
        sensors_component = components.get("mns.sensors") or components.get("sensors")

        vehicle_id = str(item.get("id") or item.get("name") or f"vehicle_{index}")
        name = str(item.get("name") or vehicle_component.get("name") or vehicle_id)
        runtime_name = item.get("runtime_name") or vehicle_component.get("runtime_name")
        spawn = _transform_to_spawn(item.get("start") or item.get("spawn") or item.get("transform"), index)
        spawn["frame"] = _scenario_frame(item, spec, default="airsim_ned")

        vehicle_type = (
            item.get("vehicle_type")
            or item.get("vehicle_type_id")
            or item.get("model_id")
            or vehicle_component.get("type")
            or vehicle_component.get("vehicle_type")
            or vehicle_component.get("model_id")
        )
        definition = {
            "name": name,
            **({"runtime_name": str(runtime_name)} if runtime_name else {}),
            **({"vehicle_type": str(vehicle_type)} if vehicle_type else {}),
            "spawn": spawn,
            "ros_domain_id": int(item.get("ros_domain_id", stack_component.get("ros_domain_id", index))),
        }
        sensors = item.get("sensors", sensors_component)
        cameras = item.get("cameras", components.get("mns.cameras") or components.get("cameras"))
        sensors, cameras = _apply_vehicle_sensor_profile(item, sensors, cameras, sensor_profiles)
        if sensors is not None:
            definition["sensors"] = copy.deepcopy(sensors)
        if cameras is not None:
            definition["cameras"] = copy.deepcopy(cameras)
        definition.update(_normalize_vehicle_connection(item, vehicle_id))

        vehicles.append({
            "id": vehicle_id,
            "name": name,
            "definition": definition,
        })
    return vehicles


def _normalize_vehicle_connection(item: dict[str, Any], vehicle_id: str) -> dict[str, Any]:
    connection = _mapping(item.get("connection"), f"vehicle {vehicle_id}.connection")
    normalized = {key: copy.deepcopy(value) for key, value in connection.items() if key in VEHICLE_CONNECTION_KEYS}

    for key in VEHICLE_CONNECTION_KEYS:
        if key not in item:
            continue
        value = copy.deepcopy(item[key])
        if key in normalized and str(normalized[key]) != str(value):
            raise SimstackError(
                f"vehicle {vehicle_id}: connection field '{key}' is defined both at the vehicle top level "
                "and under connection with different values"
            )
        normalized[key] = value
    return normalized


def _apply_vehicle_sensor_profile(
    item: dict[str, Any],
    sensors: Any,
    cameras: Any,
    sensor_profiles: dict[str, Any],
) -> tuple[Any, Any]:
    profile_id = item.get("sensor_profile") or item.get("sensor_profile_id")
    sensors_override = copy.deepcopy(sensors)
    cameras_override = copy.deepcopy(cameras)

    if isinstance(sensors_override, dict) and sensors_override.get("profile"):
        profile_id = sensors_override.get("profile")
        sensors_override = {key: value for key, value in sensors_override.items() if key != "profile"}
    if isinstance(cameras_override, dict) and cameras_override.get("profile"):
        profile_id = cameras_override.get("profile")
        cameras_override = {key: value for key, value in cameras_override.items() if key != "profile"}

    if not profile_id:
        return sensors_override, cameras_override

    profile = sensor_profiles.get(str(profile_id))
    if not isinstance(profile, dict):
        raise SimstackError(f"unknown sensor_profile: {profile_id}")

    profile_sensors = profile.get("sensors") if "sensors" in profile else profile
    profile_cameras = profile.get("cameras")
    return (
        _merge_optional_config_block(profile_sensors, sensors_override),
        _merge_optional_config_block(profile_cameras, cameras_override),
    )


def _merge_optional_config_block(base: Any, override: Any) -> Any:
    if override is None:
        return copy.deepcopy(base)
    if base is None:
        return copy.deepcopy(override)
    if isinstance(base, dict) and isinstance(override, dict):
        return merge_dicts(base, override)
    return copy.deepcopy(override)


def _normalize_assets(spec: dict[str, Any], catalogs: dict[str, Any]) -> dict[str, Any]:
    raw_items: list[tuple[dict[str, Any], str]] = []
    objects_raw = spec.get("objects")
    if isinstance(objects_raw, list):
        for item in _list(objects_raw, "objects"):
            raw_items.append((item, "ScenarioSpec.objects"))
    elif isinstance(objects_raw, dict):
        for item in _list(objects_raw.get("placements"), "objects.placements"):
            raw_items.append((item, "ScenarioSpec.objects"))
    elif objects_raw is not None:
        raise SimstackError("objects must be a list or a mapping with placements")

    for item in _list(spec.get("assets"), "assets"):
        raw_items.append((item, "ScenarioSpec.assets"))

    for entity in _list(spec.get("entities"), "entities"):
        if not isinstance(entity, dict):
            raise SimstackError("each entities item must be a mapping")
        kind = str(entity.get("kind") or entity.get("type") or "").lower()
        if kind in ("asset", "object", "obstacle", "static_obstacle"):
            raw_items.append((entity, "ScenarioSpec.entities"))

    placements: list[dict[str, Any]] = []
    assets: list[str] = []
    manifest_items: list[dict[str, Any]] = []
    pack_manifest_by_id: dict[str, dict[str, Any]] = {}
    spec_pack_metadata: dict[str, dict[str, Any]] = {}
    for raw_pack in _list(spec.get("asset_packs"), "asset_packs"):
        if not isinstance(raw_pack, dict):
            raise SimstackError("each asset_packs item must be a mapping")
        pack_id = raw_pack.get("id") or raw_pack.get("pack_id")
        if pack_id:
            spec_pack_metadata[str(pack_id)] = copy.deepcopy(raw_pack)

    for index, (item, source) in enumerate(raw_items, start=1):
        if not isinstance(item, dict):
            raise SimstackError("each objects/assets item must be a mapping")
        components = _mapping(item.get("components"), "asset.components")
        asset_component = _mapping(components.get("mns.asset") or components.get("asset"), "asset.components.mns.asset")
        pack_id = str(item.get("asset_pack") or asset_component.get("asset_pack") or "scenario_runtime_basic")
        asset_id = str(item.get("asset") or item.get("asset_id") or asset_component.get("asset") or item.get("id") or f"asset_{index}")
        catalog_pack = _catalog_pack(catalogs, pack_id) or spec_pack_metadata.get(pack_id, {})
        catalog_asset = _catalog_asset(catalogs, pack_id, asset_id)
        catalog_unreal = _engine_binding(catalog_asset, "unreal")
        class_path = str(
            item.get("class")
            or item.get("class_path")
            or asset_component.get("class")
            or asset_component.get("class_path")
            or catalog_unreal.get("class")
            or catalog_unreal.get("class_path")
            or catalog_asset.get("class")
            or catalog_asset.get("class_path")
            or DEFAULT_STATIC_OBSTACLE_CLASS
        )
        transform = _normalize_transform(item.get("transform") or item)
        transform["frame"] = _scenario_frame(item, spec, default="airsim_meters")
        asset_name = str(item.get("name") or item.get("id") or asset_id)
        unreal_static_mesh = (
            item.get("unreal_static_mesh")
            or asset_component.get("unreal_static_mesh")
            or catalog_unreal.get("static_mesh")
            or catalog_unreal.get("unreal_static_mesh")
            or catalog_asset.get("unreal_static_mesh")
            or catalog_asset.get("static_mesh")
        )

        placement = {
            "id": str(item.get("id") or f"asset_{index}"),
            "name": asset_name,
            "source": source,
            "asset_pack": pack_id,
            "asset": asset_id,
            "class": class_path,
            "transform": transform,
        }
        if unreal_static_mesh:
            placement["unreal_static_mesh"] = str(unreal_static_mesh)
        if item.get("actor_label"):
            placement["actor_label"] = str(item["actor_label"])

        placements.append(placement)
        assets.append(str(unreal_static_mesh or class_path))
        manifest_items.append({
            "id": placement["id"],
            "asset_pack": pack_id,
            "asset": asset_id,
            "class": class_path,
        })
        if pack_id not in pack_manifest_by_id:
            pack_manifest_by_id[pack_id] = _pack_manifest_item(pack_id, catalog_pack)

    return {
        "placements": placements,
        "entries": [],
        "assets": sorted(set(assets)),
        "asset_packs": list(pack_manifest_by_id.values()),
        "manifest": manifest_items,
    }


def _normalize_random_spawns(spec: dict[str, Any], catalogs: dict[str, Any]) -> list[dict[str, Any]]:
    raw = spec.get("random_spawns")
    if raw is None:
        raw_items: list[Any] = []
    elif isinstance(raw, list):
        raw_items = _list(raw, "random_spawns")
    elif isinstance(raw, dict):
        raw_items = _list(raw.get("volumes") or raw.get("entries") or raw.get("random_spawns"), "random_spawns")
    else:
        raise SimstackError("random_spawns must be a list or a mapping with volumes")

    for entity in _list(spec.get("entities"), "entities"):
        if not isinstance(entity, dict):
            raise SimstackError("each entities item must be a mapping")
        kind = str(entity.get("kind") or entity.get("type") or "").lower()
        if kind in ("random_spawn", "random_spawn_volume", "spawn_volume", "asset_spawn_volume"):
            raw_items.append(entity)

    spec_pack_metadata: dict[str, dict[str, Any]] = {}
    for raw_pack in _list(spec.get("asset_packs"), "asset_packs"):
        if not isinstance(raw_pack, dict):
            raise SimstackError("each asset_packs item must be a mapping")
        pack_id = raw_pack.get("id") or raw_pack.get("pack_id")
        if pack_id:
            spec_pack_metadata[str(pack_id)] = copy.deepcopy(raw_pack)

    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(raw_items, start=1):
        if not isinstance(item, dict):
            raise SimstackError("each random_spawns item must be a mapping")

        components = _mapping(item.get("components"), "random_spawns.components")
        asset_component = _mapping(components.get("mns.asset") or components.get("asset"), "random_spawns.components.mns.asset")
        pack_id = str(item.get("asset_pack") or asset_component.get("asset_pack") or "scenario_runtime_basic")
        asset_id = str(item.get("asset") or item.get("asset_id") or asset_component.get("asset") or f"asset_{index}")
        catalog_pack = _catalog_pack(catalogs, pack_id) or spec_pack_metadata.get(pack_id, {})
        catalog_asset = _catalog_asset(catalogs, pack_id, asset_id)
        catalog_unreal = _engine_binding(catalog_asset, "unreal")
        class_path = str(
            item.get("class")
            or item.get("class_path")
            or asset_component.get("class")
            or asset_component.get("class_path")
            or catalog_unreal.get("class")
            or catalog_unreal.get("class_path")
            or catalog_asset.get("class")
            or catalog_asset.get("class_path")
            or DEFAULT_STATIC_OBSTACLE_CLASS
        )
        unreal_static_mesh = (
            item.get("unreal_static_mesh")
            or asset_component.get("unreal_static_mesh")
            or catalog_unreal.get("static_mesh")
            or catalog_unreal.get("unreal_static_mesh")
            or catalog_asset.get("unreal_static_mesh")
            or catalog_asset.get("static_mesh")
        )

        bounds_raw = _mapping(item.get("bounds"), "random_spawns.bounds")
        transform = _normalize_transform(bounds_raw.get("transform") or item.get("transform") or item)
        center_raw = bounds_raw.get("center") or bounds_raw.get("position") or bounds_raw.get("location")
        center_map = _mapping(center_raw, "random_spawns.bounds.center") if center_raw is not None else transform["position"]
        rotation_raw = bounds_raw.get("rotation") or item.get("rotation")
        rotation_map = _mapping(rotation_raw, "random_spawns.bounds.rotation") if rotation_raw is not None else transform["rotation"]
        size_raw = bounds_raw.get("size") or item.get("size") or item.get("bounds_size")
        size_map = _mapping(size_raw, "random_spawns.bounds.size") if size_raw is not None else {"x": 4.0, "y": 4.0, "z": 2.0}
        spawn_scale_raw = item.get("spawn_scale") or item.get("object_scale")
        spawn_scale = _mapping(spawn_scale_raw, "random_spawns.spawn_scale") if spawn_scale_raw is not None else {"x": 1.0, "y": 1.0, "z": 1.0}

        count = int(item.get("count", item.get("clutter", item.get("clutter_count", 1))))
        if count < 0:
            raise SimstackError(f"random_spawns item {index} count must be non-negative")

        spawn_id = str(item.get("id") or item.get("name") or f"random_spawn_{index}")
        entry = {
            "id": spawn_id,
            "name": str(item.get("name") or spawn_id),
            "source": str(item.get("source") or "ScenarioSpec.random_spawns"),
            "asset_pack": pack_id,
            "asset": asset_id,
            "class": class_path,
            "count": count,
            "random_yaw": _bool_value(item.get("random_yaw"), True),
            "min_clearance_m": float(item.get("min_clearance_m", 0.5)),
            "spawn_inset_m": float(item.get("spawn_inset_m", 0.25)),
            "spawn_scale": {
                "x": float(spawn_scale.get("x", 1.0)),
                "y": float(spawn_scale.get("y", 1.0)),
                "z": float(spawn_scale.get("z", 1.0)),
            },
            "bounds": {
                "frame": str(bounds_raw.get("frame") or item.get("frame") or _scenario_frame(item, spec, default="ros2_flu")),
                "center": {
                    "x": float(center_map.get("x", 0.0)),
                    "y": float(center_map.get("y", 0.0)),
                    "z": float(center_map.get("z", 0.0)),
                },
                "yaw": float(bounds_raw.get("yaw", rotation_map.get("yaw", 0.0))),
                "size": {
                    "x": max(float(size_map.get("x", 4.0)), 0.001),
                    "y": max(float(size_map.get("y", 4.0)), 0.001),
                    "z": max(float(size_map.get("z", 2.0)), 0.001),
                },
            },
            "_asset_pack_manifest": _pack_manifest_item(pack_id, catalog_pack),
        }
        if unreal_static_mesh:
            entry["unreal_static_mesh"] = str(unreal_static_mesh)
        normalized.append(entry)
    return normalized


def _resolve_object_clutter(spec: dict[str, Any], asset_config: dict[str, Any]) -> ObjectClutter:
    raw = _mapping(spec.get("object_clutter"), "object_clutter")
    explicit_entries = _list(raw.get("entries") or raw.get("blocking_entries"), "object_clutter.entries")
    entries = copy.deepcopy(explicit_entries) + asset_config["entries"]
    enabled_default = bool(entries or asset_config["placements"])
    count = raw.get("count")
    return ObjectClutter(
        enabled=bool(raw.get("enabled", enabled_default)),
        backend=str(raw.get("backend", "scenario_spec")),
        seed=int(raw.get("seed", spec.get("seed", 42))),
        density=str(raw.get("density", "medium" if enabled_default else "none")),
        count=int(count) if count is not None else None,
        placement=str(raw.get("placement", "scenario_spec")),
        blueprint=str(raw.get("blueprint", "/Game/Xfs/Blueprints/BP_ContainerSpawner.BP_ContainerSpawner_C")),
        data_table=str(raw.get("data_table", "/Game/Xfs/Data/DT_ContainerPositions")),
        assets=_list(raw.get("assets"), "object_clutter.assets") + asset_config["assets"],
        entries=entries,
        placements=copy.deepcopy(_list(raw.get("placements"), "object_clutter.placements")) + asset_config["placements"],
        asset_packs=copy.deepcopy(_list(raw.get("asset_packs"), "object_clutter.asset_packs")) + asset_config["asset_packs"],
        config_source=None,
    )


def _resolve_scenario_runtime(spec: dict[str, Any], environment: dict[str, Any]) -> ScenarioRuntimeConfig:
    merged: dict[str, Any] = {}
    for owner, raw in (
        ("environment", environment.get("scenario_runtime")),
        ("ScenarioSpec", spec.get("scenario_runtime")),
    ):
        if raw is None:
            continue
        if not isinstance(raw, dict):
            raise SimstackError(f"{owner}.scenario_runtime must be a mapping when provided")
        merged = merge_dicts(merged, raw)

    world = _mapping(merged.get("world"), "scenario_runtime.world")
    pcg = _mapping(merged.get("pcg"), "scenario_runtime.pcg")
    procedural = _mapping(merged.get("procedural"), "scenario_runtime.procedural")
    start_goal = _mapping(merged.get("start_goal"), "scenario_runtime.start_goal")
    dynamic_obstacles = _mapping(merged.get("dynamic_obstacles"), "scenario_runtime.dynamic_obstacles")

    raw_map = world.get("map", merged.get("map"))
    raw_pcg_graph = pcg.get("graph", merged.get("pcg_graph"))
    return ScenarioRuntimeConfig(
        enabled=bool(merged.get("enabled", True)),
        world_mode=str(world.get("mode", merged.get("mode", "procedural"))),
        map=str(raw_map) if raw_map else None,
        pcg_graph=str(raw_pcg_graph) if raw_pcg_graph else None,
        procedural=procedural,
        start_goal=start_goal,
        dynamic_obstacles=dynamic_obstacles,
    )


def _catalog_pack(catalogs: dict[str, Any], pack_id: str) -> dict[str, Any]:
    pack = catalogs["asset_packs"].get(pack_id)
    return copy.deepcopy(pack) if isinstance(pack, dict) else {}


def _catalog_asset(catalogs: dict[str, Any], pack_id: str, asset_id: str) -> dict[str, Any]:
    pack = catalogs["asset_packs"].get(pack_id)
    if not isinstance(pack, dict):
        return {}
    assets = pack.get("assets") or {}
    if not isinstance(assets, dict):
        return {}
    asset = assets.get(asset_id)
    return copy.deepcopy(asset) if isinstance(asset, dict) else {}


def _engine_binding(asset: dict[str, Any], engine: str) -> dict[str, Any]:
    runtime = asset.get("runtime")
    if isinstance(runtime, dict) and isinstance(runtime.get(engine), dict):
        return copy.deepcopy(runtime[engine])

    bindings = asset.get("bindings")
    if isinstance(bindings, dict):
        binding = bindings.get(engine)
        if isinstance(binding, dict):
            return copy.deepcopy(binding)
        if isinstance(binding, str):
            return {"class": binding}
    return {}


def _pack_manifest_item(pack_id: str, pack: dict[str, Any]) -> dict[str, Any]:
    item = {
        "id": pack_id,
        "display_name": str(pack.get("display_name") or pack.get("name") or pack_id),
    }
    for key in ("version", "pak_id", "pak_name", "source_hint"):
        if pack.get(key):
            item[key] = str(pack[key])
    for key in ("runtime", "editor"):
        value = pack.get(key)
        if isinstance(value, dict):
            item[key] = copy.deepcopy(value)
    return item



def _transform_to_spawn(raw: Any, index: int) -> dict[str, float]:
    transform = _normalize_transform(raw or {})
    position = transform["position"]
    rotation = transform["rotation"]
    return {
        "x": float(position.get("x", (index - 1) * 3.0)),
        "y": float(position.get("y", 0.0)),
        "z": float(position.get("z", 0.0)),
        "yaw": float(rotation.get("yaw", 0.0)),
    }


def _normalize_spawn(data: dict[str, Any], index: int) -> dict[str, Any]:
    spawn = _mapping(data.get("spawn"), "vehicle.spawn")
    return {
        "x": float(spawn.get("x", (index - 1) * 3.0)),
        "y": float(spawn.get("y", 0.0)),
        "z": float(spawn.get("z", 0.0)),
        "yaw": float(spawn.get("yaw", 0.0)),
        "frame": str(spawn.get("frame", data.get("spawn_frame", "airsim_ned"))),
    }


def _scenario_frame(item: dict[str, Any], spec: dict[str, Any], default: str) -> str:
    for raw in (
        item.get("frame"),
        _mapping(item.get("transform"), "transform").get("frame") if isinstance(item.get("transform"), dict) else None,
        _mapping(item.get("start"), "start").get("frame") if isinstance(item.get("start"), dict) else None,
        _mapping(item.get("spawn"), "spawn").get("frame") if isinstance(item.get("spawn"), dict) else None,
    ):
        if raw:
            return str(raw)

    components = item.get("components")
    if isinstance(components, dict):
        unreal_authoring = components.get("mns.unreal_authoring") or components.get("unreal_authoring")
        if isinstance(unreal_authoring, dict) and unreal_authoring.get("spawn_frame"):
            return str(unreal_authoring["spawn_frame"])

    coordinate_frame = spec.get("coordinate_frame")
    if isinstance(coordinate_frame, dict) and coordinate_frame.get("convention"):
        return str(coordinate_frame["convention"])
    return default


def _normalize_transform(raw: Any) -> dict[str, Any]:
    data = _mapping(raw, "transform")
    position_raw = data.get("position") or data.get("location") or data.get("airsim_meters") or data
    rotation_raw = data.get("rotation") or data.get("rotation_degrees") or data
    scale_is_nested = isinstance(data.get("scale"), dict)
    scale_raw = data.get("scale") if scale_is_nested else data
    position = _mapping(position_raw, "transform.position")
    rotation = _mapping(rotation_raw, "transform.rotation")
    scale = _mapping(scale_raw, "transform.scale")
    scale_x_key = "x" if scale_is_nested else "scale_x"
    scale_y_key = "y" if scale_is_nested else "scale_y"
    scale_z_key = "z" if scale_is_nested else "scale_z"
    return {
        "position": {
            "x": float(position.get("x", 0.0)),
            "y": float(position.get("y", 0.0)),
            "z": float(position.get("z", 0.0)),
        },
        "rotation": {
            "pitch": float(rotation.get("pitch", 0.0)),
            "roll": float(rotation.get("roll", 0.0)),
            "yaw": float(rotation.get("yaw", 0.0)),
        },
        "scale": {
            "x": float(scale.get(scale_x_key, scale.get("scale_x", 1.0))),
            "y": float(scale.get(scale_y_key, scale.get("scale_y", 1.0))),
            "z": float(scale.get(scale_z_key, scale.get("scale_z", 1.0))),
        },
    }


def _scenariospec_source_files(spec_path: Path) -> list[Path]:
    source_root = spec_path.parent
    files = [spec_path]
    for path in source_root.rglob("*"):
        if not path.is_file():
            continue
        if "ScenarioBundle" in path.relative_to(source_root).parts:
            continue
        if path.suffix.lower() in SCENARIOSPEC_INCLUDE_GLOB_SUFFIXES:
            files.append(path)

    bundle_root = source_root / "ScenarioBundle"
    if bundle_root.is_dir():
        files.extend(sorted(path for path in bundle_root.rglob("*") if path.is_file()))
    return sorted(dict.fromkeys(path.resolve() for path in files))


def _bool_value(raw: Any, default: bool = False) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        value = raw.strip().lower()
        if value in ("1", "true", "yes", "on"):
            return True
        if value in ("0", "false", "no", "off"):
            return False
    return bool(raw)


def _mapping(raw: Any, label: str) -> dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise SimstackError(f"{label} must be a mapping when provided")
    return copy.deepcopy(raw)


def _list(raw: Any, label: str) -> list[Any]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise SimstackError(f"{label} must be a list when provided")
    return list(raw)


def _resolve_ref(base_dir: Path, raw_path: Any) -> Path:
    path = Path(str(raw_path)).expanduser()
    if not path.is_absolute():
        path = (base_dir / path).resolve()
    return path
