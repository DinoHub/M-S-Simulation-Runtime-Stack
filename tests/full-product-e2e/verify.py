#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any

import yaml

EXPECTED_BLOCKS_IMAGE = "dhdevspace/auto_mns:blocks-v0.2.0-review.2"
EXPECTED_CLASS = "/Script/ScenarioRuntime.BlockingBoxActor"
EXPECTED_STARTS = {
    "Copter1": {"x": 2.0, "y": 3.0, "z": 1.0, "yaw": 15.0, "domain": 1},
    "Copter2": {"x": 8.0, "y": -4.0, "z": 1.5, "yaw": -30.0, "domain": 2},
}


class VerificationError(RuntimeError):
    pass


def check(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def close(actual: Any, expected: float, label: str, tolerance: float = 0.01) -> None:
    check(math.isclose(float(actual), expected, abs_tol=tolerance), f"{label}: expected {expected}, got {actual}")


def load_yaml(path: Path) -> dict[str, Any]:
    check(path.is_file(), f"missing YAML: {path}")
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    check(isinstance(value, dict), f"expected mapping in {path}")
    return value


def load_json(path: Path) -> dict[str, Any]:
    check(path.is_file(), f"missing JSON: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    check(isinstance(value, dict), f"expected object in {path}")
    return value


def verify_sensor_profile(root: Path) -> None:
    document = load_yaml(root / "SensorProfiles.yaml")
    profiles = document.get("sensor_profiles", document)
    profile = profiles.get("e2e_multirotor") if isinstance(profiles, dict) else None
    check(isinstance(profile, dict), "e2e_multirotor sensor profile was not preserved")
    sensors = profile.get("sensors", {})
    lidar = sensors.get("lidar", {}) if isinstance(sensors, dict) else {}
    check(lidar.get("enabled") is True, "authored lidar is not enabled")
    check(lidar.get("channels") == 24, "authored lidar channel count changed")
    check(lidar.get("points_per_second") == 120000, "authored lidar point rate changed")
    close(lidar.get("range"), 55, "authored lidar range")
    cameras = profile.get("cameras", {})
    camera = cameras.get("front_rgb", {}) if isinstance(cameras, dict) else {}
    check(camera.get("enabled") is True, "front_rgb camera is not enabled")
    check(camera.get("width") == 640 and camera.get("height") == 360, "front_rgb resolution changed")
    close(camera.get("fov_degrees"), 82, "front_rgb FOV")


def verify_authored(root: Path) -> None:
    manifest = load_yaml(root / "ScenarioSpec.yaml")
    check(manifest.get("schema") == "mns.scenario.v1", "exported ScenarioSpec schema changed")
    includes = manifest.get("includes", {})
    for key in ("environment", "runtime", "sensor_profiles", "vehicles", "objects", "random_spawns"):
        check(key in includes, f"exported ScenarioSpec is missing include: {key}")

    environment_doc = load_yaml(root / "Environment.yaml")
    environment = environment_doc.get("environment", {})
    check(environment.get("id") == "blocks", "authoring did not preserve the Blocks environment")
    frame = environment_doc.get("coordinate_frame", {})
    check(frame.get("convention") == "ros2_flu", "authoring did not export ROS 2 FLU coordinates")
    check(frame.get("unit") == "meter", "authoring did not export meter units")
    time_of_day = environment.get("time_of_day", {})
    check(time_of_day.get("enabled") is True, "time of day was disabled during authoring")
    check(time_of_day.get("start") == "2026-08-03T09:30:00", "time of day start changed")
    weather = environment.get("weather", {})
    check(weather.get("enabled") is True and weather.get("preset") == "rain", "rain weather was not preserved")

    runtime = load_yaml(root / "Runtime.yaml").get("runtime", {})
    features = runtime.get("features", {})
    check(runtime.get("profile") == "airsim_unreal_ardupilot_docker", "runtime profile changed")
    check(features.get("ros2_bridge") is True, "ROS 2 bridge was disabled")
    check(features.get("qgroundcontrol") is False and features.get("mavros") is False, "optional runtime services changed")

    verify_sensor_profile(root)

    vehicle_files = sorted((root / "Vehicles").glob("*.yaml"))
    check(len(vehicle_files) == 2, f"expected two authored drones, found {len(vehicle_files)}")
    vehicles = [load_yaml(path) for path in vehicle_files]
    by_name = {str(vehicle.get("runtime_name")): vehicle for vehicle in vehicles}
    check(set(by_name) == set(EXPECTED_STARTS), f"runtime vehicle names changed: {sorted(by_name)}")
    for runtime_name, expected in EXPECTED_STARTS.items():
        vehicle = by_name[runtime_name]
        check(vehicle.get("sensor_profile") == "e2e_multirotor", f"{runtime_name} sensor profile changed")
        check(vehicle.get("ros_domain_id") == expected["domain"], f"{runtime_name} ROS domain changed")
        start = vehicle.get("start", {})
        check(start.get("frame") == "ros2_flu", f"{runtime_name} spawn frame changed")
        for field in ("x", "y", "z", "yaw"):
            close(start.get(field), expected[field], f"{runtime_name} start.{field}")

    objects = load_yaml(root / "Objects.yaml").get("objects", [])
    check(isinstance(objects, list) and len(objects) == 1, "expected one authored static object")
    authored_object = objects[0]
    check(authored_object.get("asset_pack") == "scenario_runtime_basic", "static object pack changed")
    check(authored_object.get("asset") == "blocking_box", "static object asset changed")
    transform = authored_object.get("transform", {})
    check(transform.get("frame") == "ros2_flu", "static object frame changed")
    for field, expected in (("x", 12), ("y", 5), ("z", 0.5)):
        close(transform.get("position", {}).get(field), expected, f"static object position.{field}")
    close(transform.get("rotation", {}).get("yaw"), 35, "static object yaw")
    for field, expected in (("x", 1.5), ("y", 0.75), ("z", 2)):
        close(transform.get("scale", {}).get(field), expected, f"static object scale.{field}")

    spawns = load_yaml(root / "RandomSpawns.yaml").get("random_spawns", [])
    check(isinstance(spawns, list) and len(spawns) == 1, "expected one authored random spawn volume")
    spawn = spawns[0]
    check(spawn.get("asset_pack") == "scenario_runtime_basic" and spawn.get("asset") == "blocking_box", "spawn asset changed")
    check(spawn.get("count") == 3 and spawn.get("random_yaw") is True, "spawn policy changed")
    bounds = spawn.get("bounds", {})
    check(bounds.get("frame") == "ros2_flu", "spawn volume frame changed")
    for field, expected in (("x", 20), ("y", -6), ("z", 4)):
        close(bounds.get("center", {}).get(field), expected, f"spawn center.{field}")
    for field, expected in (("x", 8), ("y", 6), ("z", 8)):
        close(bounds.get("size", {}).get(field), expected, f"spawn size.{field}")

    catalogs = root / "ScenarioBundle" / "catalogs"
    environments = load_yaml(catalogs / "environments.yaml").get("environments", {})
    blocks = environments.get("blocks", {})
    level_pack_path = blocks.get("level_pack")
    check(isinstance(level_pack_path, str), "exported Blocks environment has no level-pack manifest")
    level_pack = load_json(root / level_pack_path)
    check(level_pack.get("runtime", {}).get("image") == EXPECTED_BLOCKS_IMAGE, "exported bundle does not pin the review.2 Blocks image")
    asset_packs = load_yaml(catalogs / "asset_packs.yaml").get("asset_packs", {})
    primitive = asset_packs.get("scenario_runtime_basic", {})
    blocking_box = primitive.get("assets", {}).get("blocking_box", {}) if isinstance(primitive, dict) else {}
    check(blocking_box.get("class") == EXPECTED_CLASS, "exported primitive has no runtime class binding")


def env_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


def verify_generated(root: Path) -> None:
    manifest = load_json(root / "generated-manifest.json")
    check(manifest.get("airsim", {}).get("image") == EXPECTED_BLOCKS_IMAGE, "generator selected the wrong Blocks image")
    check(len(manifest.get("vehicles", [])) == 2, "generated manifest does not contain two drones")
    plugin_summary = manifest.get("scenario_plugin", {})
    check(plugin_summary.get("enabled") is True, "scenario plugin was not enabled")
    check(plugin_summary.get("object_count") == 1, "generated object count changed")
    check(plugin_summary.get("random_spawn_volume_count") == 1, "generated random spawn volume count changed")
    check(plugin_summary.get("random_spawn_requested_count") == 3, "generated random spawn count changed")
    conditions_summary = manifest.get("scenario_conditions", {})
    check(conditions_summary.get("enabled") is True, "scenario conditions were not enabled")

    settings = load_json(root / "config" / "unreal-airsim" / "settings.json")
    vehicles = settings.get("Vehicles", {})
    check(set(vehicles) == set(EXPECTED_STARTS), f"AirSim vehicles changed: {sorted(vehicles)}")
    for runtime_name, expected in EXPECTED_STARTS.items():
        vehicle = vehicles[runtime_name]
        close(vehicle.get("X"), expected["x"], f"{runtime_name} AirSim X")
        close(vehicle.get("Y"), -expected["y"], f"{runtime_name} FLU-to-NED Y")
        close(vehicle.get("Z"), -expected["z"], f"{runtime_name} FLU-to-NED Z")
        close(vehicle.get("Yaw"), expected["yaw"], f"{runtime_name} AirSim yaw")
        lidar = vehicle.get("Sensors", {}).get("LidarSensor1", {})
        check(lidar.get("Enabled") is True, f"{runtime_name} lidar is disabled")
        check(lidar.get("NumberOfChannels") == 24, f"{runtime_name} lidar channels changed")
        check(lidar.get("PointsPerSecond") == 120000, f"{runtime_name} lidar point rate changed")
        camera = vehicle.get("Cameras", {}).get("front_rgb", {})
        captures = camera.get("CaptureSettings", [])
        check(len(captures) == 1, f"{runtime_name} front_rgb capture settings missing")
        capture = captures[0]
        check(capture.get("Width") == 640 and capture.get("Height") == 360, f"{runtime_name} camera resolution changed")
        close(capture.get("FovDegrees"), 82, f"{runtime_name} camera FOV")

    plugin = load_json(root / "config" / "scenario-plugin" / "scenario_plugin.json")
    check(len(plugin.get("objects", [])) == 1, "runtime plugin has the wrong static object count")
    check(len(plugin.get("random_spawns", [])) == 1, "runtime plugin has the wrong spawn volume count")
    check(plugin["objects"][0].get("class") == EXPECTED_CLASS, "static object runtime class changed")
    check(plugin["random_spawns"][0].get("class") == EXPECTED_CLASS, "spawn runtime class changed")
    check(plugin["random_spawns"][0].get("count") == 3, "runtime spawn count changed")

    conditions = load_json(root / "config" / "scenario" / "scenario_conditions.json").get("conditions", {})
    check(conditions.get("weather", {}).get("preset") == "rain", "generated weather is not rain")
    check(conditions.get("time_of_day", {}).get("enabled") is True, "generated time of day is disabled")

    compose = load_yaml(root / "docker-compose.yml")
    services = compose.get("services", {})
    bridge_services = [service for service in services.values() if "single_vehicle.launch.py" in str(service.get("command", ""))]
    check(len(bridge_services) == 2, f"expected two ROS 2 bridge services, found {len(bridge_services)}")
    generated_env = env_values(root / ".env")
    check(generated_env.get("AIRSIM_IMAGE") == EXPECTED_BLOCKS_IMAGE, "generated .env does not pin Blocks review.2")
    args = (root / "scenario-docker-args.txt").read_text(encoding="utf-8")
    check("-MnSScenarioPluginConfig=/simrunner/scenario_plugin.json" in args, "runtime plugin argument missing")
    check("-MnSScenarioConditions=/simrunner/scenario_conditions.json" in args, "runtime conditions argument missing")


def verify_live(root: Path) -> None:
    unreal_log = (root / "unreal.log").read_text(encoding="utf-8")
    check("Applied scenario conditions to sky/weather" in unreal_log, "runtime did not report applying weather/time conditions")
    check(re.search(r"spawned\s+4/4\s+authored object\(s\)", unreal_log) is not None, "runtime did not spawn the static object plus three random objects")
    for runtime_name in EXPECTED_STARTS:
        topics = (root / f"topics-{runtime_name}.txt").read_text(encoding="utf-8")
        camera_topic = f"/{runtime_name}/front_rgb/image_raw"
        lidar_topic = f"/{runtime_name}/LidarSensor1/points"
        check(camera_topic in topics, f"live camera topic missing: {camera_topic}")
        check(lidar_topic in topics, f"live lidar topic missing: {lidar_topic}")
        for kind in ("camera", "lidar"):
            rates = (root / f"{kind}-hz-{runtime_name}.txt").read_text(encoding="utf-8")
            check("average rate:" in rates, f"no live {kind} samples observed for {runtime_name}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("authored", "generated", "live"), required=True)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        {"authored": verify_authored, "generated": verify_generated, "live": verify_live}[args.phase](root)
    except (OSError, ValueError, VerificationError) as exc:
        print(f"[full-product-e2e][FAIL] {exc}")
        return 1
    print(f"[full-product-e2e][PASS] {args.phase}: {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
