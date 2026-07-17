"""Render scenario sensor declarations into AirSim Sensors blocks."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from .aliases import SENSOR_FIELD_ALIASES
from .common import apply_rotation, apply_xyz, normalize_named_collection, sensor_name, to_airsim_key
from .definitions import SENSOR_DEFINITIONS, SensorDefinition, default_sensor_block
from .errors import SensorConfigError


def render_sensor_block(raw: Any) -> dict[str, Any]:
    if raw is None:
        return default_sensor_block()
    if raw is False:
        return {}
    if raw is True:
        return default_sensor_block()
    if not isinstance(raw, Mapping):
        raise SensorConfigError("sensors must be a mapping, true, false, or omitted")

    include_defaults = bool(raw.get("defaults", True))
    sensors = default_sensor_block() if include_defaults else {}

    for definition in SENSOR_DEFINITIONS:
        if definition.yaml_key in raw:
            apply_single_sensor_config(sensors, definition, raw[definition.yaml_key])
        if definition.collection_key and definition.collection_key in raw:
            for item in normalize_named_collection(raw.get(definition.collection_key), definition.collection_key):
                apply_single_sensor_config(sensors, definition, item)

    raw_air = raw.get("raw") or raw.get("airsim")
    if raw_air is not None:
        if not isinstance(raw_air, Mapping):
            raise SensorConfigError("sensors.raw must be a mapping of AirSim sensor names")
        for name, config in raw_air.items():
            if not isinstance(config, Mapping):
                raise SensorConfigError(f"sensors.raw.{name} must be a mapping")
            sensors[str(name)] = dict(config)

    return sensors


def apply_single_sensor_config(sensors: dict[str, Any], definition: SensorDefinition, raw: Any) -> None:
    if raw is None:
        return
    name = sensor_name(raw, definition.default_name)
    if raw is False and name not in sensors:
        return
    sensors[name] = merge_sensor_config(sensors.get(name, definition.defaults), raw)


def merge_sensor_config(base: Mapping[str, Any], raw: Any) -> dict[str, Any]:
    config = deepcopy(dict(base))
    if raw is True:
        config["Enabled"] = True
        return config
    if raw is False:
        config["Enabled"] = False
        return config
    if raw is None:
        return config
    if not isinstance(raw, Mapping):
        raise SensorConfigError("sensor entries must be mappings or booleans")

    for key, value in raw.items():
        if key == "name":
            continue
        if key == "position":
            apply_xyz(config, value)
            continue
        if key == "rotation":
            apply_rotation(config, value)
            continue
        config[to_airsim_key(str(key), SENSOR_FIELD_ALIASES)] = value
    return config
