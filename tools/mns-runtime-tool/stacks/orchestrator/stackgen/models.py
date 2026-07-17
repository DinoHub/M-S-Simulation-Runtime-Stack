"""Normalized scenario models used by generators."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .scenario_runtime import ScenarioRuntimeConfig


@dataclass(frozen=True)
class VehicleConnection:
    host: str
    data_port: int
    control_port: int
    mavros_port: int
    mavros_fcu_url: str
    data_protocol: str
    mavros_protocol: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Vehicle:
    source_name: str
    runtime_name: str
    index: int
    vehicle_type: str
    spawn: dict[str, Any]
    sensors: dict[str, Any]
    cameras: dict[str, Any]
    ros_domain_id: int
    connection: VehicleConnection
    pawn_path: str | None = None
    pawn_bp: str | None = None
    runtime_asset_pack: dict[str, Any] | None = None

    @property
    def autopilot_host(self) -> str:
        return self.connection.host

    @property
    def fdm_udp_port(self) -> int:
        return self.connection.data_port

    @property
    def control_port(self) -> int:
        return self.connection.control_port

    @property
    def mavros_tcp_port(self) -> int:
        return self.connection.mavros_port

    @property
    def mavros_fcu_url(self) -> str:
        return self.connection.mavros_fcu_url


@dataclass(frozen=True)
class ObjectClutter:
    enabled: bool
    backend: str
    seed: int
    density: str
    count: int | None
    placement: str
    blueprint: str
    data_table: str
    assets: list[str]
    entries: list[dict[str, Any]]
    placements: list[dict[str, Any]]
    asset_packs: list[dict[str, Any]]
    config_source: Path | None


@dataclass(frozen=True)
class MetricsConfig:
    enabled: bool = True
    required: bool = False
    requested: list[str] = field(default_factory=list)
    live_stream: bool = False
    stream_port: int = 9700
    stream_bind: str = "0.0.0.0"
    archive_upload: bool = False
    archive_load_clickhouse: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ResolvedScenario:
    scenario_id: str
    name: str
    stack_name: str
    runtime_profile: str
    environment_name: str
    airsim_image: str
    airsim_executable: str
    autopilot_type: str
    autopilot_managed: bool
    autopilot_endpoint: str
    autopilot_hostname_prefix: str
    qgroundcontrol: bool
    ros2_bridge: bool
    bridge_mavros: bool
    origin: dict[str, Any]
    time_of_day: dict[str, Any]
    conditions: dict[str, Any]
    object_clutter: ObjectClutter
    scenario_runtime: ScenarioRuntimeConfig
    vehicles: list[Vehicle]
    source_files: list[Path]
    source_root: Path
    random_spawns: list[dict[str, Any]] = field(default_factory=list)
    runtime_artifact: dict[str, Any] = field(default_factory=dict)
    metrics: MetricsConfig = field(default_factory=MetricsConfig)
