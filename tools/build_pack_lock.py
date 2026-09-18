#!/usr/bin/env python3
"""Build a packs/*.lock.json (schema mns.pack_release_lock.v1) from published
MnS pack releases.

    tools/build_pack_lock.py --release-repo DinoHub/TEVV-Airsim --discover
        --host-contract packs/runtime-host-compatibility.ue582.json
        --images-env images/standalone-v2-ue582.generated.env
        --shell local/mns-product-shell:ue582-local.a1936b0a5f5f
        --cache .mns/downloads/pack-cache
        --output packs/standalone-v2-ue582.lock.json

(`make pack-lock` runs exactly that for the selected channel.) --discover takes
every pack-* release cooked for the contract's host id, newest version per
pack; --release-tag names releases explicitly and can be combined with it.

Each release (TEVV-Airsim tooling/scripts/publish_pack.py layout) carries the
bundle, `<bundle>.sha256`, `artifact.json` (variants + payload digest) and the
semantic manifest (`mns_level_pack.json` / `mns_asset_pack.json`). Everything
the installer verifies at download time comes from those files; the one value
they do not carry is the bundle's content-addressed `artifact_digest`, which is
what the pack store indexes on and what a ScenarioSpec names. That comes from
the product shell's own verifier (`packs verify --host <id>`), run on the
archive -- so the lock is written by the same code that will later install it,
and a bundle that the shell rejects never makes it into a lock.

The archive is fetched into --cache if it is not already there with the right
checksum; tools/install-demo-packs.sh reuses the same cache, so a lock build
followed by an install downloads each bundle once.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "mns.pack_release_lock.v1"
IMAGE_ROLES = {
    "product_shell": "MNS_PRODUCT_SHELL_IMAGE",
    "authoring": "MNS_AUTHORING_IMAGE",
    "stack_generator": "MNS_STACK_GENERATOR_IMAGE",
    "runtime_host": "MNS_RUNTIME_HOST_IMAGE",
    "ros2_bridge": "MNS_ROS2_BRIDGE_IMAGE",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def gh_json(*args: str) -> Any:
    result = subprocess.run(["gh", *args], check=True, text=True, capture_output=True)
    return json.loads(result.stdout)


def gh_download(repo: str, tag: str, pattern: str, dest: Path) -> None:
    subprocess.run(["gh", "release", "download", tag, "-R", repo, "-D", str(dest),
                    "-p", pattern, "--clobber"], check=True)


def read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.strip()
    return out


def selection_for(pack_id: str, kind: str) -> str:
    """Short CLI flag name: `--condo` for condo-level, `--office-props` for the
    asset pack. Level packs drop a trailing -level/-environment; asset packs
    keep their id."""
    if kind == "level":
        return re.sub(r"-(level|environment)$", "", pack_id)
    return pack_id


def shell_verify(shell: str, archive: Path, host_id: str) -> dict[str, Any]:
    result = subprocess.run(
        ["docker", "run", "--rm", "--network=none", "--user", f"{os.getuid()}:{os.getgid()}",
         "-e", "HOME=/tmp", "-v", f"{archive.parent}:/in:ro", shell,
         "packs", "verify", f"/in/{archive.name}", "--host", host_id],
        check=True, text=True, capture_output=True)
    return json.loads(result.stdout)


def version_key(version: str) -> tuple[int, ...]:
    """Sort key for a pack version, so 1.0.10 sorts above 1.0.9.

    Pack versions are dotted integers (`1.0.3`), occasionally with a hyphenated
    suffix. There is no semver library in tools/requirements.txt and this does
    not need one: split on both separators and compare numerically, treating a
    non-numeric component as 0 so a malformed version sorts low rather than
    raising.
    """
    return tuple(int(x) if x.isdigit() else 0 for x in re.split(r"[.-]", str(version)))


def discover_latest(repo: str, host_id: str, cache: Path,
                    quiet: bool = False) -> dict[tuple[str, str], dict[str, str]]:
    """The newest published release per (kind, id) cooked for `host_id`.

    The filter is the release's own artifact.json (fetched into the receipts
    cache, a few KB each), not the tag name, because the tag does not say which
    engine the payload was cooked for.
    """
    releases = gh_json("release", "list", "-R", repo, "--limit", "200", "--json", "tagName,isDraft,isPrerelease")
    tags = [r["tagName"] for r in releases if not r.get("isDraft") and r["tagName"].startswith("pack-")]
    latest: dict[tuple[str, str], dict[str, str]] = {}
    for tag in tags:
        receipts = cache / "receipts" / tag
        receipts.mkdir(parents=True, exist_ok=True)
        if not (receipts / "artifact.json").is_file():
            try:
                gh_download(repo, tag, "artifact.json", receipts)
            except subprocess.CalledProcessError:
                if not quiet:
                    print(f"skipping {tag}: no artifact.json", file=sys.stderr)
                continue
        artifact = json.loads((receipts / "artifact.json").read_text(encoding="utf-8"))
        if not any(v.get("host_compatibility_id") == host_id for v in artifact.get("variants", [])):
            if not quiet:
                print(f"skipping {tag}: no variant for {host_id}", file=sys.stderr)
            continue
        kind, pack_id, version = artifact["kind"], artifact["pack"]["id"], artifact["pack"]["version"]
        current = latest.get((kind, pack_id))
        if current is None or version_key(version) > version_key(current["version"]):
            latest[(kind, pack_id)] = {"version": str(version), "tag": tag}
    return latest


def discover_release_tags(repo: str, host_id: str, cache: Path) -> list[str]:
    """Every pack release to put in a lock: newest version per (kind, id).

    A lock is a snapshot; this is how it catches up with what packaging
    published since.
    """
    latest = discover_latest(repo, host_id, cache)
    return sorted(entry["tag"] for entry in latest.values())


def build_entry(repo: str, tag: str, cache: Path, shell: str, host_id: str) -> dict[str, Any]:
    release = gh_json("release", "view", tag, "-R", repo, "--json", "assets,createdAt")
    assets = {asset["name"]: asset for asset in release["assets"]}
    bundle_name = next((n for n in assets if n.endswith((".mnslevelpack", ".mnsassetpack"))), None)
    # publish_pack.py splits a bundle over 1900 MB into <bundle>.part-NNN and
    # attaches only <bundle>.sha256 for the whole; the bundle name comes from
    # that sidecar and the parts are reassembled locally before verification.
    parts: list[str] = []
    if not bundle_name:
        sha_names = [n for n in assets if n.endswith(".sha256")
                     and n[:-len(".sha256")].endswith((".mnslevelpack", ".mnsassetpack"))]
        if len(sha_names) == 1:
            bundle_name = sha_names[0][:-len(".sha256")]
            parts = sorted(n for n in assets if n.startswith(f"{bundle_name}.part-"))
    if not bundle_name or (bundle_name not in assets and not parts):
        raise SystemExit(f"{repo}@{tag}: no .mnslevelpack/.mnsassetpack asset (or its parts)")
    kind = "level" if bundle_name.endswith(".mnslevelpack") else "asset"
    manifest_name = "mns_level_pack.json" if kind == "level" else "mns_asset_pack.json"
    for name in ("artifact.json", manifest_name, f"{bundle_name}.sha256"):
        if name not in assets:
            raise SystemExit(f"{repo}@{tag}: release lacks {name}")

    receipts = cache / "receipts" / tag
    receipts.mkdir(parents=True, exist_ok=True)
    for name in ("artifact.json", manifest_name, f"{bundle_name}.sha256"):
        if not (receipts / name).is_file():
            gh_download(repo, tag, name, receipts)
    artifact = json.loads((receipts / "artifact.json").read_text(encoding="utf-8"))
    manifest = json.loads((receipts / manifest_name).read_text(encoding="utf-8"))
    expected_sha = (receipts / f"{bundle_name}.sha256").read_text().split()[0].lower()
    size = int(assets[bundle_name]["size"]) if not parts else sum(int(assets[n]["size"]) for n in parts)

    variants = [v for v in artifact.get("variants", []) if v.get("host_compatibility_id") == host_id]
    if not variants:
        raise SystemExit(f"{repo}@{tag}: no variant cooked for {host_id}")
    variant = variants[0]

    archive = cache / bundle_name
    if not archive.is_file() or sha256_file(archive) != expected_sha:
        print(f"Downloading {bundle_name} ({size / 1e6:.0f} MB) from {repo}@{tag}...", flush=True)
        if parts:
            parts_dir = cache / "parts" / tag
            parts_dir.mkdir(parents=True, exist_ok=True)
            with archive.open("wb") as assembled:
                for part in parts:
                    gh_download(repo, tag, part, parts_dir)
                    with (parts_dir / part).open("rb") as source:
                        while chunk := source.read(8 * 1024 * 1024):
                            assembled.write(chunk)
                    (parts_dir / part).unlink()
        else:
            gh_download(repo, tag, bundle_name, cache)
    actual = sha256_file(archive)
    if actual != expected_sha:
        raise SystemExit(f"{bundle_name}: sha256 {actual} != release {expected_sha}")
    if archive.stat().st_size != size:
        raise SystemExit(f"{bundle_name}: size {archive.stat().st_size} != release {size}")

    print(f"Verifying {bundle_name} with {shell}...", flush=True)
    verified = shell_verify(shell, archive, host_id)
    identity = manifest.get("level_pack" if kind == "level" else "asset_pack") or {}
    pack_id, version = artifact["pack"]["id"], artifact["pack"]["version"]
    if (verified.get("id"), verified.get("version"), verified.get("kind")) != (pack_id, version, kind):
        raise SystemExit(f"{bundle_name}: verifier reports {verified}, release says {pack_id}@{version} {kind}")
    plugins = [p.get("id") for p in (manifest.get("dependencies") or {}).get("plugins", []) if p.get("id")]
    return {
        "selection": selection_for(pack_id, kind),
        "kind": kind,
        "id": pack_id,
        "display_name": str(identity.get("display_name") or pack_id),
        "version": version,
        "asset_name": bundle_name,
        **({"parts": parts} if parts else {}),
        "size_bytes": size,
        "sha256": expected_sha,
        "artifact_digest": verified["digest"],
        "payload_digest": variant["payload_digest"],
        "source_manifest_digest": artifact.get("source_manifest_digest", ""),
        "map_path": str((manifest.get("unreal") or {}).get("entry_map") or ""),
        "required_plugins": plugins,
        "release": {"repository": repo, "tag": tag},
        "published_at": release.get("createdAt", ""),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--release-repo", required=True)
    parser.add_argument("--release-tag", action="append", default=[],
                        help="a pack release to include; repeatable. With --discover, added to the discovered set")
    parser.add_argument("--discover", action="store_true",
                        help="include every pack-* release on --release-repo cooked for the host contract's id "
                             "(newest version per pack)")
    parser.add_argument("--host-contract", type=Path, required=True,
                        help="runtime host capability contract; its id is the lock's capability_id")
    parser.add_argument("--images-env", type=Path, required=True,
                        help="generated channel env whose MNS_*_IMAGE pins become required_images")
    parser.add_argument("--shell", help="product shell image to verify with (default: the env's MNS_PRODUCT_SHELL_IMAGE)")
    parser.add_argument("--cache", type=Path, default=ROOT / ".mns" / "downloads" / "pack-cache")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--lock-repository", default="DinoHub/M-S-Simulation-Runtime-Stack")
    parser.add_argument("--lock-tag", help="release.tag recorded in the lock (default: output stem)")
    args = parser.parse_args(argv)

    host_id = json.loads(args.host_contract.read_text(encoding="utf-8"))["id"]
    env = read_env(args.images_env)
    missing = [var for var in IMAGE_ROLES.values() if var not in env]
    if missing:
        raise SystemExit(f"{args.images_env}: missing {', '.join(missing)}")
    shell = args.shell or env["MNS_PRODUCT_SHELL_IMAGE"]
    args.cache.mkdir(parents=True, exist_ok=True)

    tags = list(args.release_tag)
    if args.discover:
        tags += [t for t in discover_release_tags(args.release_repo, host_id, args.cache) if t not in tags]
    if not tags:
        raise SystemExit("no releases selected: pass --release-tag and/or --discover")
    print(f"Releases: {', '.join(tags)}")
    packs = [build_entry(args.release_repo, tag, args.cache, shell, host_id) for tag in tags]
    packs.sort(key=lambda p: (p["kind"] != "level", p["id"]))
    lock = {
        "schema": SCHEMA,
        "release": {"repository": args.lock_repository,
                    "tag": args.lock_tag or args.output.stem.removesuffix(".lock")},
        "capability_id": host_id,
        "host_contract": str(args.host_contract.resolve().relative_to(ROOT)) if args.host_contract.resolve().is_relative_to(ROOT) else str(args.host_contract),
        "required_images": {role: env[var] for role, var in IMAGE_ROLES.items()},
        "packs": packs,
    }
    args.output.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {args.output} ({len(packs)} packs, capability {host_id})")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        print(f"build-pack-lock: {' '.join(map(str, exc.cmd[:4]))} failed: {(exc.stderr or '').strip()[-600:]}", file=sys.stderr)
        raise SystemExit(1)
