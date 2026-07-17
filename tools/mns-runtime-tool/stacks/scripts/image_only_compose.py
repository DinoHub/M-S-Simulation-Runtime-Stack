#!/usr/bin/env python3
"""Normalize generated ScenarioSpec stacks for runtime-stack users.

The integration stack generator can emit build contexts for developer checkouts.
This runtime-stack wrapper is user-facing and must run from images only, so this
script removes build contexts and converts build pull policies to image pulls.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

IMAGE_REPLACEMENTS = {
    "${ARDUPILOT_IMAGE:-local/auto_mns:ardupilot-latest}": "${ARDUPILOT_IMAGE:-dhdevspace/auto_mns:ardupilot-slim}",
    "${PX4_IMAGE:-local/auto_mns:px4-airsim-px4}": "${PX4_IMAGE:-dhdevspace/auto_mns:px4-airsim-px4}",
    "${ROS2_IMAGE:-local/auto_mns:tevv-airsim-ros2-bridge-humble}": "${ROS2_IMAGE:-dhdevspace/auto_mns:airsim-ros2-bridge}",
    "local/auto_mns:airsim-qgc-x11-latest": "${QGC_IMAGE:-dhdevspace/auto_mns:airsim-qgc-x11-latest}",
}


def normalize_image(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    return IMAGE_REPLACEMENTS.get(value, value)


def local_default_images(data: dict[str, Any]) -> list[str]:
    results: list[str] = []
    for service_name, service in (data.get("services") or {}).items():
        if not isinstance(service, dict):
            continue
        image = service.get("image")
        if isinstance(image, str) and "local/" in image:
            results.append(f"{service_name}: {image}")
    return results


def sanitize(stack_dir: Path) -> int:
    compose_path = stack_dir / "docker-compose.yml"
    if not compose_path.is_file():
        print(f"ERROR: missing compose file: {compose_path}", file=sys.stderr)
        return 2

    data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        print(f"ERROR: compose root must be a mapping: {compose_path}", file=sys.stderr)
        return 2

    services = data.get("services")
    if not isinstance(services, dict):
        print(f"ERROR: compose has no services mapping: {compose_path}", file=sys.stderr)
        return 2

    stripped: list[str] = []
    for service_name, service in services.items():
        if not isinstance(service, dict):
            continue
        if "build" in service:
            service.pop("build", None)
            stripped.append(str(service_name))
        if service.get("pull_policy") == "build":
            service["pull_policy"] = "if_not_present"
        if "image" in service:
            service["image"] = normalize_image(service["image"])

    remaining_builds = [name for name, service in services.items() if isinstance(service, dict) and "build" in service]
    if remaining_builds:
        print("ERROR: generated stack still contains build contexts:", file=sys.stderr)
        for name in remaining_builds:
            print(f"  {name}", file=sys.stderr)
        return 2

    compose_path.write_text(yaml.safe_dump(data, sort_keys=False, default_flow_style=False), encoding="utf-8")
    (stack_dir / ".mns-image-only").write_text("schema=mns.generated_stack.image_only.v1\n", encoding="utf-8")

    if stripped:
        print("Image-only stack: removed build contexts from " + ", ".join(stripped))
    local_images = local_default_images(data)
    if local_images:
        print("WARNING: stack still references local image tags; override or push these tags for other hosts:", file=sys.stderr)
        for item in local_images:
            print(f"  {item}", file=sys.stderr)
    return 0


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] in {"-h", "--help"}:
        print("Usage: image_only_compose.py <generated-stack-dir>")
        return 0 if len(argv) == 2 else 2
    return sanitize(Path(argv[1]).expanduser().resolve())


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
