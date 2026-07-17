"""Render AirSim settings.json from a resolved scenario."""
from __future__ import annotations

from typing import Any

from ..autopilots import get_autopilot_profile
from ..models import ResolvedScenario


def _spawn_for_airsim(spawn: dict[str, Any]) -> dict[str, float]:
    x = float(spawn.get("x", 0.0))
    y = float(spawn.get("y", 0.0))
    z = float(spawn.get("z", 0.0))
    yaw = float(spawn.get("yaw", 0.0))
    frame = str(spawn.get("frame", "airsim_ned")).lower()

    if frame.startswith("ros2_flu") or frame in {"ros2", "ros2_map"}:
        return {
            "x": x,
            "y": -y,
            "z": -z,
            "yaw": yaw,
        }

    return {"x": x, "y": y, "z": z, "yaw": yaw}


def airsim_settings(resolved: ResolvedScenario) -> dict[str, Any]:
    origin = {
        "Latitude": float(resolved.origin.get("latitude", 42.764938)),
        "Longitude": float(resolved.origin.get("longitude", -115.579201)),
        "Altitude": float(resolved.origin.get("altitude", 1183)),
    }

    settings: dict[str, Any] = {
        "SettingsVersion": 2.0,
        "SimMode": "Multirotor",
        "ClockType": "ScalableClock",
        "ViewMode": "FlyWithMe",
        "LocalHostIp": "0.0.0.0",
        "ApiServerPort": 41451,
        "ApiServerEndpoint": "0.0.0.0:41451",
        "RpcEnabled": True,
        "InitialInstanceSegmentation": False,
        "LogMessagesVisible": False,
        "EngineSound": False,
        "OriginGeopoint": origin,
        "Vehicles": {},
    }
    if resolved.time_of_day:
        settings["TimeOfDay"] = {
            "Enabled": bool(resolved.time_of_day.get("enabled", False)),
            "StartDateTime": str(resolved.time_of_day.get("start", "")),
            "CelestialClockSpeed": float(resolved.time_of_day.get("celestial_clock_speed", 1)),
            "StartDateTimeDst": bool(resolved.time_of_day.get("dst", False)),
            "UpdateIntervalSecs": int(resolved.time_of_day.get("update_interval_secs", 60)),
            "MoveSun": bool(resolved.time_of_day.get("move_sun", True)),
        }

    profile = get_autopilot_profile(resolved.autopilot_type)
    for vehicle in resolved.vehicles:
        airsim_spawn = _spawn_for_airsim(vehicle.spawn)
        vehicle_settings = profile.airsim_vehicle_settings(resolved, vehicle)
        vehicle_settings.update({
            "EnableCollisions": True,
            "AllowAPIAlways": True,
            "X": airsim_spawn["x"],
            "Y": airsim_spawn["y"],
            "Z": airsim_spawn["z"],
            "Yaw": airsim_spawn["yaw"],
            "Sensors": vehicle.sensors,
            "Cameras": vehicle.cameras,
        })
        if vehicle.pawn_path:
            vehicle_settings["PawnPath"] = vehicle.pawn_path
            if vehicle.pawn_bp:
                settings.setdefault("PawnPaths", {})[vehicle.pawn_path] = {"PawnBP": vehicle.pawn_bp}
        settings["Vehicles"][vehicle.runtime_name] = vehicle_settings
    return settings
