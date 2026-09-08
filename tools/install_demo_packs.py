#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / "packs" / "standalone-v2-review.1.lock.json"
# The content-addressed store the product shell's `packs install` writes.
# Its index is the only record of what is installed; the lock's
# artifact_digest is what install_archive() asserts against, so matching on
# that digest is exact rather than "a pack with this id exists".
STORE_INDEX = ROOT / ".mns" / "pack-store" / "index.json"
# The runtime host's frozen capability contract (packs/README.md). The lock's
# capability_id must be this document's id: the generator validates every
# resolved pack against the contract, and a pack cooked for another host id
# would install fine here and fail at generate time instead.
HOST_CONTRACT_PATH = ROOT / "packs" / "runtime-host-compatibility.json"
SELECTIONS = ("xfs", "safti", "condo", "pendleton", "market", "people", "vehicles")
# Archive on disk plus the copy `packs install` writes into the store, and
# some slack for the index and temporary files.
DISK_HEADROOM_BYTES = 512 * 1024 * 1024


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


def installed_digests() -> set[str]:
    """artifact digests of every pack in the local store; empty when there is no store."""
    if not STORE_INDEX.is_file():
        return set()
    try:
        packs = json.loads(STORE_INDEX.read_text(encoding="utf-8")).get("packs") or []
    except (OSError, json.JSONDecodeError, AttributeError):
        return set()
    return {str(pack.get("digest")) for pack in packs if isinstance(pack, dict) and pack.get("digest")}


def check_disk_space(download_parent: Path, packs: list[dict]) -> None:
    """Fail before the first byte is fetched when the download cannot fit.

    The failure this replaces was a curl ENOSPC halfway through --all, which
    threw away everything already downloaded and said nothing about how much
    room was needed. The estimate is archive + store copy per pack, because
    each archive is deleted only after its `packs install` has copied it.
    """
    needed = sum(int(pack["size_bytes"]) for pack in packs) * 2 + DISK_HEADROOM_BYTES
    free = shutil.disk_usage(download_parent).free
    if free < needed:
        raise RuntimeError(
            f"not enough free space under {download_parent}: need about "
            f"{needed / 1e9:.1f} GB (archive + store copy per pack), have "
            f"{free / 1e9:.1f} GB. Free some space, point MNS_DEMO_PACK_DOWNLOAD_DIR "
            f"at a bigger filesystem, or install fewer packs "
            f'(for example MNS_DEMO_PACKS="--condo --objects" make dashboard).'
        )


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
    result.add_argument("--lock", type=Path, default=LOCK_PATH,
                        help="Use a separate candidate lock without changing the released pack set")
    result.add_argument(
        "--missing", action="store_true",
        help="Skip packs already in .mns/pack-store (what `make dashboard` runs); "
             "exits 0 without touching the registry when nothing is missing",
    )
    result.add_argument(
        "--check", action="store_true",
        help="Report which selected packs are installed and exit 1 if any is missing; "
             "offline, no docker",
    )
    return result


def main(argv: list[str] | None = None) -> int:
    argument_parser = build_parser()
    args = argument_parser.parse_args(argv)
    lock = json.loads(args.lock.read_text(encoding="utf-8"))
    contract_id = json.loads(HOST_CONTRACT_PATH.read_text(encoding="utf-8")).get("id")
    if lock.get("capability_id") != contract_id:
        raise RuntimeError(
            f"{args.lock.name} is for capability {lock.get('capability_id')!r} but "
            f"{HOST_CONTRACT_PATH.name} declares {contract_id!r}; the packs would not "
            f"mount on the pinned runtime host (see packs/README.md)"
        )
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

    if args.check or args.missing:
        installed = installed_digests()
        present = [pack for pack in selected if pack["artifact_digest"] in installed]
        missing = [pack for pack in selected if pack["artifact_digest"] not in installed]
        store_note = str(STORE_INDEX.parent) if STORE_INDEX.is_file() else f"{STORE_INDEX.parent} (no store yet)"
        print(f"Pack store: {store_note}")
        for pack in present:
            print(f"  installed: {pack['id']}@{pack['version']} ({pack['selection']})")
        for pack in missing:
            print(f"  missing:   {pack['id']}@{pack['version']} ({pack['selection']})")
        if args.check:
            return 0 if not missing else 1
        if not missing:
            print(f"All {len(selected)} selected demo packs are already installed.")
            return 0
        selected = missing

    print(f"Release: {release['repository']}@{release['tag']}")
    for pack in selected:
        print(f"  {pack['selection']}: {pack['asset_name']} ({pack['artifact_digest']})")
    if args.dry_run:
        return 0

    # The product shell that runs `packs install` and `packs stage-authoring`.
    # The lock pins the release's digest so a standalone CLI run is exact; the
    # Makefile exports the image of the selected IMAGE_MODE instead (the
    # -latest alias in development), so the dashboard's install and staging
    # steps use one shell and the ResolvedPacks/.staged-with stamp agrees with
    # what tools/stage-authoring-packs.sh will check on the next start.
    image = os.environ.get("MNS_PRODUCT_SHELL_IMAGE", "").strip() or lock["required_images"]["product_shell"]
    print(f"Product shell: {image}")
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
    check_disk_space(download_parent, selected)
    with tempfile.TemporaryDirectory(prefix="mns-demo-packs-", dir=download_parent) as temporary:
        download_root = Path(temporary)
        for pack in selected:
            archive = download_root / pack["asset_name"]
            url = f"{base_url}/{quote(pack['asset_name'], safe='')}"
            print(f"Downloading {pack['display_name']} ({pack['size_bytes'] / 1e6:.0f} MB)...")
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


def _hint_for(exc: BaseException) -> str:
    if isinstance(exc, subprocess.CalledProcessError):
        tool = str(exc.cmd[0]) if exc.cmd else ""
        if tool == "curl":
            return ("could not download from GitHub Releases: check network access to "
                    "github.com, then re-run. MNS_SKIP_PACK_INSTALL=1 make dashboard starts "
                    "the dashboard without packs.")
        if tool == "docker":
            return ("the product shell image could not be pulled or run: `docker login` "
                    "with an account that can read dhdevspace/auto_mns, or run "
                    "./product.sh setup, then re-run. MNS_SKIP_PACK_INSTALL=1 make dashboard "
                    "starts the dashboard without packs.")
    return ""


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        print(f"install-demo-packs: {exc}", file=sys.stderr)
        hint = _hint_for(exc)
        if hint:
            print(f"install-demo-packs: {hint}", file=sys.stderr)
        raise SystemExit(1)
