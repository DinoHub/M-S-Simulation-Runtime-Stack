"""Render docker-compose.yml for generated stacks."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..autopilots import get_autopilot_profile
from ..autopilots.base import AIRSIM_SERVICE_NAME, AutopilotProfile
from ..models import ResolvedScenario, Vehicle
from ..paths import REPO_ROOT
from .metrics import executable_saved_metrics_path, metrics_enabled, metrics_manifest
from .scenario_artifacts import docker_scenario_args, scenario_plugin_required, uses_legacy_random_spawn_expansion


def relative_repo_root(run_dir: Path) -> Path:
    return Path(os.path.relpath(REPO_ROOT, run_dir))


def _shm_fisheye_camera_names(vehicle: Vehicle) -> list[str]:
    names: list[str] = []
    for name, camera in vehicle.cameras.items():
        if not isinstance(camera, dict):
            continue
        capture_settings = camera.get("CaptureSettings", [])
        shm_enabled = any(
            isinstance(capture, dict)
            and capture.get("FisheyeShmPublishEnabled", True) is not False
            and (
                "fisheye" in name.lower()
                or float(capture.get("FovDegrees", 0) or 0) >= 90
            )
            for capture in capture_settings
        )
        if shm_enabled:
            names.append(name)
    return names


def bridge_command(profile: AutopilotProfile, vehicle: Vehicle) -> list[str]:
    shm_fisheye_camera_names = _shm_fisheye_camera_names(vehicle)
    camera_names_literal = "[" + ",".join(repr(name) for name in shm_fisheye_camera_names) + "]"
    enable_vio_default = "true" if shm_fisheye_camera_names else "false"
    auto_discover_cameras_default = "false" if shm_fisheye_camera_names else "true"
    return [
        "/bin/bash",
        "-lc",
        "\n".join([
            "set -e",
            "source /opt/ros/humble/setup.bash",
            "source /ws/install/setup.bash",
            f'CAMERA_NAMES_DEFAULT="{camera_names_literal}"',
            ': "$${CAMERA_NAMES:=$$CAMERA_NAMES_DEFAULT}"',
            f'echo "Starting AirSim ROS2 bridge for {vehicle.runtime_name} on ROS_DOMAIN_ID={vehicle.ros_domain_id}"',
            "exec ros2 launch airsim_ros2_bridge single_vehicle.launch.py \\",
            "  host_ip:=$${AIRSIM_HOST_IP:-host.docker.internal} \\",
            "  host_port:=$${AIRSIM_HOST_PORT:-41451} \\",
            f"  vehicle_name:={vehicle.runtime_name} \\",
            f"  enable_vio:=$${{ENABLE_VIO:-{enable_vio_default}}} \\",
            '  "camera_names:=$${CAMERA_NAMES}" \\',
            f"  auto_discover_cameras:=$${{AUTO_DISCOVER_CAMERAS:-{auto_discover_cameras_default}}} \\",
            "  poll_rate_hz:=$${POLL_RATE_HZ:-30.0} \\",
            "  enable_mavros:=$${ENABLE_MAVROS:-false} \\",
            f"  mavros_vehicle:={vehicle.runtime_name} \\",
            f"  mavros_config:=$${{MAVROS_CONFIG:-{profile.mavros_config}}} \\",
            f"  mavros_fcu_url:=$${{MAVROS_{vehicle.index}_FCU_URL:-{vehicle.mavros_fcu_url}}} \\",
            "  enable_localization:=$${ENABLE_LOCALIZATION:-true} \\",
            "  localization_source:=$${LOCALIZATION_SOURCE:-sim} \\",
            "  enable_coordination:=$${ENABLE_COORDINATION:-false} \\",
            "  enable_local_obs:=$${ENABLE_LOCAL_OBS:-true} \\",
            "  enable_static_pcd:=$${ENABLE_STATIC_PCD:-false} \\",
            "  test_local_planner_only:=$${TEST_LOCAL_PLANNER:-true} \\",
            "  use_sim_time:=$${USE_SIM_TIME:-true} \\",
            "  local_obs_target_frame:=$${LOCAL_OBS_TARGET_FRAME:-map} \\",
            "  local_obs_buffer_sec:=$${LOCAL_OBS_BUFFER_SEC:-5.0} \\",
            "  local_obs_voxel_size:=$${LOCAL_OBS_VOXEL_SIZE:-0.15}",
        ]),
    ]


def airsim_command(resolved: ResolvedScenario) -> list[str]:
    args = [
        "echo 'Waiting 3s for initialization...'",
        "sleep 3",
        f"exec {resolved.airsim_executable}",
        "-windowed",
        "-ResX=1920",
        "-ResY=1080",
        "-NoRayTracing",
        "-ExecCmds='r.RayTracing 0;r.RayTracing.ForceAllRayTracingEffects 0;r.Lumen.HardwareRayTracing 0'",
        *docker_scenario_args(resolved),
    ]
    return [" && ".join(args[:2]) + " && " + " ".join(args[2:])]


def executable_settings_path(resolved: ResolvedScenario) -> str:
    executable_dir = Path(resolved.airsim_executable).parent.as_posix()
    if executable_dir in ("", "."):
        executable_dir = "/app/Xfs"
    return f"{executable_dir}/settings.json"


def compose_yaml(resolved: ResolvedScenario, run_dir: Path) -> dict[str, Any]:
    rel_services = relative_repo_root(run_dir)
    autopilot_profile = get_autopilot_profile(resolved.autopilot_type)
    services: dict[str, Any] = {}
    editor_profile = resolved.runtime_profile == "editor"
    host_endpoint = resolved.autopilot_endpoint.lower() == "host"
    host_managed = resolved.autopilot_managed and host_endpoint and not editor_profile
    metrics = metrics_manifest(resolved, run_dir)

    if resolved.autopilot_managed:
        for vehicle in resolved.vehicles:
            service = autopilot_profile.compose_service(
                resolved,
                run_dir,
                vehicle,
                editor_profile=editor_profile,
                host_managed=host_managed,
            )
            services[autopilot_profile.service_name(vehicle)] = service

    if not editor_profile:
        airsim_depends = {}
        if resolved.autopilot_managed and autopilot_profile.airsim_depends_on_managed_autopilots():
            airsim_depends = {autopilot_profile.service_name(v): {"condition": "service_started"} for v in resolved.vehicles}
        services[AIRSIM_SERVICE_NAME] = {
            "image": f"${{AIRSIM_IMAGE:-{resolved.airsim_image}}}",
            "pull_policy": "if_not_present",
            "container_name": f"{resolved.stack_name}-{AIRSIM_SERVICE_NAME}",
            "hostname": AIRSIM_SERVICE_NAME,
            "restart": "unless-stopped",
            "depends_on": airsim_depends,
            "user": "${HOST_UID:?set HOST_UID}:${GID:?set GID}",
            "group_add": ["video"],
            "init": True,
            "deploy": {"resources": {"reservations": {"devices": [{"driver": "nvidia", "count": "all", "capabilities": ["gpu"]}]}}},
            "environment": [
                "NVIDIA_DRIVER_CAPABILITIES=all",
                "DISPLAY=${DISPLAY:-:1}",
                "SDL_VIDEODRIVER=${SDL_VIDEODRIVER:-x11}",
                "XAUTHORITY=/tmp/.Xauthority",
                f"SIM_OBJECT_CLUTTER_ENABLED={'true' if resolved.object_clutter.enabled else 'false'}",
                "SIM_OBJECT_CLUTTER_CONFIG=/simrunner/object_clutter.yaml",
                f"SIM_OBJECT_CLUTTER_SEED={resolved.object_clutter.seed}",
                f"SIM_OBJECT_CLUTTER_DENSITY={resolved.object_clutter.density}",
                f"XFS_CONTAINER_SPAWNER_SEED={resolved.object_clutter.seed}",
                f"SCENARIO_RUNTIME_ENABLED={'true' if resolved.scenario_runtime.enabled and not uses_legacy_random_spawn_expansion(resolved) else 'false'}",
                "SCENARIO_RUNTIME_PATH=/simrunner/scenario_runtime.json",
                f"SCENARIO_CONDITIONS_ENABLED={'true' if resolved.conditions else 'false'}",
                "SCENARIO_CONDITIONS_PATH=/simrunner/scenario_conditions.json",
                f"SCENARIO_PLUGIN_ENABLED={'true' if scenario_plugin_required(resolved) else 'false'}",
                "SCENARIO_PLUGIN_CONFIG=/simrunner/scenario_plugin.json",
                f"MNS_SCENARIO_ID=${{MNS_SCENARIO_ID:-{resolved.scenario_id}}}",
                f"MNS_METRICS_ENABLED=${{MNS_METRICS_ENABLED:-{metrics['environment']['MNS_METRICS_ENABLED']}}}",
                f"MNS_METRICS_STREAM_ENABLED=${{MNS_METRICS_STREAM_ENABLED:-{metrics['environment']['MNS_METRICS_STREAM_ENABLED']}}}",
                f"MNS_METRICS_STREAM_BIND=${{MNS_METRICS_STREAM_BIND:-{metrics['environment']['MNS_METRICS_STREAM_BIND']}}}",
                f"MNS_METRICS_STREAM_PORT=${{MNS_METRICS_STREAM_PORT:-{metrics['environment']['MNS_METRICS_STREAM_PORT']}}}",
            ],
            "networks": ["sim_network"],
            "ports": ["${AIRSIM_HOST_PORT:-41451}:41451"],
            "ipc": "host",
            "volumes": [
                "/tmp/.X11-unix:/tmp/.X11-unix:rw",
                "${XAUTHORITY:-/run/user/1000/gdm/Xauthority}:/tmp/.Xauthority:ro",
                "/dev/shm:/dev/shm:rw",
                "/tmp/iceoryx2:/tmp/iceoryx2:rw",
                f"${{CONFIG_ROOT}}/unreal-airsim/settings.json:{executable_settings_path(resolved)}:ro",
                "${CONFIG_ROOT}/scenario/object_clutter.yaml:/simrunner/object_clutter.yaml:ro",
                "${CONFIG_ROOT}/scenario/object_clutter.json:/simrunner/object_clutter.json:ro",
                "${CONFIG_ROOT}/scenario/scenario_runtime.json:/simrunner/scenario_runtime.json:ro",
                "${CONFIG_ROOT}/scenario/scenario_conditions.json:/simrunner/scenario_conditions.json:ro",
                "${CONFIG_ROOT}/scenario-plugin/scenario_plugin.json:/simrunner/scenario_plugin.json:ro",
                "${CONFIG_ROOT}/asset-packs:/simrunner/asset-packs:ro",
            ],
            "extra_hosts": ["host.docker.internal:host-gateway"],
            "entrypoint": ["/bin/bash", "-c"],
            "command": airsim_command(resolved),
            "healthcheck": {
                "test": ["CMD-SHELL", "nc -z 127.0.0.1 41451 || exit 1"],
                "interval": "10s",
                "timeout": "5s",
                "retries": 18,
                "start_period": "60s",
            },
        }
        if metrics_enabled(resolved):
            services[AIRSIM_SERVICE_NAME]["volumes"].append(
                f"${{MNS_METRICS_OUTPUT:-./outputs/metrics}}:{executable_saved_metrics_path(resolved)}:rw"
            )
        if metrics["live_stream"]["enabled"]:
            stream_port = metrics["live_stream"]["port"]
            services[AIRSIM_SERVICE_NAME]["ports"].append(
                f"${{MNS_METRICS_STREAM_HOST_PORT:-{stream_port}}}:${{MNS_METRICS_STREAM_PORT:-{stream_port}}}"
            )
        if host_endpoint:
            services[AIRSIM_SERVICE_NAME]["ports"].extend([
                f"${{AIRSIM_{vehicle.index}_CONTROL_HOST_PORT:-{vehicle.control_port}}}:{vehicle.control_port}/udp"
                for vehicle in resolved.vehicles
            ])
            services[AIRSIM_SERVICE_NAME]["ports"].extend([
                f"${{AIRSIM_{vehicle.index}_TCP_HOST_PORT:-{vehicle.connection.data_port}}}:{vehicle.connection.data_port}/tcp"
                for vehicle in resolved.vehicles
                if vehicle.connection.data_protocol == "tcp"
            ])

    if resolved.ros2_bridge:
        for vehicle in resolved.vehicles:
            suffix = autopilot_profile.bridge_suffix(vehicle)
            shm_fisheye_camera_names = _shm_fisheye_camera_names(vehicle)
            enable_vio_default = "true" if shm_fisheye_camera_names else "false"
            auto_discover_cameras_default = "false" if shm_fisheye_camera_names else "true"
            bridge_service: dict[str, Any] = {
                "build": {"context": str(rel_services / "services/ros2"), "dockerfile": "docker/Dockerfile"},
                "image": "${ROS2_IMAGE:-local/auto_mns:tevv-airsim-ros2-bridge-humble}",
                "pull_policy": "build",
                "container_name": f"{resolved.stack_name}-airsim-bridge-{suffix}",
                "hostname": f"airsim_bridge_{suffix}",
                "init": True,
                "restart": "unless-stopped",
                "ipc": "host",
                "user": "${HOST_UID:?set HOST_UID}:${GID:?set GID}",
                "entrypoint": [],
                "extra_hosts": ["host.docker.internal:host-gateway"],
                "environment": {
                    "DISPLAY": "${DISPLAY:-:1}",
                    "XAUTHORITY": "/tmp/.Xauthority",
                    "QT_X11_NO_MITSHM": 1,
                    "ROS_LOCALHOST_ONLY": 0,
                    "RMW_IMPLEMENTATION": "${RMW_IMPLEMENTATION:-rmw_fastrtps_cpp}",
                    "FASTDDS_BUILTIN_TRANSPORTS": "${FASTDDS_BUILTIN_TRANSPORTS:-UDPv4}",
                    "AIRSIM_HOST_IP": "host.docker.internal",
                    "AIRSIM_HOST_PORT": "${AIRSIM_HOST_PORT:-41451}",
                    "ENABLE_MAVROS": "true" if resolved.bridge_mavros else "${ENABLE_MAVROS:-false}",
                    "ENABLE_LOCALIZATION": "${ENABLE_LOCALIZATION:-true}",
                    "LOCALIZATION_SOURCE": "${LOCALIZATION_SOURCE:-sim}",
                    "ENABLE_COORDINATION": "${ENABLE_COORDINATION:-false}",
                    "ENABLE_LOCAL_OBS": "${ENABLE_LOCAL_OBS:-true}",
                    "ENABLE_STATIC_PCD": "${ENABLE_STATIC_PCD:-false}",
                    "TEST_LOCAL_PLANNER": "${TEST_LOCAL_PLANNER:-true}",
                    "USE_SIM_TIME": "${USE_SIM_TIME:-true}",
                    "ENABLE_VIO": f"${{ENABLE_VIO:-{enable_vio_default}}}",
                    "AUTO_DISCOVER_CAMERAS": f"${{AUTO_DISCOVER_CAMERAS:-{auto_discover_cameras_default}}}",
                    "CAMERA_NAMES": "${CAMERA_NAMES:-}",
                    "POLL_RATE_HZ": "${POLL_RATE_HZ:-30.0}",
                    "ROS_DOMAIN_ID": vehicle.ros_domain_id,
                    "VEHICLE_NAME": vehicle.runtime_name,
                },
                "volumes": [
                    "/tmp/.X11-unix:/tmp/.X11-unix:rw",
                    "${XAUTHORITY:-/run/user/1000/gdm/Xauthority}:/tmp/.Xauthority:ro",
                    "/dev/shm:/dev/shm:rw",
                    "/tmp/iceoryx2:/tmp/iceoryx2:rw",
                ],
                "networks": [f"agent_internal-{vehicle.index}"],
                "command": bridge_command(autopilot_profile, vehicle),
            }
            if not editor_profile:
                bridge_service["depends_on"] = {AIRSIM_SERVICE_NAME: {"condition": "service_healthy"}}
            services[f"airsim_bridge_{suffix}"] = bridge_service

    if resolved.qgroundcontrol:
        qgc_service: dict[str, Any] = {
            "build": {"context": str(rel_services / "external-services/qgroundcontrol"), "dockerfile": "Dockerfile.qgc-x11"},
            "image": "local/auto_mns:airsim-qgc-x11-latest",
            "pull_policy": "build",
            "container_name": f"{resolved.stack_name}-qgroundcontrol-x11",
            "hostname": "qgc-x11",
            "ipc": "host",
            "environment": [
                "DISPLAY=${DISPLAY:-:0}",
                "XAUTHORITY=/tmp/.Xauthority",
                "QT_X11_NO_MITSHM=1",
                "QGC_APPIMAGE=/opt/qgroundcontrol/QGroundControl.AppImage",
                "APPIMAGE_EXTRACT_AND_RUN=1",
                "LIBGL_ALWAYS_SOFTWARE=0",
                "MESA_GL_VERSION_OVERRIDE=3.3",
                "QT_QPA_PLATFORM=xcb:depth=24",
            ],
            "volumes": [
                "/tmp/.X11-unix:/tmp/.X11-unix:rw",
                "${XAUTHORITY:-$HOME/.Xauthority}:/tmp/.Xauthority:ro",
                "${CONFIG_ROOT}/qgroundcontrol/qgc_config:/config-template:ro",
                "${CONFIG_ROOT}/qgroundcontrol/user_config:/home/qgc/.config/QGroundControl",
            ],
            "devices": ["/dev/dri:/dev/dri"],
            "shm_size": "2gb",
            "deploy": {"resources": {"reservations": {"devices": [{"driver": "nvidia", "count": 1, "capabilities": ["gpu", "graphics", "utility"]}]}}},
            "security_opt": ["seccomp:unconfined"],
            "user": "${HOST_UID:?set HOST_UID}:${GID:?set GID}",
            "group_add": ["video"],
            "init": True,
            "restart": "unless-stopped",
            "healthcheck": {
                "test": ["CMD-SHELL", "xdpyinfo > /dev/null 2>&1 || exit 1"],
                "interval": "30s",
                "timeout": "10s",
                "retries": 3,
                "start_period": "15s",
            },
        }
        if host_managed:
            qgc_service["network_mode"] = "host"
        else:
            qgc_service["networks"] = ["sim_network"]
        services["qgroundcontrol-x11"] = qgc_service

    networks = {"sim_network": {"name": f"{resolved.stack_name}_sim", "driver": "bridge"}}
    for vehicle in resolved.vehicles:
        networks[f"agent_internal-{vehicle.index}"] = {"name": f"{resolved.stack_name}_agent_internal-{vehicle.index}", "driver": "bridge"}

    return {"name": resolved.stack_name, "services": services, "networks": networks}
