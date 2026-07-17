"""Render generated stack manifests and connection contracts."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..autopilots import get_autopilot_profile
from ..autopilots.base import AIRSIM_SERVICE_NAME
from ..models import ObjectClutter, ResolvedScenario
from ..provenance import generation_provenance
from ..scenario_runtime import density_to_pcg
from .metrics import metrics_manifest
from .scenario_artifacts import scenario_plugin_required


def object_clutter_manifest(clutter: ObjectClutter) -> dict[str, Any]:
    return {
        "enabled": clutter.enabled,
        "backend": clutter.backend,
        "seed": clutter.seed,
        "density": clutter.density,
        "count": clutter.count,
        "placement": clutter.placement,
        "blueprint": clutter.blueprint,
        "data_table": clutter.data_table,
        "assets": clutter.assets,
        "entries": clutter.entries,
        "placements": clutter.placements,
        "asset_packs": clutter.asset_packs,
        "mounted_yaml": "/simrunner/object_clutter.yaml",
        "mounted_json": "/simrunner/object_clutter.json",
        "unreal_args": {
            "SimObjectClutterConfig": "/simrunner/object_clutter.yaml",
            "SimObjectClutterSeed": clutter.seed,
            "SimObjectClutterDensity": clutter.density,
        },
        "environment": {
            "SIM_OBJECT_CLUTTER_ENABLED": "true" if clutter.enabled else "false",
            "SIM_OBJECT_CLUTTER_CONFIG": "/simrunner/object_clutter.yaml",
            "SIM_OBJECT_CLUTTER_SEED": str(clutter.seed),
            "SIM_OBJECT_CLUTTER_DENSITY": clutter.density,
            "XFS_CONTAINER_SPAWNER_SEED": str(clutter.seed),
        },
    }


def manifest(resolved: ResolvedScenario) -> dict[str, Any]:
    autopilot_profile = get_autopilot_profile(resolved.autopilot_type)
    scenario_plugin_asset_packs = list(resolved.object_clutter.asset_packs)
    seen_pack_ids = {
        str(pack.get("id") or pack.get("pack_id"))
        for pack in scenario_plugin_asset_packs
        if isinstance(pack, dict) and (pack.get("id") or pack.get("pack_id"))
    }
    for spawn in resolved.random_spawns:
        pack = spawn.get("_asset_pack_manifest")
        if not isinstance(pack, dict):
            continue
        pack_id = str(pack.get("id") or pack.get("pack_id") or "")
        if not pack_id or pack_id in seen_pack_ids:
            continue
        scenario_plugin_asset_packs.append(dict(pack))
        seen_pack_ids.add(pack_id)
    for vehicle in resolved.vehicles:
        pack = vehicle.runtime_asset_pack
        if not isinstance(pack, dict):
            continue
        pack_id = str(pack.get("id") or pack.get("pack_id") or "")
        if not pack_id or pack_id in seen_pack_ids:
            continue
        scenario_plugin_asset_packs.append(dict(pack))
        seen_pack_ids.add(pack_id)

    plugin_required = scenario_plugin_required(resolved)

    return {
        **generation_provenance(),
        "scenario_id": resolved.scenario_id,
        "name": resolved.name,
        "stack_name": resolved.stack_name,
        "runtime_profile": resolved.runtime_profile,
        "environment": resolved.environment_name,
        "autopilot": {
            "type": resolved.autopilot_type,
            "managed": resolved.autopilot_managed,
            "endpoint": resolved.autopilot_endpoint,
            "hostname_prefix": resolved.autopilot_hostname_prefix,
        },
        "features": {
            "qgroundcontrol": resolved.qgroundcontrol,
            "ros2_bridge": resolved.ros2_bridge,
            "mavros": resolved.bridge_mavros,
        },
        "airsim_rpc": {"host": "localhost", "port": 41451},
        "airsim": {
            "mode": "unreal_editor_host" if resolved.runtime_profile == "editor" else "docker_container",
            "image": resolved.airsim_image,
            "executable": resolved.airsim_executable,
            "settings_json": "config/unreal-airsim/settings.json",
            "container": None if resolved.runtime_profile == "editor" else f"{resolved.stack_name}-{AIRSIM_SERVICE_NAME}",
        },
        "runtime_artifact": resolved.runtime_artifact,
        "metrics": metrics_manifest(resolved, Path(".")),
        "object_clutter": object_clutter_manifest(resolved.object_clutter),
        "scenario_plugin": {
            "enabled": plugin_required,
            "object_count": len(resolved.object_clutter.placements),
            "random_spawn_volume_count": len(resolved.random_spawns),
            "random_spawn_requested_count": sum(int(item.get("count", 0)) for item in resolved.random_spawns),
            "asset_packs": scenario_plugin_asset_packs,
            "mounted_json": "/simrunner/scenario_plugin.json",
            "unreal_args": {
                "MnSScenarioPluginConfig": "/simrunner/scenario_plugin.json",
            },
            "environment": {
                "SCENARIO_PLUGIN_CONFIG": "/simrunner/scenario_plugin.json",
                "SCENARIO_PLUGIN_ENABLED": "true" if plugin_required else "false",
            },
        },
        "scenario_conditions": {
            "enabled": bool(resolved.conditions),
            "mounted_json": "/simrunner/scenario_conditions.json",
            "conditions": resolved.conditions,
            "unreal_args": {
                "MnSScenarioConditions": "/simrunner/scenario_conditions.json",
            } if resolved.conditions else {},
            "environment": {
                "SCENARIO_CONDITIONS_CONFIG": "/simrunner/scenario_conditions.json",
                "SCENARIO_CONDITIONS_ENABLED": "true" if resolved.conditions else "false",
            },
        },
        "scenario_runtime": {
            "enabled": resolved.scenario_runtime.enabled,
            "mounted_json": "/simrunner/scenario_runtime.json",
            "world": {
                "mode": resolved.scenario_runtime.world_mode,
                "map": resolved.scenario_runtime.map,
            },
            "pcg": {
                "graph": resolved.scenario_runtime.pcg_graph,
                "density": density_to_pcg(resolved.object_clutter.density) if resolved.object_clutter.enabled else 0.0,
            },
            "command_line": {
                "ScenarioPath": "/simrunner/scenario_runtime.json",
                "startSeed": resolved.object_clutter.seed,
            },
            "requires_plugins": [
                "ScenarioRunner",
                "ScenarioProcedural",
                "ScenarioObstacles",
                "ScenarioDynamic",
            ],
        },
        "vehicles": [
            {
                "source_name": v.source_name,
                "airsim_name": v.runtime_name,
                "index": v.index,
                "vehicle_type": v.vehicle_type,
                "pawn_path": v.pawn_path,
                "vehicle_model_pack": (v.runtime_asset_pack or {}).get("id") if v.runtime_asset_pack else None,
                "ros_domain_id": v.ros_domain_id,
                "autopilot_host": v.autopilot_host,
                "data_protocol": v.connection.data_protocol,
                "data_port": v.connection.data_port,
                autopilot_profile.data_port_manifest_key: v.connection.data_port,
                "control_port": v.control_port,
                "mavros_protocol": v.connection.mavros_protocol,
                f"mavros_{v.connection.mavros_protocol}_port": v.connection.mavros_port,
                "mavros_fcu_url": v.mavros_fcu_url,
                "sensors": list(v.sensors.keys()),
                "cameras": list(v.cameras.keys()),
                "bridge_container": f"{resolved.stack_name}-airsim-bridge-{autopilot_profile.bridge_suffix(v)}",
                "autopilot_container": autopilot_profile.container_name(resolved, v) if resolved.autopilot_managed else None,
            }
            for v in resolved.vehicles
        ],
    }


def scenario_artifact_manifest(resolved: ResolvedScenario, run_dir: Path) -> dict[str, Any]:
    scenario_dir = run_dir / "config" / "scenario"
    data = manifest(resolved)
    data["scenario_artifacts"] = {
        "scenario_runtime_json": str(scenario_dir / "scenario_runtime.json"),
        "scenario_conditions_json": str(scenario_dir / "scenario_conditions.json"),
        "object_clutter_yaml": str(scenario_dir / "object_clutter.yaml"),
        "object_clutter_json": str(scenario_dir / "object_clutter.json"),
        "scenario_plugin_json": str(run_dir / "config" / "scenario-plugin" / "scenario_plugin.json"),
        "docker_launch_args": str(run_dir / "scenario-docker-args.txt"),
        "editor_launch_args": str(run_dir / "editor-launch-args.txt") if resolved.runtime_profile == "editor" else None,
    }
    return data


def execution_context(resolved: ResolvedScenario, run_dir: Path) -> dict[str, Any]:
    data = manifest(resolved)
    data["schema"] = "mns.execution_context.v1"
    data["run_dir"] = str(run_dir)
    data["artifacts"] = {
        "docker_compose": str(run_dir / "docker-compose.yml"),
        "env": str(run_dir / ".env"),
        "generated_manifest": str(run_dir / "generated-manifest.json"),
        "airsim_settings": str(run_dir / "config" / "unreal-airsim" / "settings.json"),
        "scenario_runtime": str(run_dir / "config" / "scenario" / "scenario_runtime.json"),
        "scenario_conditions_json": str(run_dir / "config" / "scenario" / "scenario_conditions.json"),
        "object_clutter_yaml": str(run_dir / "config" / "scenario" / "object_clutter.yaml"),
        "object_clutter_json": str(run_dir / "config" / "scenario" / "object_clutter.json"),
        "scenario_plugin_json": str(run_dir / "config" / "scenario-plugin" / "scenario_plugin.json"),
        "metrics_runtime": str(run_dir / "config" / "metrics" / "metrics_runtime.json"),
        "metrics_dir": str(run_dir / "outputs" / "metrics"),
    }
    data["source"] = {
        "root": str(resolved.source_root),
        "files": [str(path) for path in resolved.source_files],
    }
    return data
