"""Render scenario camera declarations into AirSim camera blocks."""
from __future__ import annotations

from typing import Any, Mapping

from .aliases import CAMERA_CAPTURE_KEYS, CAMERA_FIELD_ALIASES, CAPTURE_FIELD_ALIASES
from .common import apply_rotation, apply_xyz, to_airsim_key
from .errors import SensorConfigError


def render_camera_block(raw: Any) -> dict[str, Any]:
    if raw is None or raw is False:
        return {}
    if raw is True:
        return {"FrontCamera": default_camera()}
    if not isinstance(raw, (Mapping, list)):
        raise SensorConfigError("cameras must be a mapping, list, true, false, or omitted")

    cameras: dict[str, Any] = {}
    if isinstance(raw, Mapping):
        if raw.get("enabled") is False:
            return {}
        raw_air = raw.get("raw") or raw.get("airsim")
        if raw_air is not None:
            if not isinstance(raw_air, Mapping):
                raise SensorConfigError("cameras.raw must be a mapping of AirSim camera names")
            for name, config in raw_air.items():
                if not isinstance(config, Mapping):
                    raise SensorConfigError(f"cameras.raw.{name} must be a mapping")
                cameras[str(name)] = dict(config)
        for name, config in raw.items():
            if name in ("enabled", "raw", "airsim"):
                continue
            if config is False:
                continue
            rendered = render_camera_config(config, str(name))
            if rendered:
                cameras[str(name)] = rendered
        return cameras

    for item in raw:
        if not isinstance(item, Mapping):
            raise SensorConfigError("each cameras item must be a mapping")
        name = str(item.get("name") or item.get("id") or f"Camera{len(cameras) + 1}")
        rendered = render_camera_config(item, name)
        if rendered:
            cameras[name] = rendered
    return cameras


def render_camera_config(raw: Any, name: str) -> dict[str, Any]:
    if raw is True:
        return default_camera()
    if not isinstance(raw, Mapping):
        raise SensorConfigError(f"camera {name} must be a mapping or true")
    if raw.get("enabled") is False:
        return {}

    config: dict[str, Any] = {
        "X": 0.25,
        "Y": 0,
        "Z": -0.5,
        "Pitch": 0,
        "Roll": 0,
        "Yaw": 0,
    }
    for key, value in raw.items():
        if key in ("name", "id", "enabled", "capture_settings"):
            continue
        if key == "position":
            apply_xyz(config, value)
            continue
        if key == "rotation":
            apply_rotation(config, value)
            continue
        if key == "fisheye_shm_publish_enabled":
            # AirSim's fisheye component reads this at camera scope, while the
            # bridge also uses the capture-scoped copy for SHM camera discovery.
            config["FisheyeShmPublishEnabled"] = bool(value)
        if key in CAMERA_CAPTURE_KEYS:
            continue
        config[to_airsim_key(str(key), CAMERA_FIELD_ALIASES)] = value

    capture_settings = raw.get("capture_settings")
    if capture_settings is None:
        config["CaptureSettings"] = [default_capture_setting(raw)]
    elif isinstance(capture_settings, Mapping):
        config["CaptureSettings"] = [render_capture_setting(capture_settings)]
    elif isinstance(capture_settings, list):
        config["CaptureSettings"] = [render_capture_setting(item) for item in capture_settings]
    else:
        raise SensorConfigError(f"camera {name}.capture_settings must be a mapping or list")

    if _camera_has_fisheye_capture(config):
        config.setdefault("FisheyeShmPublishEnabled", _fisheye_shm_enabled(config))

    return config


def _camera_has_fisheye_capture(config: Mapping[str, Any]) -> bool:
    for capture in config.get("CaptureSettings", []):
        if isinstance(capture, Mapping) and any(str(key).startswith("Fisheye") for key in capture):
            return True
    return False


def _fisheye_shm_enabled(config: Mapping[str, Any]) -> bool:
    for capture in config.get("CaptureSettings", []):
        if isinstance(capture, Mapping) and "FisheyeShmPublishEnabled" in capture:
            return bool(capture["FisheyeShmPublishEnabled"])
    return True


def default_camera() -> dict[str, Any]:
    return {
        "CaptureSettings": [default_capture_setting({})],
        "X": 0.25,
        "Y": 0,
        "Z": -0.5,
        "Pitch": 0,
        "Roll": 0,
        "Yaw": 0,
    }


def default_capture_setting(raw: Mapping[str, Any]) -> dict[str, Any]:
    capture = {
        "ImageType": int(raw.get("image_type", 0)),
        "Width": int(raw.get("width", 1280)),
        "Height": int(raw.get("height", 720)),
        "FovDegrees": float(raw.get("fov_degrees", raw.get("fov", 90))),
    }
    for key, value in raw.items():
        if key in CAMERA_CAPTURE_KEYS and key not in ("image_type", "width", "height", "fov_degrees", "fov"):
            airsim_key = to_airsim_key(str(key), CAPTURE_FIELD_ALIASES)
            capture[airsim_key] = normalize_capture_value(airsim_key, value)
    return capture


def render_capture_setting(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise SensorConfigError("capture settings entries must be mappings")
    capture: dict[str, Any] = {}
    for key, value in raw.items():
        airsim_key = to_airsim_key(str(key), CAPTURE_FIELD_ALIASES)
        capture[airsim_key] = normalize_capture_value(airsim_key, value)
    return capture


def normalize_capture_value(airsim_key: str, value: Any) -> Any:
    if airsim_key != "FisheyeSensorMode":
        return value

    if isinstance(value, bool):
        raise SensorConfigError("fisheye_sensor_mode must be realistic or ground-truth")
    if isinstance(value, (int, float)):
        if value == 0:
            return "realistic"
        if value == 1:
            return "ground-truth"
        raise SensorConfigError("fisheye_sensor_mode numeric values must be 0 or 1")
    if isinstance(value, str):
        normalized = value.strip().lower().replace("_", "-")
        if normalized in ("0", "realistic"):
            return "realistic"
        if normalized in ("1", "ground-truth"):
            return "ground-truth"
        raise SensorConfigError("fisheye_sensor_mode must be realistic or ground-truth")

    raise SensorConfigError("fisheye_sensor_mode must be realistic or ground-truth")
