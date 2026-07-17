"""Human-readable output for generated stack contracts."""
from __future__ import annotations

import json
from pathlib import Path

from .autopilots import get_autopilot_profile
from .models import ResolvedScenario


def explain(resolved: ResolvedScenario) -> str:
    autopilot_profile = get_autopilot_profile(resolved.autopilot_type)
    lines = [
        f"Scenario: {resolved.name}",
        f"Stack: {resolved.stack_name}",
        f"Profile: {resolved.runtime_profile}",
        f"Environment: {resolved.environment_name}",
        f"Autopilot: {resolved.autopilot_type} ({'managed SITL' if resolved.autopilot_managed else 'external'})",
        f"Object clutter: {'enabled' if resolved.object_clutter.enabled else 'disabled'} "
        f"({resolved.object_clutter.backend}, seed={resolved.object_clutter.seed}, density={resolved.object_clutter.density})",
        f"Scenario runtime: {'enabled' if resolved.scenario_runtime.enabled else 'disabled'} "
        f"(world={resolved.scenario_runtime.world_mode}, map={resolved.scenario_runtime.map or 'current'}, "
        f"pcg_graph={resolved.scenario_runtime.pcg_graph or 'none'})",
        "Vehicles:",
    ]
    for vehicle in resolved.vehicles:
        lines.append(
            f"  {vehicle.source_name} -> {vehicle.runtime_name}: host={vehicle.autopilot_host}, "
            f"{vehicle.connection.data_protocol}={vehicle.connection.data_port}, "
            f"control={vehicle.control_port}, bridge={autopilot_profile.bridge_suffix(vehicle)}, "
            f"ROS_DOMAIN_ID={vehicle.ros_domain_id}"
        )
    return "\n".join(lines)


def ports_text(stack_dir: Path) -> str:
    path = stack_dir / "generated-manifest.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    profile = data.get("runtime_profile", "docker")
    lines = [
        f"Profile: {profile}",
        f"AirSim RPC: {data['airsim_rpc']['host']}:{data['airsim_rpc']['port']}",
    ]
    if profile == "editor":
        lines.append(f"Unreal Editor settings: {stack_dir / data['airsim']['settings_json']}")
        lines.append(f"Unreal Editor launch args: {stack_dir / 'editor-launch-args.txt'}")
    lines.append("Vehicle connections:")
    for vehicle in data["vehicles"]:
        data_protocol = vehicle.get("data_protocol", "udp")
        data_port = vehicle.get("data_port", vehicle.get("udp_port"))
        lines.extend([
            f"  {vehicle['source_name']} -> {vehicle['airsim_name']}",
            f"    AirSim autopilot {data_protocol.upper()} target: {vehicle['autopilot_host']}:{data_port}",
            f"    AirSim control port: {vehicle['control_port']}",
            f"    MAVROS FCU URL: {vehicle['mavros_fcu_url']}",
        ])
    return "\n".join(lines)
