#!/usr/bin/env python3
"""Check engine/image/pack provenance before browser E2E; never claim E2E success."""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMAGE_ROLES = ("product_shell", "authoring", "stack_generator", "runtime_host",
               "ros2_bridge", "dashboard_backend", "dashboard_frontend", "timescaledb")
HOST_LABEL = "tevv.content_packs.host_compatibility_id"
PIN = re.compile(r"[^\s@]+@sha256:[0-9a-f]{64}")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
TAGGED_IMAGE = re.compile(
    r"[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*"
    r":[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}"
)


class CandidateError(ValueError):
    pass


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def docker_json(*arguments: str):
    result = subprocess.run(["docker", *arguments], check=True, text=True, capture_output=True)
    return json.loads(result.stdout)


def host_id_from_image(reference: str) -> str:
    """The frozen host id a runtime-host/ScenarioLab image was packaged with.

    The label is written at packaging time from the engine's own
    Build.version (TEVV-Airsim packaging), so on a machine that only runs the
    images -- every customer machine -- it is the same fact
    host_id_from_engine() reads from an installed engine, without needing
    one. The image must already be present locally (--local-images
    candidates always are; pinned ones are pulled by the product setup).
    """
    metadata = docker_json("image", "inspect", reference)[0]
    host_id = (metadata.get("Config", {}).get("Labels") or {}).get(HOST_LABEL)
    if not isinstance(host_id, str) or not re.fullmatch(
            r"ue-\d+\.\d+\.\d+-cl\d+-linux-development-vulkan-sm6-iostore-v2(?:-render-[0-9a-f]{16})?", host_id):
        raise CandidateError(f"{reference} carries no usable {HOST_LABEL} label: {host_id!r}")
    return host_id


def host_id_from_engine(engine_root: Path, expected_version: str,
                        candidate_id: str | None = None) -> str:
    build = read_json(engine_root / "Engine/Build/Build.version")
    fields = ("MajorVersion", "MinorVersion", "PatchVersion", "Changelist")
    if not isinstance(build, dict) or any(type(build.get(key)) is not int for key in fields):
        raise CandidateError("Build.version must contain integer version fields and Changelist")
    actual = ".".join(str(build[key]) for key in fields[:3])
    if actual != expected_version or build["Changelist"] <= 0:
        raise CandidateError(f"engine is {actual} CL{build['Changelist']}; expected {expected_version} with a release changelist")
    base = f"ue-{actual}-cl{build['Changelist']}-linux-development-vulkan-sm6-iostore-v2"
    # Build.version proves the engine, not the renderer. Image labels and the
    # frozen contracts independently verify the full candidate identity.
    if candidate_id is not None:
        if not isinstance(candidate_id, str) or not re.fullmatch(
            re.escape(base) + r"(?:-render-[0-9a-f]{16})?", candidate_id
        ):
            raise CandidateError(f"candidate capability does not match observed engine {base}")
        return candidate_id
    return base


def is_local_candidate_reference(reference: object) -> bool:
    """Return true for an explicit, non-moving tag usable in local mode."""
    if not isinstance(reference, str) or not TAGGED_IMAGE.fullmatch(reference):
        return False
    return not reference.rsplit(":", 1)[1].lower().endswith("latest")


def validate_lock(lock: dict, host_id: str, local_images: bool = False) -> None:
    if not isinstance(lock, dict) or lock.get("schema") != "mns.pack_release_lock.v1":
        raise CandidateError("expected mns.pack_release_lock.v1 candidate lock")
    if lock.get("capability_id") != host_id:
        raise CandidateError(f"pack lock capability must be {host_id}; got {lock.get('capability_id')}")
    images = lock.get("required_images")
    if not isinstance(images, dict):
        raise CandidateError("candidate lock must declare required_images")
    for role in IMAGE_ROLES:
        if role not in images:
            requirement = "declare an explicit local tag or registry digest" if local_images else "be pinned by registry digest"
            raise CandidateError(f"required_images.{role} must {requirement}")

    # Additional components (for example an autopilot) follow the same mode.
    local_references = set()
    for role, reference in images.items():
        if isinstance(reference, str) and PIN.fullmatch(reference):
            continue
        if not local_images:
            raise CandidateError(f"required_images.{role} must be pinned by registry digest")
        if not is_local_candidate_reference(reference):
            raise CandidateError(
                f"required_images.{role} must be a registry digest or an explicit non-latest local tag"
            )
        if reference in local_references:
            raise CandidateError(f"local image tag is reused by more than one role: {reference}")
        local_references.add(reference)

    if local_images:
        if not local_references:
            raise CandidateError("--local-images requires at least one explicit local image tag")
        expected_ids = lock.get("required_image_ids")
        if not isinstance(expected_ids, dict):
            raise CandidateError("local candidate lock must declare required_image_ids")
        missing = sorted(set(images) - set(expected_ids))
        extra = sorted(set(expected_ids) - set(images))
        if missing or extra:
            raise CandidateError(
                f"required_image_ids keys must exactly match required_images; missing={missing}, extra={extra}"
            )
        for role, image_id in expected_ids.items():
            if not isinstance(image_id, str) or not DIGEST.fullmatch(image_id):
                raise CandidateError(f"required_image_ids.{role} must be sha256:<64 lowercase hex>")
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


def verify_images(images: dict, host_id: str, expected_ids: dict | None = None) -> dict:
    receipts = {}
    for role, reference in images.items():
        metadata = docker_json("image", "inspect", reference)[0]
        image_id = metadata.get("Id")
        if expected_ids is not None and image_id != expected_ids[role]:
            raise CandidateError(
                f"{role} local image ID is {image_id!r}; expected {expected_ids[role]!r}"
            )
        if role in ("authoring", "runtime_host"):
            actual = (metadata.get("Config", {}).get("Labels") or {}).get(HOST_LABEL)
            if actual != host_id:
                raise CandidateError(f"{role} image declares {actual!r}; expected {host_id}")
        receipts[role] = {"reference": reference, "image_id": image_id}
    return receipts


def verify_packs(lock: dict, store: Path, host_id: str, local_images: bool = False) -> list:
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
        pull_policy = ("--pull=never",) if local_images else ()
        result = docker_json(
            "run", *pull_policy, "--rm", "--network=none", "--read-only", "--tmpfs", "/tmp",
            "--user", f"{os.getuid()}:{os.getgid()}", "-e", "HOME=/tmp",
            "-v", f"{store}:/packs:ro", lock["required_images"]["product_shell"],
            "packs", "verify", str(Path("/packs") / relative), "--host", host_id,
        )
        expected = {"kind": pack["kind"], "id": pack["id"], "version": pack["version"], "digest": digest}
        if any(result.get(key) != value for key, value in expected.items()):
            raise CandidateError(f"pack verification receipt does not match lock: {pack['id']}")
        receipts.append(result)
    return receipts


def verify_dashboard(container: str, workspace: Path, images: dict, receipts: dict,
                     selection: dict | None = None) -> None:
    metadata = docker_json("inspect", container)[0]
    if not metadata.get("State", {}).get("Running"):
        raise CandidateError("dashboard container is not running")
    if metadata.get("Image") != receipts["dashboard_backend"]["image_id"]:
        raise CandidateError("running dashboard backend differs from the candidate image")
    environment = dict(item.split("=", 1) for item in metadata["Config"].get("Env", []) if "=" in item)
    expected = {"MNS_WORKSPACE_ROOT": str(workspace.resolve()),
                "MNS_AUTHORING_IMAGE": images["authoring"],
                "MNS_STACK_GENERATOR_IMAGE": images["stack_generator"]}
    if selection is not None:
        expected.update(selection)
    for key, value in expected.items():
        if environment.get(key) != value:
            raise CandidateError(f"running dashboard {key} differs from the candidate: {environment.get(key)!r}")


def dashboard_configuration(images: dict, local_images: bool = False) -> tuple[dict, dict]:
    """Select every generated runtime slot explicitly for the full E2E matrix."""
    for role in ("ardupilot", "px4", "qgroundcontrol", "sim_real_eval", "lichtblick", "timescaledb"):
        reference = images.get(role)
        if not isinstance(reference, str) or not (
            PIN.fullmatch(reference) or (local_images and is_local_candidate_reference(reference))
        ):
            requirement = "an explicit local tag or registry digest" if local_images else "a registry digest"
            raise CandidateError(f"dashboard launch requires {requirement} in required_images.{role}")
    names = {"product_shell": "MNS_PRODUCT_SHELL_IMAGE", "authoring": "MNS_AUTHORING_IMAGE",
             "stack_generator": "MNS_STACK_GENERATOR_IMAGE", "runtime_host": "MNS_RUNTIME_HOST_IMAGE",
             "ros2_bridge": "MNS_ROS2_BRIDGE_IMAGE", "dashboard_backend": "DASHBOARD_BACKEND_IMAGE",
             "dashboard_frontend": "DASHBOARD_FRONTEND_IMAGE", "lichtblick": "DASHBOARD_LICHTBLICK_IMAGE",
             "timescaledb": "DASHBOARD_TIMESCALEDB_IMAGE"}
    environment = {name: images[role] for role, name in names.items()}
    environment["MNS_IMAGE_SET"] = "published"
    overlay = {"schema": "mns.image_sets.v1", "image_sets": {"published": {
        "pull_policy": "never" if local_images else "missing", "images": {
            "simulators": {"tevv_runtime_host": images["runtime_host"]},
            "autopilots": {"ardupilot": images["ardupilot"], "px4": images["px4"]},
            "ros2_bridge": images["ros2_bridge"], "qgroundcontrol": images["qgroundcontrol"],
            "sim_real_eval": images["sim_real_eval"]}}}}
    return environment, overlay


def write_local_override(path: Path, environment: dict, images: dict, expected_ids: dict) -> None:
    """Persist shell-safe local selections and their inspected IDs for reruns."""
    lines = [
        "# Generated by tools/check_ue_candidate.py --local-images.",
        "# Source this file before a manual docker compose rerun; do not hand-edit.",
    ]
    for role in sorted(images):
        lines.append(f"# {role}: {images[role]} = {expected_ids[role]}")
    for key, value in sorted(environment.items()):
        value = str(value)
        if "\n" in value or "\r" in value:
            raise CandidateError(f"cannot persist multiline local override {key}")
        lines.append(f"{key}={shlex.quote(value)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o600)


def write_local_rerun(path: Path, engine_root: Path | None, engine_version: str, lock: Path,
                      pack_store: Path, workspace: Path, selection: dict | None = None,
                      compose_project: str | None = None) -> None:
    """Persist a revalidating command, never a catalog/Makefile shortcut."""
    command = [
        sys.executable, str(ROOT / "tools/check_ue_candidate.py"),
        *(["--engine-root", str(engine_root.resolve())] if engine_root is not None else []),
        "--engine-version", engine_version,
        "--lock", str(lock.resolve()),
        "--pack-store", str(pack_store.resolve()),
        "--workspace", str(workspace.resolve()),
        "--local-images", "--start-dashboard",
    ]
    if compose_project:
        command += ["--compose-project", compose_project]
    if selection is not None:
        for flag, key in (
            ("--authoring-data-root", "MNS_AUTHORING_DATA_ROOT"),
            ("--runtime-host-contract", "MNS_RUNTIME_HOST_COMPATIBILITY_CONTRACT"),
            ("--authoring-host-contract", "MNS_AUTHORING_HOST_CONTRACT"),
        ):
            command += [flag, selection[key]]
    override = path.parent / "local-images.env"
    path.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "set -a\n"
        f"source {shlex.quote(str(override.resolve()))}\n"
        "set +a\n"
        "exec " + shlex.join(command) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o700)


def candidate_selection(workspace: Path, store: Path, lock_path: Path, lock: dict,
                        *, authoring_data: Path | None = None,
                        runtime_contract: Path | None = None,
                        authoring_contract: Path | None = None) -> dict:
    """Resolve once; the same selection feeds staging, compose and inspection."""
    workspace = workspace.resolve(strict=True)

    def inside(value: Path | str | None, label: str, *, file: bool = False) -> Path:
        if not value:
            raise CandidateError(f"candidate requires {label}; supply it explicitly or in the lock")
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = workspace / path
        path = path.resolve()
        if path == workspace or not path.is_relative_to(workspace):
            raise CandidateError(f"{label} must be inside the selected workspace: {path}")
        if any(character in str(path) for character in ("\n", "\r", ",", ":")):
            raise CandidateError(f"{label} cannot be represented safely as a container bind")
        if file and not path.is_file():
            raise CandidateError(f"{label} does not exist: {path}")
        return path

    selected_store = inside(store, "pack store")
    data = inside(authoring_data or ".mns/ue-candidate/authoring-data", "authoring data root")
    selected_lock = inside(lock_path, "candidate lock", file=True)
    runtime = inside(runtime_contract or lock.get("host_contract"), "runtime host contract", file=True)
    authoring = inside(authoring_contract or lock.get("authoring_host_contract"),
                       "authoring host contract", file=True)
    for path in (runtime, authoring):
        contract = read_json(path)
        if contract.get("schema") != "mns.host_compatibility.v1" or contract.get("id") != lock["capability_id"]:
            raise CandidateError(f"host contract does not match candidate capability: {path}")
    return {
        "MNS_IMAGE_SET": "published",
        "MNS_IMAGE_SET_FILE": str(workspace / ".mns/ue-candidate/image-set.json"),
        "MNS_PACK_STORE_ROOT": str(selected_store),
        "MNS_AUTHORING_DATA_ROOT": str(data),
        "MNS_RUNTIME_HOST_COMPATIBILITY_CONTRACT": str(runtime),
        "MNS_AUTHORING_HOST_CONTRACT": str(authoring),
        "MNS_DEMO_PACK_LOCK": str(selected_lock),
        "MNS_CHANNEL": "candidate",
        # A verified candidate never seeds unrelated defaults or skips staging.
        "MNS_SEED_AUTHORING_DEFAULTS": "0",
        "MNS_IMAGE_PULL_POLICY": "never",
    }


def verify_staged_selection(selection: dict) -> None:
    lock = read_json(Path(selection["MNS_DEMO_PACK_LOCK"]))
    index_path = Path(selection["MNS_AUTHORING_DATA_ROOT"]) / "ResolvedPacks/index.json"
    index = read_json(index_path)
    if index.get("cook_capability_id") != lock["capability_id"]:
        raise CandidateError("staged authoring index differs from the verified candidate capability")
    staged = {pack.get("artifact_digest") for kind in ("level_packs", "asset_packs")
              for pack in index.get(kind, [])}
    missing = [pack["id"] for pack in lock["packs"] if pack["artifact_digest"] not in staged]
    if missing:
        raise CandidateError(f"candidate packs were not staged for authoring: {missing}")


def start_dashboard(workspace: Path, store: Path, images: dict, local_images: bool = False,
                    expected_ids: dict | None = None, *, selection: dict,
                    compose_project: str | None = None) -> Path | None:
    if compose_project is not None and not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", compose_project):
        raise CandidateError("compose project must contain lowercase letters, digits, hyphens or underscores")
    workspace = workspace.resolve(strict=True)
    if store.resolve() != Path(selection["MNS_PACK_STORE_ROOT"]):
        raise CandidateError("dashboard launch store differs from the verified selection")
    environment, overlay = dashboard_configuration(images, local_images=local_images)
    config = workspace / ".mns/ue-candidate"
    config.mkdir(parents=True, exist_ok=True)
    image_set = config / "image-set.json"
    image_set.write_text(json.dumps(overlay, indent=2) + "\n")
    # Explicit environment takes precedence over old machine-local .env pins.
    overrides = {**environment, **selection, "MSRS_ROOT": str(workspace),
                 "MNS_IMAGE_SET_FILE": str(image_set), "HOST_UID": str(os.getuid()),
                 "HOST_GID": str(os.getgid()), "DASHBOARD_PULL_POLICY": "never",
                 "MNS_IMAGE_PULL_POLICY": "never", "MNS_SKIP_PACK_STAGING": "0"}
    if compose_project:
        overrides["DASHBOARD_CONTAINER_PREFIX"] = compose_project + "-"
    for key in ("DISPLAY", "XAUTHORITY"):
        if os.environ.get(key):
            overrides[key] = os.environ[key]
    local_override = None
    if local_images:
        if expected_ids is None:
            raise CandidateError("local dashboard launch requires expected image IDs")
        local_override = config / "local-images.env"
        write_local_override(local_override, overrides, images, expected_ids)
    environment = {**os.environ, **overrides}
    stage_environment = environment
    if local_images:
        # Address the already-inspected local image by immutable ID and require
        # the staging helper to reject a missing local image without pulling.
        stage_environment = {
            **environment, "MNS_PRODUCT_SHELL_IMAGE": expected_ids["product_shell"]
        }
    subprocess.run([str(workspace / "tools/stage-authoring-packs.sh")],
                   cwd=workspace, env=stage_environment, check=True)
    verify_staged_selection(selection)
    subprocess.run(["docker", "compose", "-p", compose_project or "m-s-simulation-runtime-stack", "-f",
                    "docker-compose-dashboard.yml", "up", "-d", "--pull", "never"],
                   cwd=workspace, env=environment, check=True)
    return local_override


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine-root", type=Path,
                        help="an installed Unreal engine whose Build.version defines the host id; "
                             "without it the id is read from the lock's runtime_host image label")
    parser.add_argument("--engine-version", default="5.8.2")
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--pack-store", type=Path, default=ROOT / ".mns/pack-store")
    parser.add_argument("--dashboard-container", help="also check the live dashboard's actual workspace and image selections")
    parser.add_argument("--workspace", type=Path, default=ROOT)
    parser.add_argument("--authoring-data-root", type=Path)
    parser.add_argument("--runtime-host-contract", type=Path,
                        help="Frozen runtime contract; otherwise lock.host_contract")
    parser.add_argument("--authoring-host-contract", type=Path,
                        help="Frozen authoring contract; otherwise lock.authoring_host_contract")
    parser.add_argument("--start-dashboard", action="store_true",
                        help="after verification, stage packs and launch the dashboard with this exact candidate")
    parser.add_argument("--compose-project", help="isolated Compose project and dashboard container-name prefix")
    parser.add_argument("--local-images", action="store_true",
                        help="allow explicit local tags only with exact required_image_ids; never pull them")
    args = parser.parse_args(argv)
    try:
        lock = read_json(args.lock)
        if args.engine_root is not None:
            host_id = host_id_from_engine(args.engine_root.expanduser(), args.engine_version,
                                         lock.get("capability_id"))
        else:
            # No engine on this machine (the normal customer case): the
            # runtime host image's packaging label is the frozen id, and
            # verify_images() below re-checks that authoring agrees with it.
            runtime_host = (lock.get("required_images") or {}).get("runtime_host")
            if not isinstance(runtime_host, str):
                raise CandidateError("lock has no required_images.runtime_host to read the host id from; "
                                     "pass --engine-root")
            host_id = host_id_from_image(runtime_host)
            if not host_id.startswith(f"ue-{args.engine_version}-"):
                raise CandidateError(f"{runtime_host} is packaged for {host_id}, not UE {args.engine_version}")
        validate_lock(lock, host_id, local_images=args.local_images)
        selection = None
        if args.start_dashboard or args.dashboard_container:
            selection = candidate_selection(
                args.workspace, args.pack_store, args.lock, lock,
                authoring_data=args.authoring_data_root,
                runtime_contract=args.runtime_host_contract,
                authoring_contract=args.authoring_host_contract,
            )
        expected_ids = lock.get("required_image_ids") if args.local_images else None
        images = verify_images(lock["required_images"], host_id, expected_ids=expected_ids)
        packs = verify_packs(lock, args.pack_store, host_id, local_images=args.local_images)
        local_override = None
        local_rerun = None
        if args.start_dashboard:
            local_override = start_dashboard(
                args.workspace, args.pack_store, lock["required_images"],
                local_images=args.local_images, expected_ids=expected_ids,
                selection=selection, compose_project=args.compose_project,
            )
            if args.local_images:
                local_rerun = args.workspace.resolve() / ".mns/ue-candidate/rerun.sh"
                write_local_rerun(
                    local_rerun, args.engine_root.expanduser() if args.engine_root else None,
                    args.engine_version, args.lock.expanduser(), args.pack_store.expanduser(),
                    args.workspace, selection, compose_project=args.compose_project,
                )
        if args.dashboard_container:
            verify_dashboard(args.dashboard_container, args.workspace, lock["required_images"], images,
                             selection)
        print(json.dumps({"status": "preflight_passed", "e2e_verified": False,
                          "host_compatibility_id": host_id, "images": images, "packs": packs,
                          "local_override": str(local_override) if local_override else None,
                          "local_rerun": str(local_rerun) if local_rerun else None}, indent=2))
        return 0
    except (CandidateError, OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as exc:
        print(f"UE candidate BLOCKED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
