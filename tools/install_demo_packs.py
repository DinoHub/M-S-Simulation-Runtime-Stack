#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / "packs" / "standalone-v2-review.1.lock.json"
SELECTIONS = ("xfs", "safti", "condo", "pendleton", "market", "people", "vehicles")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(command: list[str], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
    """Run a command, surfacing its stderr when it fails.

    With capture_output=True, CalledProcessError swallows the child's stderr:
    a failed `packs install` reported only "returned non-zero exit status 1"
    and the real cause (bad archive, disk full, permission) was lost. Echo the
    captured streams before re-raising so the caller's handler still sees a
    CalledProcessError but the operator sees the reason.
    """
    try:
        return subprocess.run(command, check=True, text=True, capture_output=capture)
    except subprocess.CalledProcessError as exc:
        if capture:
            for stream, label in ((exc.stdout, "stdout"), (exc.stderr, "stderr")):
                if stream:
                    print(f"--- {' '.join(command[:3])}... {label} ---", file=sys.stderr)
                    print(stream.rstrip(), file=sys.stderr)
        raise


def ensure_image(image: str) -> None:
    inspected = subprocess.run(
        ["docker", "image", "inspect", image],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if inspected.returncode != 0:
        run(["docker", "pull", image])


def install_archive(image: str, archive: Path, expected_digest: str) -> None:
    result = run(
        [
            "docker", "run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}",
            "-e", "HOME=/tmp",
            "-e", "MNS_WORKSPACE_ROOT=/workspace",
            "-e", "MNS_PACK_STORE_ROOT=/workspace/.mns/pack-store",
            "-v", f"{ROOT}:/workspace:rw",
            "-v", f"{archive.parent}:/mnt/mns/release:ro",
            image, "packs", "install", f"/mnt/mns/release/{archive.name}",
        ],
        capture=True,
    )
    installed = json.loads(result.stdout)
    if installed.get("digest") != expected_digest:
        raise RuntimeError(
            f"installed digest mismatch for {archive.name}: "
            f"{installed.get('digest')} != {expected_digest}"
        )


def build_parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Download, checksum, and install standalone-v2 demo packs."
    )
    result.add_argument("--all", action="store_true", help="Install all four levels and all object packs")
    result.add_argument("--objects", action="store_true", help="Install all three object packs")
    for name in SELECTIONS:
        result.add_argument(f"--{name}", action="store_true", help=f"Install the {name} pack")
    result.add_argument("--dry-run", action="store_true", help="Print selected release assets without downloading")
    return result


def main(argv: list[str] | None = None) -> int:
    argument_parser = build_parser()
    args = argument_parser.parse_args(argv)
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    requested = {name for name in SELECTIONS if getattr(args, name)}
    if args.all:
        requested.update(pack["selection"] for pack in lock["packs"])
    if args.objects:
        requested.update(("market", "people", "vehicles"))
    if not requested:
        argument_parser.error("select --all, --objects, or at least one individual pack")

    selected = [pack for pack in lock["packs"] if pack["selection"] in requested]
    release = lock["release"]
    base_url = (
        f"https://github.com/{release['repository']}/releases/download/"
        f"{quote(release['tag'], safe='')}"
    )
    print(f"Release: {release['repository']}@{release['tag']}")
    for pack in selected:
        print(f"  {pack['selection']}: {pack['asset_name']} ({pack['artifact_digest']})")
    if args.dry_run:
        return 0

    image = lock["required_images"]["product_shell"]
    ensure_image(image)
    # Stage downloads inside the repo, not $TMPDIR. --all fetches ~2.6 GB (XFS
    # alone is 1.17 GB) and on the common systemd layout /tmp is a tmpfs sized
    # at half of RAM, so the default location ENOSPC'd partway through and
    # discarded everything already fetched. .mns/ is gitignored and is where
    # the pack store lives anyway, so it is on the same filesystem the install
    # needs space on. MNS_DEMO_PACK_DOWNLOAD_DIR overrides it.
    download_parent = Path(
        os.environ.get("MNS_DEMO_PACK_DOWNLOAD_DIR") or (ROOT / ".mns" / "downloads")
    )
    download_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mns-demo-packs-", dir=download_parent) as temporary:
        download_root = Path(temporary)
        for pack in selected:
            archive = download_root / pack["asset_name"]
            url = f"{base_url}/{quote(pack['asset_name'], safe='')}"
            print(f"Downloading {pack['display_name']}...")
            run([
                "curl", "--fail", "--location", "--retry", "3",
                "--output", str(archive), url,
            ])
            if archive.stat().st_size != pack["size_bytes"]:
                raise RuntimeError(f"size mismatch for {archive.name}")
            actual_sha256 = sha256_file(archive)
            if actual_sha256 != pack["sha256"]:
                raise RuntimeError(
                    f"SHA-256 mismatch for {archive.name}: "
                    f"{actual_sha256} != {pack['sha256']}"
                )
            try:
                install_archive(image, archive, pack["artifact_digest"])
            finally:
                # Free each archive as soon as it is installed. Holding all
                # seven until the TemporaryDirectory unwound meant --all needed
                # the full ~2.6 GB at once, on top of the copies written into
                # .mns/pack-store.
                archive.unlink(missing_ok=True)
            print(f"Installed {pack['id']}@{pack['version']}.")

    environment = os.environ.copy()
    environment["MNS_PRODUCT_SHELL_IMAGE"] = image
    subprocess.run(
        [str(ROOT / "tools" / "stage-authoring-packs.sh")],
        check=True,
        env=environment,
    )
    print("ScenarioLab pack index refreshed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        print(f"install-demo-packs: {exc}", file=sys.stderr)
        raise SystemExit(1)
