#!/usr/bin/env python3
"""Check engine/image/pack provenance before browser E2E; never claim E2E success."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMAGE_ROLES = ("product_shell", "authoring", "stack_generator", "runtime_host",
               "ros2_bridge", "dashboard_backend", "dashboard_frontend")
HOST_LABEL = "tevv.content_packs.host_compatibility_id"
PIN = re.compile(r"[^\s@]+@sha256:[0-9a-f]{64}")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


class CandidateError(ValueError):
    pass


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def docker_json(*arguments: str):
    result = subprocess.run(["docker", *arguments], check=True, text=True, capture_output=True)
    return json.loads(result.stdout)


def host_id_from_engine(engine_root: Path, expected_version: str) -> str:
    build = read_json(engine_root / "Engine/Build/Build.version")
    fields = ("MajorVersion", "MinorVersion", "PatchVersion", "Changelist")
    if not isinstance(build, dict) or any(type(build.get(key)) is not int for key in fields):
        raise CandidateError("Build.version must contain integer version fields and Changelist")
    actual = ".".join(str(build[key]) for key in fields[:3])
    if actual != expected_version or build["Changelist"] <= 0:
        raise CandidateError(f"engine is {actual} CL{build['Changelist']}; expected {expected_version} with a release changelist")
    return f"ue-{actual}-cl{build['Changelist']}-linux-development-vulkan-sm6-iostore-v2"


def validate_lock(lock: dict, host_id: str) -> None:
    if not isinstance(lock, dict) or lock.get("schema") != "mns.pack_release_lock.v1":
        raise CandidateError("expected mns.pack_release_lock.v1 candidate lock")
    if lock.get("capability_id") != host_id:
        raise CandidateError(f"pack lock capability must be {host_id}; got {lock.get('capability_id')}")
    images = lock.get("required_images")
    if not isinstance(images, dict):
        raise CandidateError("candidate lock must declare required_images")
    for role in IMAGE_ROLES:
        if not isinstance(images.get(role), str) or not PIN.fullmatch(images[role]):
            raise CandidateError(f"required_images.{role} must be pinned by registry digest")
    # Additional components (for example an autopilot) must also be pinned.
    for role, reference in images.items():
        if not isinstance(reference, str) or not PIN.fullmatch(reference):
            raise CandidateError(f"required_images.{role} must be pinned by registry digest")
    packs = lock.get("packs")
    if not isinstance(packs, list) or not packs:
        raise CandidateError("candidate lock must include the packs used by this test")
    identities = set()
    for pack in packs:
        if not isinstance(pack, dict) or pack.get("kind") not in ("level", "asset"):
            raise CandidateError("candidate pack must declare level or asset kind")
        if any(not isinstance(pack.get(key), str) or not pack[key] for key in ("id", "version", "artifact_digest")):
            raise CandidateError("candidate pack must declare id, version and artifact_digest")
        if not DIGEST.fullmatch(pack["artifact_digest"]):
            raise CandidateError("candidate pack digest must be sha256:<64 lowercase hex>")
        identity = (pack["kind"], pack["id"], pack["version"])
        if identity in identities:
            raise CandidateError(f"duplicate candidate pack: {identity}")
        identities.add(identity)
    if not any(pack["kind"] == "level" for pack in packs):
        raise CandidateError("candidate requires at least one level pack")


def verify_images(images: dict, host_id: str) -> dict:
    receipts = {}
    for role, reference in images.items():
        metadata = docker_json("image", "inspect", reference)[0]
        if role in ("authoring", "runtime_host"):
            actual = (metadata.get("Config", {}).get("Labels") or {}).get(HOST_LABEL)
            if actual != host_id:
                raise CandidateError(f"{role} image declares {actual!r}; expected {host_id}")
        receipts[role] = {"reference": reference, "image_id": metadata["Id"]}
    return receipts


def verify_packs(lock: dict, store: Path, host_id: str) -> list:
    receipts = []
    store = store.resolve(strict=True)
    for pack in lock["packs"]:
        digest = pack["artifact_digest"]
        relative = Path("blobs/sha256") / digest.removeprefix("sha256:")
        bundle = (store / relative).resolve(strict=True)
        if not bundle.is_relative_to(store):
            raise CandidateError("pack bundle escapes the selected store")
        # Use the Authoring-owned verifier shipped in the product shell. It
        # verifies file checksums, bundle identity, and an exact host variant.
        result = docker_json(
            "run", "--rm", "--network=none", "--read-only", "--tmpfs", "/tmp",
            "--user", f"{os.getuid()}:{os.getgid()}", "-e", "HOME=/tmp",
            "-v", f"{store}:/packs:ro", lock["required_images"]["product_shell"],
            "packs", "verify", str(Path("/packs") / relative), "--host", host_id,
        )
        expected = {"kind": pack["kind"], "id": pack["id"], "version": pack["version"], "digest": digest}
        if any(result.get(key) != value for key, value in expected.items()):
            raise CandidateError(f"pack verification receipt does not match lock: {pack['id']}")
        receipts.append(result)
    return receipts


def verify_dashboard(container: str, workspace: Path, images: dict, receipts: dict) -> None:
    metadata = docker_json("inspect", container)[0]
    if not metadata.get("State", {}).get("Running"):
        raise CandidateError("dashboard container is not running")
    if metadata.get("Image") != receipts["dashboard_backend"]["image_id"]:
        raise CandidateError("running dashboard backend differs from the candidate image")
    environment = dict(item.split("=", 1) for item in metadata["Config"].get("Env", []) if "=" in item)
    expected = {"MNS_WORKSPACE_ROOT": str(workspace.resolve()),
                "MNS_AUTHORING_IMAGE": images["authoring"],
                "MNS_STACK_GENERATOR_IMAGE": images["stack_generator"]}
    for key, value in expected.items():
        if environment.get(key) != value:
            raise CandidateError(f"running dashboard {key} differs from the candidate: {environment.get(key)!r}")


def dashboard_configuration(images: dict) -> tuple[dict, dict]:
    """Select every generated runtime slot explicitly for the full E2E matrix."""
    for role in ("ardupilot", "px4", "qgroundcontrol", "sim_real_eval", "lichtblick"):
        if not isinstance(images.get(role), str) or not PIN.fullmatch(images[role]):
            raise CandidateError(f"dashboard launch requires digest-pinned required_images.{role}")
    names = {"product_shell": "MNS_PRODUCT_SHELL_IMAGE", "authoring": "MNS_AUTHORING_IMAGE",
             "stack_generator": "MNS_STACK_GENERATOR_IMAGE", "runtime_host": "MNS_RUNTIME_HOST_IMAGE",
             "ros2_bridge": "MNS_ROS2_BRIDGE_IMAGE", "dashboard_backend": "DASHBOARD_BACKEND_IMAGE",
             "dashboard_frontend": "DASHBOARD_FRONTEND_IMAGE", "lichtblick": "DASHBOARD_LICHTBLICK_IMAGE"}
    environment = {name: images[role] for role, name in names.items()}
    overlay = {"schema": "mns.image_sets.v1", "image_sets": {"published": {
        "pull_policy": "missing", "images": {
            "simulators": {"tevv_runtime_host": images["runtime_host"]},
            "autopilots": {"ardupilot": images["ardupilot"], "px4": images["px4"]},
            "ros2_bridge": images["ros2_bridge"], "qgroundcontrol": images["qgroundcontrol"],
            "sim_real_eval": images["sim_real_eval"]}}}}
    return environment, overlay


def start_dashboard(workspace: Path, store: Path, images: dict) -> None:
    workspace = workspace.resolve(strict=True)
    if store.resolve() != workspace / ".mns/pack-store":
        raise CandidateError("dashboard launch must use the workspace's .mns/pack-store")
    environment, overlay = dashboard_configuration(images)
    config = workspace / ".mns/ue-candidate"
    config.mkdir(parents=True, exist_ok=True)
    image_set = config / "image-set.json"
    image_set.write_text(json.dumps(overlay, indent=2) + "\n")
    # Explicit environment takes precedence over old machine-local .env pins.
    environment = {**os.environ, **environment, "MSRS_ROOT": str(workspace),
                   "MNS_IMAGE_SET_FILE": str(image_set), "HOST_UID": str(os.getuid()),
                   "HOST_GID": str(os.getgid()), "DASHBOARD_PULL_POLICY": "never"}
    subprocess.run([str(workspace / "tools/stage-authoring-packs.sh")],
                   cwd=workspace, env=environment, check=True)
    subprocess.run(["docker", "compose", "-p", "m-s-simulation-runtime-stack", "-f",
                    "docker-compose-dashboard.yml", "up", "-d", "--pull", "never"],
                   cwd=workspace, env=environment, check=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine-root", type=Path, required=True)
    parser.add_argument("--engine-version", default="5.8.2")
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--pack-store", type=Path, default=ROOT / ".mns/pack-store")
    parser.add_argument("--dashboard-container", help="also check the live dashboard's actual workspace and image selections")
    parser.add_argument("--workspace", type=Path, default=ROOT)
    parser.add_argument("--start-dashboard", action="store_true",
                        help="after verification, stage packs and launch the dashboard with this exact candidate")
    args = parser.parse_args(argv)
    try:
        host_id = host_id_from_engine(args.engine_root.expanduser(), args.engine_version)
        lock = read_json(args.lock)
        validate_lock(lock, host_id)
        images = verify_images(lock["required_images"], host_id)
        packs = verify_packs(lock, args.pack_store, host_id)
        if args.start_dashboard:
            start_dashboard(args.workspace, args.pack_store, lock["required_images"])
        if args.dashboard_container:
            verify_dashboard(args.dashboard_container, args.workspace, lock["required_images"], images)
        print(json.dumps({"status": "preflight_passed", "e2e_verified": False,
                          "host_compatibility_id": host_id, "images": images, "packs": packs}, indent=2))
        return 0
    except (CandidateError, OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as exc:
        print(f"UE candidate BLOCKED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
