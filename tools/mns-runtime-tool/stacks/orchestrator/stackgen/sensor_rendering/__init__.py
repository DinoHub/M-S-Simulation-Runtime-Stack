"""Focused renderers behind the public stackgen.sensors API."""
from __future__ import annotations

from .cameras import (
    default_camera,
    default_capture_setting,
    render_camera_block,
    render_camera_config,
    render_capture_setting,
)
from .common import apply_rotation, apply_xyz, normalize_named_collection, sensor_name, to_airsim_key
from .definitions import SENSOR_DEFINITIONS, SENSOR_DEFINITIONS_BY_KEY, SensorDefinition, default_sensor_block
from .errors import SensorConfigError
from .models import RenderedSensors
from .sensors import apply_single_sensor_config, merge_sensor_config, render_sensor_block

__all__ = [
    "RenderedSensors",
    "SENSOR_DEFINITIONS",
    "SENSOR_DEFINITIONS_BY_KEY",
    "SensorDefinition",
    "SensorConfigError",
    "apply_single_sensor_config",
    "apply_rotation",
    "apply_xyz",
    "default_camera",
    "default_capture_setting",
    "default_sensor_block",
    "merge_sensor_config",
    "normalize_named_collection",
    "render_camera_block",
    "render_camera_config",
    "render_capture_setting",
    "render_sensor_block",
    "sensor_name",
    "to_airsim_key",
]
