#!/usr/bin/env python3
"""Download, checksum, and install the standalone-v2 demo packs of one lock.

The lock (packs/*.lock.json, schema mns.pack_release_lock.v1) is the whole
catalog: which packs exist, where each one is published, its size and
checksum, the artifact digest the store indexes on, and the host capability
every pack was cooked for. Two locks are shipped -- the UE 5.5.4 review set and
the UE 5.8.2 candidate -- and `make dashboard CHANNEL=...` picks one together
with its own pack store, authoring data root and host contract.

Roots come from the environment so the Makefile, product.sh and the dashboard
backend all point the installer at the same directories:

    MNS_DEMO_PACK_LOCK                     lock file (default: the 5.5.4 review lock)
    MNS_PACK_STORE_ROOT                    content-addressed store (default .mns/pack-store)
    MNS_RUNTIME_HOST_COMPATIBILITY_CONTRACT contract whose id must equal the lock's
    MNS_PRODUCT_SHELL_IMAGE                shell used for `packs install` / staging
    MNS_DEMO_PACK_DOWNLOAD_DIR             scratch space for downloads
    MNS_DEMO_PACK_CACHE_DIR                archives already fetched (reused, never deleted)
"""
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
DEFAULT_LOCK_PATH = ROOT / "packs" / "standalone-v2-review.1.lock.json"
DEFAULT_STORE_ROOT = ROOT / ".mns" / "pack-store"
DEFAULT_HOST_CONTRACT = ROOT / "packs" / "runtime-host-compatibility.json"
# Archive on disk plus the copy `packs install` writes into the store, and
# some slack for the index and temporary files.
DISK_HEADROOM_BYTES = 512 * 1024 * 1024


def env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name, "").strip()
    return Path(raw).expanduser().resolve() if raw else default.resolve()


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


def container_path(host_path: Path) -> str:
    """Where a path under this checkout appears inside the product shell,
    which mounts the checkout at /workspace. Anything outside cannot be
    reached by the shell at all, which is why the store must live in here."""
    try:
        return f"/workspace/{host_path.resolve().relative_to(ROOT).as_posix()}"
    except ValueError as exc:
        raise RuntimeError(f"{host_path} is outside the checkout {ROOT}; the product "
                           f"shell only sees paths under it") from exc


def installed_digests(store_root: Path) -> set[str]:
    """artifact digests of every pack in the store; empty when there is no store."""
    index = store_root / "index.json"
    if not index.is_file():
        return set()
    try:
        packs = json.loads(index.read_text(encoding="utf-8")).get("packs") or []
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


def host_user() -> str:
    """uid:gid the pack containers run as. The dashboard backend runs this
    script as root inside its container; MNS_HOST_UID/GID (what compose hands
    it) keep the store and staged files owned by the operator on the host."""
    uid = os.environ.get("MNS_HOST_UID", "").strip() or str(os.getuid())
    gid = os.environ.get("MNS_HOST_GID", "").strip() or str(os.getgid())
    return f"{uid}:{gid}"


def own_by_host(*paths: Path) -> None:
    """When running as root with a host user configured, hand freshly made
    directories to that user so the next host-side `make dashboard` can write
    into them. A no-op for the normal host-user run."""
    if os.getuid() != 0:
        return
    uid = os.environ.get("MNS_HOST_UID", "").strip()
    gid = os.environ.get("MNS_HOST_GID", "").strip()
    if not uid.isdigit() or not gid.isdigit():
        return
    for path in paths:
        try:
            os.chown(path, int(uid), int(gid))
        except OSError:
            pass


def install_archive(image: str, archive: Path, expected_digest: str, store_root: Path) -> None:
    result = run(
        [
            "docker", "run", "--rm", "--user", host_user(),
            "-e", "HOME=/tmp",
            "-e", "MNS_WORKSPACE_ROOT=/workspace",
            "-e", f"MNS_PACK_STORE_ROOT={container_path(store_root)}",
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


def pack_url(lock: dict, pack: dict) -> str:
    """One release per lock (the 5.5.4 review set lives on this repository's
    release) or one release per pack (TEVV-Airsim publishes each pack under
    its own tag): a pack-level `release` overrides the lock-level one."""
    release = pack.get("release") or lock["release"]
    return (
        f"https://github.com/{release['repository']}/releases/download/"
        f"{quote(release['tag'], safe='')}/{quote(pack['asset_name'], safe='')}"
    )


def build_parser(lock: dict | None) -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Download, checksum, and install standalone-v2 demo packs.",
        epilog="Selections come from the lock; --lock or MNS_DEMO_PACK_LOCK picks it.",
    )
    result.add_argument("--lock", type=Path, default=env_path("MNS_DEMO_PACK_LOCK", DEFAULT_LOCK_PATH),
                        help="pack lock to install from (default: MNS_DEMO_PACK_LOCK or the 5.5.4 review lock)")
    result.add_argument("--all", action="store_true", help="Install every level and object pack in the lock")
    result.add_argument("--objects", action="store_true", help="Install every object (asset) pack in the lock")
    for pack in (lock or {}).get("packs", []):
        result.add_argument(f"--{pack['selection']}", action="store_true",
                            help=f"Install {pack['display_name']} ({pack['kind']}, {pack['size_bytes'] / 1e6:.0f} MB)")
    result.add_argument("--dry-run", action="store_true", help="Print selected release assets without downloading")
    result.add_argument(
        "--missing", action="store_true",
        help="Skip packs already in the store (what `make dashboard` runs); "
             "exits 0 without touching the registry when nothing is missing",
    )
    result.add_argument(
        "--check", action="store_true",
        help="Report which selected packs are installed and exit 1 if any is missing; "
             "offline, no docker",
    )
    return result


def main(argv: list[str] | None = None) -> int:
    # Two-phase parse: the lock decides which --<selection> flags exist.
    pre, _ = build_parser(None).parse_known_args(argv)
    lock_path = pre.lock.resolve()
    if not lock_path.is_file():
        raise RuntimeError(f"pack lock does not exist: {lock_path}")
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    argument_parser = build_parser(lock)
    args = argument_parser.parse_args(argv)

    # The runtime host's frozen capability contract (packs/README.md). The
    # lock's capability_id must be this document's id: the generator validates
    # every resolved pack against the contract, and a pack cooked for another
    # host id would install fine here and fail at generate time instead.
    contract_path = env_path("MNS_RUNTIME_HOST_COMPATIBILITY_CONTRACT",
                             ROOT / lock["host_contract"] if lock.get("host_contract") else DEFAULT_HOST_CONTRACT)
    contract_id = json.loads(contract_path.read_text(encoding="utf-8")).get("id")
    if lock.get("capability_id") != contract_id:
        raise RuntimeError(
            f"{lock_path.name} is for capability {lock.get('capability_id')!r} but "
            f"{contract_path} declares {contract_id!r}; the packs would not "
            f"mount on the selected runtime host (see packs/README.md)"
        )
    store_root = env_path("MNS_PACK_STORE_ROOT", DEFAULT_STORE_ROOT)

    selections = {pack["selection"] for pack in lock["packs"]}
    requested = {name for name in selections if getattr(args, name.replace("-", "_"), False)}
    if args.all:
        requested |= selections
    if args.objects:
        requested |= {pack["selection"] for pack in lock["packs"] if pack["kind"] == "asset"}
    if not requested:
        argument_parser.error("select --all, --objects, or at least one pack: "
                              + " ".join(f"--{s}" for s in sorted(selections)))
    selected = [pack for pack in lock["packs"] if pack["selection"] in requested]

    if args.check or args.missing:
        installed = installed_digests(store_root)
        present = [pack for pack in selected if pack["artifact_digest"] in installed]
        missing = [pack for pack in selected if pack["artifact_digest"] not in installed]
        store_note = str(store_root) if (store_root / "index.json").is_file() else f"{store_root} (no store yet)"
        print(f"Pack store: {store_note}")
        print(f"Lock: {lock_path.relative_to(ROOT) if lock_path.is_relative_to(ROOT) else lock_path} "
              f"({lock['capability_id']})")
        for pack in present:
            print(f"  installed: {pack['id']}@{pack['version']} ({pack['selection']})")
        for pack in missing:
            print(f"  missing:   {pack['id']}@{pack['version']} ({pack['selection']})")
        if args.check:
            return 0 if not missing else 1
        if not missing:
            print(f"All {len(selected)} selected demo packs are already installed.")
            if args.dry_run:
                return 0
            # Nothing to fetch, but staging still runs: a store filled by an
            # earlier run that died before its staging step (or by the
            # dashboard while ScenarioLab's index was stale) is exactly the
            # case where "already installed" and "ready for ScenarioLab"
            # differ. stage-authoring-packs.sh is a no-op when the index is
            # current, so a re-run costs one mtime comparison.
            return stage(os.environ.get("MNS_PRODUCT_SHELL_IMAGE", "").strip()
                         or lock["required_images"]["product_shell"], store_root)
        selected = missing

    print(f"Lock: {lock_path.name} -> {store_root}")
    for pack in selected:
        release = pack.get("release") or lock["release"]
        print(f"  {pack['selection']}: {pack['asset_name']} from {release['repository']}@{release['tag']} "
              f"({pack['artifact_digest']})")
    if args.dry_run:
        return 0

    # The product shell that runs `packs install` and `packs stage-authoring`.
    # The lock pins the release's image so a standalone CLI run is exact; the
    # Makefile exports the image of the selected channel/IMAGE_MODE instead,
    # so the dashboard's install and staging steps use one shell and the
    # ResolvedPacks/.staged-with stamp agrees with what
    # tools/stage-authoring-packs.sh will check on the next start.
    image = os.environ.get("MNS_PRODUCT_SHELL_IMAGE", "").strip() or lock["required_images"]["product_shell"]
    print(f"Product shell: {image}")
    ensure_image(image)
    # Stage downloads inside the repo, not $TMPDIR. --all fetches gigabytes
    # and on the common systemd layout /tmp is a tmpfs sized at half of RAM,
    # so the default location ENOSPC'd partway through and discarded
    # everything already fetched. .mns/ is gitignored and is where the pack
    # store lives anyway, so it is on the same filesystem the install needs
    # space on. MNS_DEMO_PACK_DOWNLOAD_DIR overrides it.
    download_parent = env_path("MNS_DEMO_PACK_DOWNLOAD_DIR", ROOT / ".mns" / "downloads")
    download_parent.mkdir(parents=True, exist_ok=True)
    store_root.mkdir(parents=True, exist_ok=True)
    own_by_host(download_parent, store_root, store_root.parent)
    # An archive already fetched (tools/build_pack_lock.py, or a previous
    # attempt that kept its cache) is reused when its checksum matches; the
    # cache is never written to or deleted from here.
    cache_dir = env_path("MNS_DEMO_PACK_CACHE_DIR", download_parent / "pack-cache")
    to_download = [pack for pack in selected
                   if not (cache_dir / pack["asset_name"]).is_file()]
    check_disk_space(download_parent, to_download)
    with tempfile.TemporaryDirectory(prefix="mns-demo-packs-", dir=download_parent) as temporary:
        download_root = Path(temporary)
        for pack in selected:
            cached = cache_dir / pack["asset_name"]
            archive = download_root / pack["asset_name"]
            if cached.is_file() and cached.stat().st_size == pack["size_bytes"] \
                    and sha256_file(cached) == pack["sha256"]:
                print(f"Using cached {pack['display_name']} ({cached})")
                archive = cached
            else:
                print(f"Downloading {pack['display_name']} ({pack['size_bytes'] / 1e6:.0f} MB)...")
                run([
                    "curl", "--fail", "--location", "--retry", "3",
                    "--output", str(archive), pack_url(lock, pack),
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
                install_archive(image, archive, pack["artifact_digest"], store_root)
            finally:
                # Free each downloaded archive as soon as it is installed.
                # Holding all of them until the TemporaryDirectory unwound
                # meant --all needed the full download at once, on top of
                # the copies written into the store. Cached archives stay.
                if archive != cached:
                    archive.unlink(missing_ok=True)
            print(f"Installed {pack['id']}@{pack['version']}.")

    return stage(image, store_root)


def stage(image: str, store_root: Path) -> int:
    """Refresh ScenarioLab's resolved index for the store with the same shell
    that installed into it (tools/stage-authoring-packs.sh)."""
    environment = os.environ.copy()
    environment["MNS_PRODUCT_SHELL_IMAGE"] = image
    environment["MNS_PACK_STORE_ROOT"] = str(store_root)
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
