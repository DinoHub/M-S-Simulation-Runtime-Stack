"""ArduPilot stack-generation profile."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import AutopilotProfile, relative_repo_root
from ..models import ResolvedScenario, Vehicle, VehicleConnection


class ArduPilotProfile(AutopilotProfile):
    type_name = "ardupilot"
    default_hostname_prefix = "ardupilot-drone"
    bridge_prefix = "c"
    mavros_config = "mavros_ardupilot.yaml"
    support_config_stack = "ardupilot-xfs-multi"
    support_config_dirs = ("qgroundcontrol", "ardupilot")
    data_port_manifest_key = "udp_port"

    def default_runtime_name(self, index: int) -> str:
        return f"Copter{index}"

    def docker_hostname_index(self, index: int) -> int:
        return index - 1

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
        host_endpoint = endpoint_mode == "host"
        host_like_endpoint = runtime_profile == "editor" or host_endpoint
        if runtime_profile == "editor":
            data_port = int(vehicle_data.get("editor_udp_port", 9003 + (index - 1) * 10))
            control_port = int(vehicle_data.get("editor_control_port", 9002 + (index - 1) * 10))
        elif host_endpoint:
            data_port = int(vehicle_data.get("udp_port", 9003 + (index - 1) * 10))
            control_port = int(vehicle_data.get("control_port", 9002 + (index - 1) * 10))
        else:
            data_port = int(vehicle_data.get("udp_port", 9003))
            control_port = int(vehicle_data.get("control_port", 9002 + index - 1))

        if managed:
            mavros_host = "host.docker.internal" if host_like_endpoint else f"ardupilot-drone-{index - 1}"
            default_mavros_port = 5760 + (index - 1) * 10 if host_like_endpoint else 5760
        elif host_like_endpoint:
            mavros_host = "host.docker.internal"
            default_mavros_port = 5760 + (index - 1) * 10
        else:
            mavros_host = autopilot_host
            default_mavros_port = 5760
        mavros_port = int(vehicle_data.get("mavros_tcp_port", default_mavros_port))
        default_mavros_url = f"tcp://{mavros_host}:{mavros_port}"

        return VehicleConnection(
            host=autopilot_host,
            data_port=data_port,
            control_port=control_port,
            mavros_port=mavros_port,
            mavros_fcu_url=str(vehicle_data.get("mavros_fcu_url", default_mavros_url)),
            data_protocol="udp",
            mavros_protocol="tcp",
        )

    def airsim_vehicle_settings(self, resolved: ResolvedScenario, vehicle: Vehicle) -> dict[str, Any]:
        return {
            "VehicleType": "ArduCopter",
            "UseSerial": False,
            "LocalHostIp": "0.0.0.0",
            "UdpIp": vehicle.connection.host,
            "UdpPort": vehicle.connection.data_port,
            "ControlPort": vehicle.connection.control_port,
            "ControlPortLocal": vehicle.connection.control_port,
            "LockStep": True,
        }

    def service_name(self, vehicle: Vehicle) -> str:
        return f"ardupilot-drone-{vehicle.index - 1}"

    def container_name(self, resolved: ResolvedScenario, vehicle: Vehicle) -> str:
        return f"{resolved.stack_name}-ardupilot-drone-{vehicle.index - 1}"

    def hostname(self, vehicle: Vehicle) -> str:
        return f"ardupilot-drone-{vehicle.index - 1}"

    def airsim_depends_on_managed_autopilots(self) -> bool:
        return True

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
            "build": {"context": str(rel_services / "services/ardupilot"), "dockerfile": "Dockerfile.ardupilotbuild"},
            "image": "${ARDUPILOT_IMAGE:-local/auto_mns:ardupilot-latest}",
            "pull_policy": "build",
            "container_name": self.container_name(resolved, vehicle),
            "hostname": self.hostname(vehicle),
            "restart": "no",
            "depends_on": {"qgroundcontrol-x11": {"condition": "service_started"}} if resolved.qgroundcontrol else {},
            "user": "${HOST_UID:?set HOST_UID}:${GID:?set GID}",
            "cap_add": ["SYS_PTRACE"],
            "security_opt": ["seccomp:unconfined"],
            "environment": [
                f"ARDUPILOT_SIM_HOSTNAME=${{ARDUPILOT_SIM_HOSTNAME:-{sim_host}}}",
                f"MAVLINK_GCS_HOST=${{MAVLINK_GCS_HOST:-{gcs_host}}}",
                "HOST_NETWORK_MODE=true" if host_managed else "HOST_NETWORK_MODE=false",
                f"AIRSIM_SENSOR_PORT={vehicle.connection.data_port}" if editor_profile or host_managed else "",
                f"AIRSIM_CONTROL_PORT={vehicle.connection.control_port}" if editor_profile or host_managed else "",
                f"MAVROS_TCP_PORT={vehicle.connection.mavros_port}" if host_managed else "",
                "ARDUPILOT_HOME_LAT=${ARDUPILOT_HOME_LAT:-42.764938}",
                "ARDUPILOT_HOME_LON=${ARDUPILOT_HOME_LON:--115.579201}",
                "ARDUPILOT_HOME_ALT=${ARDUPILOT_HOME_ALT:-1183}",
                f"INSTANCE_NUM={instance}",
            ],
            "volumes": ["${CONFIG_ROOT}/ardupilot/config:/ardupilot_config:ro"],
            "command": [str(instance)],
            "healthcheck": {
                "test": ["CMD-SHELL", "test -f /ardupilot_workspace/ardupilot/build/sitl/bin/arducopter && pgrep -f arducopter"],
                "interval": "5s",
                "timeout": "5s",
                "retries": 24,
                "start_period": "120s",
            },
        }
        service["environment"] = [item for item in service["environment"] if item]
        if host_managed:
            service["network_mode"] = "host"
        else:
            service["networks"] = ["sim_network", f"agent_internal-{vehicle.index}"]
        if editor_profile or not resolved.qgroundcontrol:
            service["extra_hosts"] = ["host.docker.internal:host-gateway"]
        if editor_profile:
            service["ports"] = [
                f"${{ARDUPILOT_{vehicle.index}_SENSOR_HOST_PORT:-{vehicle.connection.data_port}}}:9003/udp",
            ]
        return service
