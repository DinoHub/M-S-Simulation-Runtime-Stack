#!/usr/bin/env python3
"""Warn before flying a vehicle that spawns where the level has no floor.

    tools/check_spawn.py SCENARIO                  # a ScenarioSpec.yaml or its folder
    tools/check_spawn.py campaign run NAME [...]   # the campaign's base scenario
                                                   # (and each variant's level)

`make fly` and `make campaign` run it first. It reads packs/level-spawn-hints.json
(what has been measured about a published level) and prints a WARNING, with the
known-good start, for each vehicle that starts within a metre of the Unreal
world origin on a level whose origin has nothing under it. On XFS such a
vehicle falls through the world at ~8 m/s and PX4 refuses to arm ("vertical
velocity unstable"), which otherwise shows up minutes later as a failed run.

Advisory only: it always exits 0, and says nothing when it cannot tell (no
PyYAML, a spec it cannot read, a level with no hints, or a scenario whose
coordinate_frame.origin is an explicit non-zero pose, e.g. a PlayerStart that
ScenarioLab recorded).
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
HINTS_FILE = ROOT / "packs" / "level-spawn-hints.json"
# Horizontal distance (m) from the world origin that counts as "at the origin".
NEAR_ORIGIN_M = 1.0
CAMPAIGN_SUBCOMMANDS = {"run", "preflight", "validate", "plan"}

try:
    import yaml
except ImportError:  # pragma: no cover - the host has no PyYAML: say nothing
    yaml = None


def _load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        elif isinstance(value, list) and isinstance(out.get(key), list):
            out[key] = out[key] + value
        else:
            out[key] = value
    return out


def _include(base_dir: Path, section: str, ref: Any) -> dict[str, Any]:
    """One `includes:` entry as a fragment of the document. Covers the forms
    ScenarioLab exports (a file per section, a folder of vehicle files); the
    contract's loader is the authority, this only has to find vehicles and the
    environment."""
    if isinstance(ref, list):
        merged: dict[str, Any] = {}
        for item in ref:
            merged = _merge(merged, _include(base_dir, section, item))
        return merged
    if isinstance(ref, dict):
        return ref if section in ref else {section: ref}
    path = base_dir / str(ref)
    if path.is_dir():
        docs = [_load_yaml(p) for p in sorted(path.iterdir()) if p.suffix in (".yaml", ".yml")]
        docs = [d for d in docs if d is not None]
        if section == "vehicles":
            items: list[Any] = []
            for d in docs:
                if isinstance(d, list):
                    items.extend(d)
                elif isinstance(d, dict):
                    items.extend(d["vehicles"] if isinstance(d.get("vehicles"), list) else [d])
            return {"vehicles": items}
        merged = {}
        for d in docs:
            if isinstance(d, dict):
                merged = _merge(merged, d if section in d else {section: d})
        return merged
    doc = _load_yaml(path)
    if isinstance(doc, dict) and section in doc:
        return doc
    return {section: doc}


def load_scenario(path: Path) -> tuple[Path, dict[str, Any]]:
    spec_path = path / "ScenarioSpec.yaml" if path.is_dir() else path
    doc = _load_yaml(spec_path) or {}
    includes = doc.pop("includes", None)
    if isinstance(includes, dict):
        for section, ref in includes.items():
            doc = _merge(doc, _include(spec_path.parent, str(section), ref))
    elif isinstance(includes, list):
        for ref in includes:
            frag = _load_yaml(spec_path.parent / str(ref))
            if isinstance(frag, dict):
                doc = _merge(doc, frag)
    return spec_path, doc


def _number(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(value)


def origin_is_identity(doc: dict[str, Any]) -> bool:
    frame = doc.get("coordinate_frame")
    origin = frame.get("origin") if isinstance(frame, dict) else None
    if not isinstance(origin, dict):
        return True  # absent or a legacy string: the generator resolves it to identity
    position = origin.get("position") or {}
    return all(abs(_number(position.get(axis))) < 1e-6 for axis in ("x", "y", "z"))


def load_hints(path: Path = HINTS_FILE) -> dict[str, Any]:
    try:
        return (json.loads(path.read_text(encoding="utf-8")).get("levels") or {})
    except (OSError, ValueError):
        return {}


def check(doc: dict[str, Any], hints: dict[str, Any], label: str) -> list[str]:
    env = doc.get("environment") or {}
    level_id = str(env.get("id") or "")
    hint = hints.get(level_id)
    if not isinstance(hint, dict) or hint.get("origin_has_floor", True):
        return []
    versions = hint.get("versions")
    version = str(env.get("version") or "")
    if versions and version and version not in versions:
        return []
    if not origin_is_identity(doc):
        return []
    known = hint.get("known_start") or {}
    where = hint.get("known_start_name") or "a measured start"
    suggestion = "start: {x: %s, y: %s, z: %s}" % (known.get("x"), known.get("y"), known.get("z"))
    used_by = hint.get("used_by") or []
    example = f"as {used_by[0]} does" if used_by else "measured on this level"
    warnings = []
    for vehicle in doc.get("vehicles") or []:
        if not isinstance(vehicle, dict):
            continue
        start = vehicle.get("start") or {}
        x, y = _number(start.get("x")), _number(start.get("y"))
        if math.hypot(x, y) >= NEAR_ORIGIN_M:
            continue
        name = vehicle.get("id") or vehicle.get("name") or "a vehicle"
        warnings.append(
            f"WARNING: {label}: vehicle {name} starts at ({x:g}, {y:g}), the world origin of "
            f"{level_id}{'@' + version if version else ''}, which has no floor there: it will fall "
            f"through the level and the autopilot will refuse to arm.\n"
            f"         Fix: use the measured start ({where}), {suggestion} (AirSim NED metres, +z down), "
            f"{example}, or set coordinate_frame.origin to a pose over the "
            f"level's floor. Measured values: {HINTS_FILE.relative_to(ROOT)}.")
    return warnings


def find_campaign(name: str, cwd: Path) -> Path | None:
    for candidate in (Path(name), cwd / name, ROOT / "scenarios" / name):
        if candidate.is_file():
            return candidate
        if (candidate / "CampaignSpec.yaml").is_file():
            return candidate / "CampaignSpec.yaml"
    return None


def check_campaign(argv: list[str], hints: dict[str, Any], cwd: Path) -> list[str]:
    if not argv or argv[0] not in CAMPAIGN_SUBCOMMANDS:
        return []
    names = [a for a in argv[1:] if not a.startswith("-")]
    if not names:
        return []
    campaign_file = find_campaign(names[0], cwd)
    if campaign_file is None:
        return []
    campaign = _load_yaml(campaign_file) or {}
    scenario_ref = campaign.get("scenario")
    if not scenario_ref:
        return []
    _, base = load_scenario((campaign_file.parent / str(scenario_ref)).resolve())
    label = f"campaign {campaign.get('id') or names[0]}"
    warnings = check(base, hints, label)
    seen = {str((base.get("environment") or {}).get("id"))}
    for variant in campaign.get("variants") or []:
        overrides = (variant or {}).get("overrides") or {}
        if not isinstance(overrides, dict):
            continue
        merged = _merge(base, {k: v for k, v in overrides.items() if k in ("environment", "coordinate_frame")})
        level = str((merged.get("environment") or {}).get("id"))
        if level in seen:
            continue
        seen.add(level)
        warnings += check(merged, hints, f"{label} variant {variant.get('id')}")
    return warnings


def main(argv: list[str]) -> int:
    if yaml is None or not argv or argv[0] in ("-h", "--help"):
        if argv and argv[0] in ("-h", "--help"):
            print(__doc__.strip())
        return 0
    hints = load_hints()
    if not hints:
        return 0
    try:
        if argv[0] == "campaign":
            warnings = check_campaign(argv[1:], hints, Path.cwd())
        else:
            spec_path, doc = load_scenario(Path(argv[0]))
            label = spec_path.parent.name if spec_path.name == "ScenarioSpec.yaml" else spec_path.name
            warnings = check(doc, hints, label)
    except (OSError, yaml.YAMLError, AttributeError, TypeError):
        return 0  # not ours to report: generate and validate say what is wrong
    for line in warnings:
        print(line, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
