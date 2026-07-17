"""Registry of first-class scenario sensor definitions."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping

from .defaults import (
    DEFAULT_BAROMETER,
    DEFAULT_DISTANCE,
    DEFAULT_ECHO,
    DEFAULT_GPS,
    DEFAULT_GPU_LIDAR,
    DEFAULT_IMU,
    DEFAULT_LIDAR,
    DEFAULT_MAGNETOMETER,
    DEFAULT_MARLOC_UWB,
    DEFAULT_SENSOR_TEMPLATE,
    DEFAULT_WIFI,
)


@dataclass(frozen=True)
class SensorDefinition:
    yaml_key: str
    default_name: str
    defaults: Mapping[str, Any]
    collection_key: str | None = None
    include_by_default: bool = False


SENSOR_DEFINITIONS = (
    SensorDefinition("barometer", "Barometer", DEFAULT_BAROMETER, include_by_default=True),
    SensorDefinition("gps", "Gps", DEFAULT_GPS, include_by_default=True),
    SensorDefinition("imu", "Imu", DEFAULT_IMU),
    SensorDefinition("magnetometer", "Magnetometer", DEFAULT_MAGNETOMETER),
    SensorDefinition("distance", "DistanceSensor1", DEFAULT_DISTANCE, collection_key="distance_sensors"),
    SensorDefinition("lidar", "LidarSensor1", DEFAULT_LIDAR, collection_key="lidars", include_by_default=True),
    SensorDefinition("echo", "EchoSensor1", DEFAULT_ECHO, collection_key="echos"),
    SensorDefinition("gpu_lidar", "GPULidarSensor1", DEFAULT_GPU_LIDAR, collection_key="gpu_lidars"),
    SensorDefinition("sensor_template", "SensorTemplate1", DEFAULT_SENSOR_TEMPLATE, collection_key="sensor_templates"),
    SensorDefinition("marloc_uwb", "MarlocUwb1", DEFAULT_MARLOC_UWB, collection_key="marloc_uwbs"),
    SensorDefinition("wifi", "Wifi1", DEFAULT_WIFI, collection_key="wifis"),
)

SENSOR_DEFINITIONS_BY_KEY = {definition.yaml_key: definition for definition in SENSOR_DEFINITIONS}


def default_sensor_block() -> dict[str, Any]:
    return {
        definition.default_name: deepcopy(dict(definition.defaults))
        for definition in SENSOR_DEFINITIONS
        if definition.include_by_default
    }
