"""Public sensor rendering API for scenario vehicle declarations.

The implementation lives under ``stackgen.sensor_rendering`` so sensor and
camera behavior can grow independently while this import path stays stable.
"""
from __future__ import annotations

from typing import Any, Mapping

from .sensor_rendering import (
    RenderedSensors,
    SENSOR_DEFINITIONS,
    SENSOR_DEFINITIONS_BY_KEY,
    SensorDefinition,
    SensorConfigError,
    apply_single_sensor_config,
    apply_rotation,
    apply_xyz,
    default_camera,
    default_capture_setting,
    default_sensor_block,
    merge_sensor_config,
    normalize_named_collection,
    render_camera_block,
    render_camera_config,
    render_capture_setting,
    render_sensor_block,
    sensor_name,
    to_airsim_key,
)
from .sensor_rendering.aliases import (
    CAMERA_CAPTURE_KEYS,
    CAMERA_FIELD_ALIASES,
    CAPTURE_FIELD_ALIASES,
    SENSOR_FIELD_ALIASES,
)
from .sensor_rendering.defaults import (
    DEFAULT_BAROMETER,
    DEFAULT_DISTANCE,
    DEFAULT_ECHO,
    DEFAULT_GPS,
    DEFAULT_GPU_LIDAR,
    DEFAULT_IMU,
    DEFAULT_LIDAR,
    DEFAULT_MAGNETOMETER,
    DEFAULT_MARLOC_UWB,
    DEFAULT_SENSOR_BLOCK,
    DEFAULT_SENSOR_TEMPLATE,
    DEFAULT_WIFI,
    STANDARD_SENSOR_DEFAULTS,
)


def render_vehicle_sensors(vehicle_data: Mapping[str, Any]) -> RenderedSensors:
    sensors = render_sensor_block(vehicle_data.get("sensors"))
    cameras = render_camera_block(vehicle_data.get("cameras"))
    return RenderedSensors(sensors=sensors, cameras=cameras)


__all__ = [
    "CAMERA_CAPTURE_KEYS",
    "CAMERA_FIELD_ALIASES",
    "CAPTURE_FIELD_ALIASES",
    "DEFAULT_BAROMETER",
    "DEFAULT_DISTANCE",
    "DEFAULT_ECHO",
    "DEFAULT_GPS",
    "DEFAULT_GPU_LIDAR",
    "DEFAULT_IMU",
    "DEFAULT_LIDAR",
    "DEFAULT_MAGNETOMETER",
    "DEFAULT_MARLOC_UWB",
    "DEFAULT_SENSOR_BLOCK",
    "DEFAULT_SENSOR_TEMPLATE",
    "DEFAULT_WIFI",
    "RenderedSensors",
    "SENSOR_DEFINITIONS",
    "SENSOR_DEFINITIONS_BY_KEY",
    "SENSOR_FIELD_ALIASES",
    "STANDARD_SENSOR_DEFAULTS",
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
    "render_vehicle_sensors",
    "sensor_name",
    "to_airsim_key",
]
