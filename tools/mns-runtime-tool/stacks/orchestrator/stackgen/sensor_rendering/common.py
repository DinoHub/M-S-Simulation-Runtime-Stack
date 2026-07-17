"""Shared helpers for sensor and camera rendering."""
from __future__ import annotations

from typing import Any, Mapping

from .aliases import SENSOR_FIELD_ALIASES
from .errors import SensorConfigError


def sensor_name(raw: Any, default: str) -> str:
    if isinstance(raw, Mapping):
        return str(raw.get("name") or raw.get("id") or default)
    return default


def normalize_named_collection(raw: Any, field: str) -> list[Mapping[str, Any]]:
    if raw is None:
        return []
    if isinstance(raw, Mapping):
        items = []
        for name, value in raw.items():
            if value is False:
                continue
            if value is True:
                items.append({"name": name, "enabled": True})
            elif isinstance(value, Mapping):
                merged = dict(value)
                merged.setdefault("name", name)
                items.append(merged)
            else:
                raise SensorConfigError(f"{field}.{name} must be a mapping or boolean")
        return items
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, Mapping):
                raise SensorConfigError(f"each {field} item must be a mapping")
        return raw
    raise SensorConfigError(f"{field} must be a list or mapping")


def apply_xyz(config: dict[str, Any], value: Any) -> None:
    if not isinstance(value, Mapping):
        raise SensorConfigError("position must be a mapping with x/y/z")
    for key in ("x", "y", "z"):
        if key in value:
            config[SENSOR_FIELD_ALIASES[key]] = value[key]


def apply_rotation(config: dict[str, Any], value: Any) -> None:
    if not isinstance(value, Mapping):
        raise SensorConfigError("rotation must be a mapping with pitch/roll/yaw")
    for key in ("pitch", "roll", "yaw"):
        if key in value:
            config[SENSOR_FIELD_ALIASES[key]] = value[key]


def to_airsim_key(key: str, aliases: Mapping[str, str]) -> str:
    if key in aliases:
        return aliases[key]
    if "_" not in key:
        return key
    return "".join(part[:1].upper() + part[1:] for part in key.split("_") if part)
