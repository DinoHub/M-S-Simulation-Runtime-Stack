"""ScenarioLab / ScenarioRunner runtime-spec helpers."""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any


DEFAULT_STATIC_OBSTACLE_CLASS = "/Script/ScenarioRuntime.BlockingBoxActor"

DEFAULT_PROCEDURAL_SPEC: dict[str, Any] = {
    "type": "fixed_3room",
    "room_count": 3,
    "room_min_size_m": [8, 8],
    "room_max_size_m": [12, 12],
    "corridor_width_m": 2.5,
    "door_width_m": 1.5,
    "room_height_m": 4.0,
    "guarantee_connectivity": True,
    "generate_corridor_walls": True,
    "generate_ceilings": False,
}

DEFAULT_START_GOAL_SPEC: dict[str, Any] = {
    "start_policy": "first_room",
    "goal_policy": "farthest_room",
    "min_distance_m": 8,
    "spawn_inset_m": 1.0,
}

DEFAULT_DYNAMIC_OBSTACLES_SPEC: dict[str, Any] = {
    "enabled": False,
    "behaviors": {},
}


@dataclass(frozen=True)
class ScenarioRuntimeConfig:
    enabled: bool = True
    world_mode: str = "procedural"
    map: str | None = None
    pcg_graph: str | None = None
    procedural: dict[str, Any] = field(default_factory=dict)
    start_goal: dict[str, Any] = field(default_factory=dict)
    dynamic_obstacles: dict[str, Any] = field(default_factory=dict)


def merge_dicts(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        existing = result.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            result[key] = merge_dicts(existing, value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def density_to_count(density: str, explicit_count: int | None) -> int:
    if explicit_count is not None:
        return max(0, explicit_count)
    return {
        "none": 0,
        "off": 0,
        "low": 6,
        "medium": 14,
        "high": 28,
        "dense": 45,
    }.get(density.lower(), 14)


def density_to_pcg(density: str) -> float:
    return {
        "none": 0.0,
        "off": 0.0,
        "low": 0.15,
        "medium": 0.35,
        "high": 0.65,
        "dense": 0.9,
    }.get(density.lower(), 0.35)


def build_scenario_runtime_spec(
    *,
    scenario_id: str,
    seed: int,
    config: ScenarioRuntimeConfig,
    clutter_enabled: bool,
    clutter_density: str,
    clutter_count: int | None,
    clutter_entries: list[dict[str, Any]],
) -> dict[str, Any]:
    obstacle_count = density_to_count(clutter_density, clutter_count) if clutter_enabled else 0
    entries = copy.deepcopy(clutter_entries)
    if not entries and obstacle_count > 0:
        entries = [{"class": DEFAULT_STATIC_OBSTACLE_CLASS, "count": obstacle_count}]
    if not clutter_enabled:
        entries = []

    world: dict[str, Any] = {"mode": config.world_mode}
    if config.map:
        world["map"] = config.map

    pcg: dict[str, Any] = {
        "density": density_to_pcg(clutter_density) if clutter_enabled else 0.0,
    }
    if config.pcg_graph:
        pcg["graph"] = config.pcg_graph

    dynamic_obstacles = merge_dicts(DEFAULT_DYNAMIC_OBSTACLES_SPEC, config.dynamic_obstacles)

    return {
        "id": scenario_id,
        "seed": seed,
        "world": world,
        "procedural": merge_dicts(DEFAULT_PROCEDURAL_SPEC, config.procedural),
        "start_goal": merge_dicts(DEFAULT_START_GOAL_SPEC, config.start_goal),
        "obstacles": {
            "blocking": {
                "entries": entries,
                "min_clearance_m": 1.2,
                "random_yaw": True,
                "spawn_inset_m": 0.8,
            },
        },
        "pcg": pcg,
        "dynamic_obstacles": dynamic_obstacles,
    }
