"""PX4 stack-generation profile."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import AIRSIM_SERVICE_NAME, AutopilotProfile, relative_repo_root
from ..models import ResolvedScenario, Vehicle, VehicleConnection


class Px4Profile(AutopilotProfile):
    type_name = "px4"
    default_hostname_prefix = "px4-drone"
    bridge_prefix = "d"
    mavros_config = "mavros_px4.yaml"
    support_config_stack = "px4-xfs-multi"
    support_config_dirs = ("qgroundcontrol",)
    data_port_manifest_key = "tcp_port"

    def default_runtime_name(self, index: int) -> str:
        return f"Drone{index}"

    def docker_hostname_index(self, index: int) -> int:
        return index

    def resolve_connection(
        self,
        vehicle_data: dict[str, Any],
        *,
        index: int,
        runtime_profile: str,
        endpoint_mode: str,
        managed: bool,
        autopilot_host: str,
    ) -> VehicleConnection:
        host_like_endpoint = runtime_profile == "editor" or endpoint_mode == "host"
        data_port = int(vehicle_data.get("tcp_port", vehicle_data.get("udp_port", 4560 + index - 1)))
        control_port = int(vehicle_data.get("control_port", 14540 + index - 1))
        mavros_host = "host.docker.internal" if host_like_endpoint else autopilot_host
        default_mavros_port = 14555 + index - 1 if host_like_endpoint else 14555
        mavros_remote_port = int(vehicle_data.get("mavros_udp_port", vehicle_data.get("mavros_tcp_port", default_mavros_port)))
        mavros_local_port = int(vehicle_data.get("mavros_local_port", 14556))
        default_mavros_url = f"udp://:{mavros_local_port}@{mavros_host}:{mavros_remote_port}"

        return VehicleConnection(
            host=autopilot_host,
            data_port=data_port,
            control_port=control_port,
            mavros_port=mavros_remote_port,
            mavros_fcu_url=str(vehicle_data.get("mavros_fcu_url", default_mavros_url)),
            data_protocol="tcp",
            mavros_protocol="udp",
            metadata={"mavros_local_port": mavros_local_port},
        )

    def airsim_vehicle_settings(self, resolved: ResolvedScenario, vehicle: Vehicle) -> dict[str, Any]:
        host_like_endpoint = resolved.runtime_profile == "editor" or resolved.autopilot_endpoint.lower() == "host"
        remote_control_port = 14580 + vehicle.index - 1 if host_like_endpoint else 14580
        return {
            "VehicleType": "PX4Multirotor",
            "UseSerial": False,
            "UseTcp": True,
            "TcpPort": vehicle.connection.data_port,
            "ControlIp": "remote",
            "ControlPortLocal": vehicle.connection.control_port,
            "ControlPortRemote": remote_control_port,
            "LocalHostIp": "0.0.0.0",
            "LockStep": False,
        }

    def service_name(self, vehicle: Vehicle) -> str:
        return f"px4-drone-{vehicle.index}"

    def container_name(self, resolved: ResolvedScenario, vehicle: Vehicle) -> str:
        return f"{resolved.stack_name}-px4-drone-{vehicle.index}"

    def hostname(self, vehicle: Vehicle) -> str:
        return f"px4-drone-{vehicle.index}"

    def compose_service(
        self,
        resolved: ResolvedScenario,
        run_dir: Path,
        vehicle: Vehicle,
        *,
        editor_profile: bool,
        host_managed: bool,
    ) -> dict[str, Any]:
        rel_services = relative_repo_root(run_dir)
        instance = vehicle.index - 1
        gcs_host = self.gcs_host(resolved, editor_profile, host_managed)
        sim_host = self.sim_host(editor_profile, host_managed)
        service: dict[str, Any] = {
            "build": {"context": str(rel_services / "services/px4"), "dockerfile": "Dockerfile.px4"},
            "image": "${PX4_IMAGE:-local/auto_mns:px4-airsim-px4}",
            "pull_policy": "build",
            "container_name": self.container_name(resolved, vehicle),
            "hostname": self.hostname(vehicle),
            "ipc": "host",
            "restart": "unless-stopped",
            "depends_on": {"qgroundcontrol-x11": {"condition": "service_started"}} if resolved.qgroundcontrol else {},
            "environment": {
                "PX4_SIM_HOSTNAME": f"${{PX4_SIM_HOSTNAME:-{sim_host}}}",
                "PX4_SIM_MODEL": "${PX4_SIM_MODEL:-none_iris}",
                "PX4_SIMULATOR": "none",
                "PX4_HOME_LAT": "${PX4_HOME_LAT:-42.764938}",
                "PX4_HOME_LON": "${PX4_HOME_LON:--115.579201}",
                "PX4_HOME_ALT": "${PX4_HOME_ALT:-1183}",
                "PX4_SYS_AUTOSTART": "${PX4_SYS_AUTOSTART:-10016}",
                "MAV_0_BROADCAST": 1,
                "MAV_1_BROADCAST": 1,
                "MAV_2_BROADCAST": 1,
                "SWARM_ID": "${SWARM_ID:-1}",
                "SWARM_SIZE": len(resolved.vehicles),
                "MAVLINK_MODE": "${MAVLINK_MODE:-router}",
                "MAVLINK_TARGET": f"${{MAVLINK_TARGET:-{gcs_host}}}",
                "ROUTER_LOG_LEVEL": "${ROUTER_LOG_LEVEL:-info}",
                "ROUTER_REPORT_STATS": "${ROUTER_REPORT_STATS:-false}",
                "ROUTER_DEDUPE_PERIOD": "${ROUTER_DEDUPE_PERIOD:-500}",
                "HOST_NETWORK_MODE": "true" if host_managed else "false",
                "PX4_INSTANCE": instance,
            },
            "command": [
                "/bin/bash",
                "-lc",
                " && ".join([
                    f"echo 'Using PX4_SIM_HOSTNAME: '$$PX4_SIM_HOSTNAME' for {vehicle.runtime_name}'",
                    "echo 'Waiting for AirSim simulation to initialize...'",
                    "sleep 20",
                    "cd /px4_workspace/PX4-Autopilot",
                    f"echo 'Starting PX4 SITL for {vehicle.runtime_name}, instance {instance}...'",
                    f"exec ./Scripts/run_airsim_sitl.sh {instance}",
                ]),
            ],
            "healthcheck": {
                "test": ["CMD-SHELL", "pgrep -f 'px4.*-i' && pgrep -f mavlink-routerd"],
                "interval": "5s",
                "timeout": "5s",
                "retries": 24,
                "start_period": "120s",
            },
        }
        if host_managed:
            service["network_mode"] = "host"
        else:
            service["networks"] = ["sim_network", f"agent_internal-{vehicle.index}"]
            if not editor_profile:
                service["depends_on"][AIRSIM_SERVICE_NAME] = {"condition": "service_healthy"}
        if editor_profile or not resolved.qgroundcontrol:
            service["extra_hosts"] = ["host.docker.internal:host-gateway"]
        return service
