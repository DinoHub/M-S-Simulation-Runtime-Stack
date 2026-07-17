"""Render ScenarioRunner and object-clutter artifacts."""
from __future__ import annotations

import math
import random
import re
import shutil
from pathlib import Path
from typing import Any

from ..models import ObjectClutter, ResolvedScenario
from ..scenario_runtime import build_scenario_runtime_spec


def object_clutter_config(clutter: ObjectClutter) -> dict[str, Any]:
    return {
        "enabled": clutter.enabled,
        "backend": clutter.backend,
        "seed": clutter.seed,
        "density": clutter.density,
        "count": clutter.count,
        "placement": clutter.placement,
        "unreal": {
            "blueprint": clutter.blueprint,
            "data_table": clutter.data_table,
            "seed_property_candidates": ["rndSeed", "Rnd Seed", "Seed", "InitialSeed"],
        },
        "assets": clutter.assets,
        "entries": clutter.entries,
        "placements": clutter.placements,
        "asset_packs": clutter.asset_packs,
    }


ASSET_PACK_CONTAINER_ROOT = "/simrunner/asset-packs"
RUNTIME_SHADER_ARCHIVE_PREFIX = "ShaderArchive-"
RUNTIME_SHADER_ARCHIVE_SUFFIX = ".ushaderbytecode"
RUNTIME_SHADER_INFO_PREFIX = "ShaderAssetInfo-"
RUNTIME_SHADER_INFO_SUFFIX = ".assetinfo.json"


def _safe_path_name(value: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value.strip()).strip("-._")
    return name or "asset-pack"


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def scenario_plugin_required(resolved: ResolvedScenario) -> bool:
    if resolved.object_clutter.placements or resolved.random_spawns:
        return True
    if resolved.object_clutter.asset_packs:
        return True
    return any(isinstance(vehicle.runtime_asset_pack, dict) and vehicle.runtime_asset_pack for vehicle in resolved.vehicles)


LEGACY_RANDOM_SPAWN_IMAGE_MARKERS = ("safticity_ue55_unclassed-runtime-shaderfix",)


def uses_legacy_random_spawn_expansion(resolved: ResolvedScenario) -> bool:
    image = str(resolved.airsim_image).lower()
    return any(marker in image for marker in LEGACY_RANDOM_SPAWN_IMAGE_MARKERS)


def _expand_random_spawn_placements(random_spawns: list[dict[str, Any]], seed: int) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    placements: list[dict[str, Any]] = []
    for spawn in random_spawns:
        bounds = _mapping(spawn.get("bounds"))
        center = _mapping(bounds.get("center"))
        size = _mapping(bounds.get("size"))
        spawn_scale = _mapping(spawn.get("spawn_scale")) or {"x": 1.0, "y": 1.0, "z": 1.0}
        count = int(spawn.get("count", 0))
        inset = float(spawn.get("spawn_inset_m", 0.25))
        half_x = max(float(size.get("x", 0.001)) / 2.0 - inset, 0.0)
        half_y = max(float(size.get("y", 0.001)) / 2.0 - inset, 0.0)
        base_z = float(center.get("z", 0.0)) - (float(size.get("z", 0.0)) / 2.0)
        yaw_rad = math.radians(float(bounds.get("yaw", 0.0)))
        cos_yaw = math.cos(yaw_rad)
        sin_yaw = math.sin(yaw_rad)
        base_name = str(spawn.get("name") or spawn.get("id") or "random_spawn")

        for index in range(1, count + 1):
            local_x = rng.uniform(-half_x, half_x)
            local_y = rng.uniform(-half_y, half_y)
            world_x = float(center.get("x", 0.0)) + local_x * cos_yaw - local_y * sin_yaw
            world_y = float(center.get("y", 0.0)) + local_x * sin_yaw + local_y * cos_yaw
            actor_yaw = rng.uniform(0.0, 360.0) if bool(spawn.get("random_yaw", True)) else float(bounds.get("yaw", 0.0))
            placements.append({
                "id": f"{spawn.get('id', 'random_spawn')}_{index:02d}",
                "name": f"{base_name}_{index:02d}",
                "source": "ScenarioSpec.random_spawns.expanded_for_legacy_plugin",
                "asset_pack": str(spawn.get("asset_pack") or "scenario_runtime_basic"),
                "asset": str(spawn.get("asset") or spawn.get("asset_id") or spawn.get("id") or "asset"),
                "class": str(spawn.get("class") or spawn.get("class_path") or "/Script/Engine.StaticMeshActor"),
                "unreal_static_mesh": str(spawn.get("unreal_static_mesh") or ""),
                "actor_label": f"{base_name}_{index:02d}",
                "transform": {
                    "position": {
                        "x": round(world_x, 3),
                        "y": round(world_y, 3),
                        "z": round(base_z, 3),
                    },
                    "rotation": {
                        "pitch": 0.0,
                        "roll": 0.0,
                        "yaw": round(actor_yaw, 3),
                    },
                    "scale": {
                        "x": float(spawn_scale.get("x", 1.0)),
                        "y": float(spawn_scale.get("y", 1.0)),
                        "z": float(spawn_scale.get("z", 1.0)),
                    },
                    "frame": str(bounds.get("frame") or "ros2_flu"),
                },
            })
    return placements


def _runtime_source_pak(pack: dict[str, Any]) -> str:
    runtime = _mapping(pack.get("runtime"))
    for key in ("local_pak_path", "source_pak", "host_path", "pak_path"):
        value = runtime.get(key)
        if value and str(value) != "__self__":
            return str(value)

    raw_files = runtime.get("files") or runtime.get("bundle_files") or runtime.get("sidecar_files")
    if isinstance(raw_files, list):
        for item in raw_files:
            raw_path = item.get("path") if isinstance(item, dict) else item
            if raw_path is None or str(raw_path) == "__self__":
                continue
            if Path(str(raw_path)).suffix.lower() == ".pak":
                return str(raw_path)

    editor = _mapping(pack.get("editor"))
    value = editor.get("pak_path")
    if value and str(value) != "__self__":
        return str(value)
    return ""


def _resolve_source_file(raw: str, resolved: ResolvedScenario) -> Path | None:
    if not raw or raw.startswith(ASSET_PACK_CONTAINER_ROOT):
        return None
    path = Path(raw).expanduser()
    candidates = [path] if path.is_absolute() else [
        resolved.source_root / "ScenarioBundle" / path,
        resolved.source_root / path,
        Path.cwd() / path,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def _resolve_source_pak(raw: str, resolved: ResolvedScenario) -> Path | None:
    return _resolve_source_file(raw, resolved)


def _runtime_source_files(pack: dict[str, Any], source_pak: Path | None, resolved: ResolvedScenario) -> list[Path]:
    runtime = _mapping(pack.get("runtime"))
    files: list[Path] = []
    seen: set[Path] = set()

    def add(path: Path | None) -> None:
        if path and path not in seen:
            files.append(path)
            seen.add(path)

    add(source_pak)

    raw_files = runtime.get("files") or runtime.get("bundle_files") or runtime.get("sidecar_files")
    if isinstance(raw_files, list):
        for item in raw_files:
            raw_path = item.get("path") if isinstance(item, dict) else item
            if raw_path is None or str(raw_path) == "__self__":
                continue
            add(_resolve_source_file(str(raw_path), resolved))

    if source_pak:
        for extension in (".utoc", ".ucas", ".sig"):
            add(_resolve_source_file(str(source_pak.with_suffix(extension)), resolved))
        for sibling in sorted(source_pak.parent.iterdir(), key=lambda item: item.name):
            name = sibling.name
            if (
                sibling.is_file()
                and (
                    name.startswith(RUNTIME_SHADER_ARCHIVE_PREFIX) and name.endswith(RUNTIME_SHADER_ARCHIVE_SUFFIX)
                    or name.startswith(RUNTIME_SHADER_INFO_PREFIX) and name.endswith(RUNTIME_SHADER_INFO_SUFFIX)
                )
            ):
                add(sibling.resolve())

    return files


def _asset_pack_for_runtime(pack: dict[str, Any], resolved: ResolvedScenario, run_dir: Path | None) -> dict[str, Any]:
    entry = dict(pack)
    pack_id = str(entry.get("id") or entry.get("pack_id") or "asset_pack")
    runtime = _mapping(entry.get("runtime"))
    editor = _mapping(entry.get("editor"))
    source_pak = _resolve_source_pak(_runtime_source_pak(entry), resolved)
    source_files = _runtime_source_files(entry, source_pak, resolved)

    if source_pak and run_dir is not None:
        pack_dir = run_dir / "config" / "asset-packs" / _safe_path_name(pack_id)
        pack_dir.mkdir(parents=True, exist_ok=True)
        copied: list[tuple[Path, Path]] = []
        for source_file in source_files:
            target_file = pack_dir / source_file.name
            if source_file != target_file.resolve():
                shutil.copy2(source_file, target_file)
            copied.append((source_file, target_file))

        primary_target = pack_dir / source_pak.name

        runtime.setdefault("bundle_format", "unreal_pak")
        runtime.setdefault("content_mount", editor.get("content_mount") or "/ScenarioAssets/")
        runtime["pak_path"] = f"{ASSET_PACK_CONTAINER_ROOT}/{_safe_path_name(pack_id)}/{primary_target.name}"
        runtime["stack_pak_path"] = str(primary_target.relative_to(run_dir))
        if len(copied) > 1:
            runtime["files"] = [
                f"{ASSET_PACK_CONTAINER_ROOT}/{_safe_path_name(pack_id)}/{target_file.name}"
                for _, target_file in copied
            ]
            runtime["stack_files"] = [str(target_file.relative_to(run_dir)) for _, target_file in copied]
    elif runtime:
        runtime.setdefault("content_mount", editor.get("content_mount") or "/ScenarioAssets/")

    if runtime:
        entry["runtime"] = runtime
    return entry


def scenario_plugin_config(resolved: ResolvedScenario, run_dir: Path | None = None) -> dict[str, Any]:
    placements = []
    for placement in resolved.object_clutter.placements:
        transform = dict(placement.get("transform")) if isinstance(placement.get("transform"), dict) else {}
        transform.setdefault("frame", str(placement.get("frame") or "airsim_meters"))
        placements.append({
            "id": str(placement.get("id") or placement.get("asset") or "object"),
            "name": str(placement.get("name") or placement.get("actor_label") or placement.get("id") or placement.get("asset") or "object"),
            "source": str(placement.get("source") or "ScenarioSpec.objects"),
            "asset_pack": str(placement.get("asset_pack") or "scenario_runtime_basic"),
            "asset": str(placement.get("asset") or placement.get("asset_id") or placement.get("id") or "asset"),
            "class": str(placement.get("class") or placement.get("class_path") or "/Script/Engine.StaticMeshActor"),
            "unreal_static_mesh": str(placement.get("unreal_static_mesh") or ""),
            "actor_label": str(placement.get("actor_label") or placement.get("name") or ""),
            "transform": transform,
        })

    random_spawns = []
    random_spawn_pack_manifests: dict[str, dict[str, Any]] = {}
    for spawn in resolved.random_spawns:
        item = {key: value for key, value in spawn.items() if not key.startswith("_")}
        item["asset_pack"] = str(item.get("asset_pack") or "scenario_runtime_basic")
        item["asset"] = str(item.get("asset") or item.get("asset_id") or item.get("id") or "asset")
        item["class"] = str(item.get("class") or item.get("class_path") or "/Script/Engine.StaticMeshActor")
        random_spawns.append(item)
        manifest = spawn.get("_asset_pack_manifest")
        if isinstance(manifest, dict):
            random_spawn_pack_manifests[item["asset_pack"]] = dict(manifest)

    legacy_random_spawns = []
    expanded_random_spawn_count = 0
    if uses_legacy_random_spawn_expansion(resolved) and random_spawns:
        legacy_random_spawns = [dict(item) for item in random_spawns]
        expanded_random_spawn_placements = _expand_random_spawn_placements(random_spawns, resolved.object_clutter.seed)
        expanded_random_spawn_count = len(expanded_random_spawn_placements)
        placements.extend(expanded_random_spawn_placements)
        random_spawns = []

    pack_ids = {item["asset_pack"] for item in placements}
    pack_ids.update(item["asset_pack"] for item in random_spawns)
    known_packs = {
        str(pack.get("id") or pack.get("pack_id")): dict(pack)
        for pack in resolved.object_clutter.asset_packs
        if isinstance(pack, dict) and (pack.get("id") or pack.get("pack_id"))
    }
    known_packs.update(random_spawn_pack_manifests)
    for vehicle in resolved.vehicles:
        pack = vehicle.runtime_asset_pack
        if not isinstance(pack, dict):
            continue
        pack_id = str(pack.get("id") or pack.get("pack_id") or "")
        if not pack_id:
            continue
        pack_ids.add(pack_id)
        known_packs[pack_id] = dict(pack)

    asset_packs = []
    for pack_id in sorted(pack_ids):
        pack = known_packs.get(pack_id, {"id": pack_id, "display_name": pack_id})
        asset_packs.append(_asset_pack_for_runtime(pack, resolved, run_dir))

    config = {
        "schema": "mns.scenario_plugin.v1",
        "scenario_id": resolved.name,
        "stack_name": resolved.stack_name,
        "environment": {
            "name": resolved.environment_name,
            "level_id": resolved.runtime_artifact.get("level_id"),
            "map": resolved.runtime_artifact.get("map") or resolved.scenario_runtime.map,
            "pak_id": resolved.runtime_artifact.get("pak_id"),
            "pak_name": resolved.runtime_artifact.get("pak_name"),
            "runtime_boundary": "environment actors are immutable; only ScenarioPlugin objects are runtime-spawned",
        },
        "asset_packs": asset_packs,
        "objects": placements,
        "random_spawns": random_spawns,
        "runtime": {
            "spawn_policy": "explicit_authored_placements_and_random_spawns",
            "seed": resolved.object_clutter.seed,
            "position_units": "meters",
            "default_frame": "airsim_meters",
            "rotation_units": "degrees",
            "scale_units": "unreal_actor_scale",
        },
    }
    if legacy_random_spawns:
        config["legacy_random_spawns"] = legacy_random_spawns
        config["runtime"]["spawn_policy"] = "legacy_explicit_objects_from_random_spawns"
        config["runtime"]["expanded_random_spawn_count"] = expanded_random_spawn_count
    return config


def scenario_conditions_spec(resolved: ResolvedScenario) -> dict[str, Any]:
    return {
        "schema": "mns.scenario_conditions.v1",
        "scenario_id": resolved.name,
        "stack_name": resolved.stack_name,
        "environment": resolved.environment_name,
        "conditions": dict(resolved.conditions or {}),
    }


def scenario_runtime_spec(resolved: ResolvedScenario) -> dict[str, Any]:
    clutter = resolved.object_clutter
    return build_scenario_runtime_spec(
        scenario_id=resolved.name,
        seed=clutter.seed,
        config=resolved.scenario_runtime,
        clutter_enabled=clutter.enabled,
        clutter_density=clutter.density,
        clutter_count=clutter.count,
        clutter_entries=clutter.entries,
    )


def docker_scenario_args(resolved: ResolvedScenario) -> list[str]:
    args: list[str] = []
    if resolved.scenario_runtime.enabled and not uses_legacy_random_spawn_expansion(resolved):
        args.extend([
            "-ScenarioPath=/simrunner/scenario_runtime.json",
            f"-startSeed={resolved.object_clutter.seed}",
        ])
    elif uses_legacy_random_spawn_expansion(resolved):
        args.append(f"-startSeed={resolved.object_clutter.seed}")
    args.append("-MnSScenarioPluginConfig=/simrunner/scenario_plugin.json")
    if resolved.conditions:
        args.append("-MnSScenarioConditions=/simrunner/scenario_conditions.json")
    args.extend([
        "-SimObjectClutterConfig=/simrunner/object_clutter.yaml",
        f"-SimObjectClutterSeed={resolved.object_clutter.seed}",
        f"-SimObjectClutterDensity={resolved.object_clutter.density}",
    ])
    return args


def editor_launch_args(resolved: ResolvedScenario, run_dir: Path) -> list[str]:
    scenario_dir = run_dir / "config" / "scenario"
    scenario_plugin_dir = run_dir / "config" / "scenario-plugin"
    args: list[str] = []
    if resolved.scenario_runtime.enabled and not uses_legacy_random_spawn_expansion(resolved):
        args.extend([
            f"-ScenarioPath={scenario_dir / 'scenario_runtime.json'}",
            f"-startSeed={resolved.object_clutter.seed}",
        ])
    elif uses_legacy_random_spawn_expansion(resolved):
        args.append(f"-startSeed={resolved.object_clutter.seed}")
    args.append(f"-MnSScenarioPluginConfig={scenario_plugin_dir / 'scenario_plugin.json'}")
    if resolved.conditions:
        args.append(f"-MnSScenarioConditions={scenario_dir / 'scenario_conditions.json'}")
    args.extend([
        f"-SimObjectClutterConfig={scenario_dir / 'object_clutter.yaml'}",
        f"-SimObjectClutterSeed={resolved.object_clutter.seed}",
        f"-SimObjectClutterDensity={resolved.object_clutter.density}",
    ])
    return args
