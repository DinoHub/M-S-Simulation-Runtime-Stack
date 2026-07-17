"""Base classes and helpers for autopilot-specific stack generation."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..models import ResolvedScenario, Vehicle, VehicleConnection
from ..paths import REPO_ROOT


AIRSIM_SERVICE_NAME = "unreal-airsim"


def relative_repo_root(run_dir: Path) -> Path:
    return Path(os.path.relpath(REPO_ROOT, run_dir))


class AutopilotProfile:
    type_name = ""
    default_hostname_prefix = ""
    bridge_prefix = ""
    mavros_config = ""
    support_config_stack = ""
    support_config_dirs: tuple[str, ...] = ()
    data_port_manifest_key = "data_port"

    def default_runtime_name(self, index: int) -> str:
        raise NotImplementedError

    def docker_hostname_index(self, index: int) -> int:
        raise NotImplementedError

    def resolve_autopilot_host(
        self,
        vehicle_data: dict[str, Any],
        *,
        endpoint: str,
        endpoint_mode: str,
        endpoint_prefix: str,
        runtime_profile: str,
        managed: bool,
        index: int,
    ) -> str:
        host_override = vehicle_data.get("autopilot_host")
        if runtime_profile == "editor":
            return "127.0.0.1"
        if host_override:
            return str(host_override)
        if endpoint_mode == "host":
            return "host.docker.internal"
        if endpoint.startswith("docker:"):
            return f"{endpoint.split(':', 1)[1]}-{self.docker_hostname_index(index)}"
        if endpoint_mode == "docker":
            return f"{endpoint_prefix}-{self.docker_hostname_index(index)}"
        if managed:
            return f"{self.default_hostname_prefix}-{self.docker_hostname_index(index)}"
        return endpoint

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
        raise NotImplementedError

    def airsim_vehicle_settings(self, resolved: ResolvedScenario, vehicle: Vehicle) -> dict[str, Any]:
        raise NotImplementedError

    def service_name(self, vehicle: Vehicle) -> str:
        raise NotImplementedError

    def container_name(self, resolved: ResolvedScenario, vehicle: Vehicle) -> str:
        raise NotImplementedError

    def hostname(self, vehicle: Vehicle) -> str:
        raise NotImplementedError

    def bridge_suffix(self, vehicle: Vehicle) -> str:
        return f"{self.bridge_prefix}{vehicle.index}"

    def airsim_depends_on_managed_autopilots(self) -> bool:
        return False

    def gcs_host(self, resolved: ResolvedScenario, editor_profile: bool, host_managed: bool) -> str:
        if host_managed:
            return "127.0.0.1"
        if resolved.qgroundcontrol:
            return "qgroundcontrol-x11"
        return "host.docker.internal"

    def sim_host(self, editor_profile: bool, host_managed: bool) -> str:
        if host_managed:
            return "127.0.0.1"
        if editor_profile:
            return "host.docker.internal"
        return AIRSIM_SERVICE_NAME

    def compose_service(
        self,
        resolved: ResolvedScenario,
        run_dir: Path,
        vehicle: Vehicle,
        *,
        editor_profile: bool,
        host_managed: bool,
    ) -> dict[str, Any]:
        raise NotImplementedError
