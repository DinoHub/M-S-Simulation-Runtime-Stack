#!/usr/bin/env python3
"""Download, checksum, and install the standalone-v2 demo packs of one lock.

The lock (packs/*.lock.json, schema mns.pack_release_lock.v1) is the whole
catalog: which packs exist, where each one is published, its size and
checksum, the artifact digest the store indexes on, and the host capability
every pack was cooked for. Two locks are shipped -- the UE 5.8.2 set (default)
and the previous UE 5.5.4 review set -- and `make dashboard CHANNEL=...` picks one together
with its own pack store, authoring data root and host contract.

Roots come from the environment so the Makefile, product.sh and the dashboard
backend all point the installer at the same directories:

    MNS_DEMO_PACK_LOCK                     lock file (default: the v1 (MnS 1.0) lock)
    MNS_PACK_STORE_ROOT                    content-addressed store (default .mns/v1/pack-store)
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
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCK_PATH = ROOT / "packs" / "v1.0.0.lock.json"
DEFAULT_STORE_ROOT = ROOT / ".mns" / "v1" / "pack-store"
DEFAULT_HOST_CONTRACT = ROOT / "packs" / "runtime-host-compatibility.v1.json"
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


def run(command: list[str], *, capture: bool = False,
        stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run a command, surfacing its stderr when it fails.

    With capture_output=True, CalledProcessError swallows the child's stderr:
    a failed `packs install` reported only "returned non-zero exit status 1"
    and the real cause (bad archive, disk full, permission) was lost. Echo the
    captured streams before re-raising so the caller's handler still sees a
    CalledProcessError but the operator sees the reason.
    """
    try:
        return subprocess.run(command, check=True, text=True, capture_output=capture,
                              input=stdin)
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


def install_archive(image: str, archive: Path, expected_digest: str | None, store_root: Path) -> str:
    """Install one verified archive into the store through the pinned product
    shell (`packs install`), which verifies the bundle and returns its
    content-addressed digest. With `expected_digest` (a lock entry) the two must
    agree; without one (a release or a dropped-in archive) the shell's digest is
    the record. Returns the installed artifact digest."""
    uid_gid = host_user()
    result = run(
        ["docker", "run", "--rm", "--network=none", "--user", uid_gid, "-e", "HOME=/tmp",
         "-e", "MNS_WORKSPACE_ROOT=/workspace",
         "-e", f"MNS_PACK_STORE_ROOT={container_path(store_root)}",
         "-v", f"{ROOT}:/workspace:rw",
         "-v", f"{archive.parent}:/mnt/mns/release:ro",
         image, "packs", "install", f"/mnt/mns/release/{archive.name}"],
        capture=True,
    )
    try:
        digest = json.loads(result.stdout)["digest"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise RuntimeError(f"packs install did not report a digest for {archive.name}: {result.stdout[-400:]}") from exc
    if expected_digest and digest != expected_digest:
        raise RuntimeError(
            f"{archive.name} installed as {digest}, but the lock expects {expected_digest}"
        )
    return digest


def pack_release(lock: dict, pack: dict) -> dict:
    """One release per lock (the 5.5.4 review set lives on this repository's
    release) or one release per pack (TEVV-Airsim publishes each pack under
    its own tag): a pack-level `release` overrides the lock-level one."""
    declared = pack.get("release") or lock.get("release")
    if not declared:
        raise RuntimeError(
            f"{pack['id']}@{pack['version']} declares no release, and neither does "
            f"the lock, so there is nothing to download it from. A lock entry "
            f"without a release describes an archive assembled by hand and never "
            f"published: install it into the store directly (tools/pull-packs.sh "
            f"--import), or publish it and rebuild the lock with `make pack-lock`."
        )
    release = dict(declared)
    # A lock written by hand may spell the repository `repo`; every lock
    # build_pack_lock.py produces spells it `repository`. Same meaning.
    if "repository" not in release and "repo" in release:
        release["repository"] = release["repo"]
    return release


def github_token() -> str | None:
    """A token that can read the pack releases, or None.

    GH_TOKEN / GITHUB_TOKEN first (how CI and a headless install supply one),
    then the gh CLI's own token if gh happens to be installed and logged in.
    """
    for name in ("GH_TOKEN", "GITHUB_TOKEN"):
        value = os.environ.get(name)
        if value:
            return value
    if shutil.which("gh"):
        found = subprocess.run(["gh", "auth", "token"], text=True, capture_output=True)
        if found.returncode == 0 and found.stdout.strip():
            return found.stdout.strip()
    return None


def asset_api_url(release: dict, pack: dict, token: str) -> str:
    """Resolve asset_name to the API asset URL that can actually be fetched.

    The obvious URL -- github.com/<repo>/releases/download/<tag>/<name> -- is
    unauthenticated-only. Against a PRIVATE repository (TEVV-Airsim is one) it
    returns 404 whether or not you send a token: that path ignores the
    Authorization header entirely, and GitHub answers 404 rather than 401 so a
    private asset's existence does not leak. Verified all three ways; only
    api.github.com/repos/<repo>/releases/assets/<id> with
    `Accept: application/octet-stream` returns the bytes.

    The lock stores asset_name, not the numeric id, so the id is resolved here
    with one authenticated call per release tag.
    """
    repo, tag = release["repository"], release["tag"]
    listed = run(
        ["curl", "--fail", "--silent", "--show-error", "--location",
         "--config", "-",
         f"https://api.github.com/repos/{repo}/releases/tags/{quote(tag, safe='')}"],
        capture=True,
        stdin=f'header = "Authorization: Bearer {token}"\n'
              f'header = "Accept: application/vnd.github+json"\n',
    )
    try:
        payload = json.loads(listed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{repo}@{tag}: release metadata was not JSON ({exc})") from exc
    for asset in payload.get("assets") or []:
        if asset.get("name") == pack["asset_name"]:
            return asset["url"]
    available = ", ".join(sorted(a.get("name", "?") for a in payload.get("assets") or []))
    raise RuntimeError(
        f"{repo}@{tag} has no asset named {pack['asset_name']!r}. "
        f"Present: {available or '(none)'}"
    )


# A slow link drops long GitHub transfers mid-stream (curl 92 "HTTP/2 stream
# was not closed cleanly", 18 partial file, 56 recv failure), which --retry
# does not cover and which used to throw away a 1.9 GB archive at 55%. Each
# attempt resumes from the bytes already on disk instead.
DOWNLOAD_ATTEMPTS = 8
CURL_HTTP_ERROR = 22  # --fail on a 4xx/5xx: repeating the request will not help


def _download_named_asset(release: dict, asset_name: str, token: str, target: Path) -> None:
    """Fetch one release asset by name, resuming after a dropped connection.
    The token goes in via a curl config on stdin, not as -H on the command
    line, so it never appears in argv or `ps`."""
    url = asset_api_url(release, {"asset_name": asset_name}, token)
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        try:
            run(
                ["curl", "--fail", "--location", "--http1.1", "--retry", "3",
                 "--continue-at", "-", "--config", "-",
                 "--output", str(target), url],
                stdin=f'header = "Authorization: Bearer {token}"\n'
                      f'header = "Accept: application/octet-stream"\n',
            )
            return
        except subprocess.CalledProcessError as exc:
            if exc.returncode == CURL_HTTP_ERROR or attempt == DOWNLOAD_ATTEMPTS:
                raise
            done = target.stat().st_size if target.exists() else 0
            print(f"  connection dropped (curl exit {exc.returncode}) after "
                  f"{done / 1e6:.0f} MB; resuming (attempt {attempt + 1} of "
                  f"{DOWNLOAD_ATTEMPTS})", file=sys.stderr)
            time.sleep(min(5 * attempt, 30))


def download_asset(release: dict, pack: dict, token: str, target: Path) -> None:
    """Fetch a pack archive: one asset, or the split `parts` a release carries
    when the bundle exceeded GitHub's per-asset cap (publish_pack.py splits
    anything over 1900 MB into `<bundle>.part-NNN`). Parts are streamed into
    `target` in order and deleted as they go; the caller verifies the whole
    archive's size and sha256 exactly as for a single asset."""
    parts = pack.get("parts") or []
    if not parts:
        _download_named_asset(release, pack["asset_name"], token, target)
        return
    with target.open("wb") as assembled:
        for index, part_name in enumerate(parts):
            part_path = target.with_name(f"{target.name}.part-{index:03d}.download")
            print(f"  part {index + 1}/{len(parts)}: {part_name}")
            _download_named_asset(release, part_name, token, part_path)
            with part_path.open("rb") as source:
                shutil.copyfileobj(source, assembled, 8 * 1024 * 1024)
            part_path.unlink()


PACK_SUFFIXES = (".mnslevelpack", ".mnsassetpack")


def release_assets(release: dict, token: str) -> list[dict]:
    """List a release's assets through the API (the only path that works for
    private repositories; see asset_api_url)."""
    repo, tag = release["repository"], release["tag"]
    listed = run(
        ["curl", "--fail", "--silent", "--show-error", "--location", "--config", "-",
         f"https://api.github.com/repos/{repo}/releases/tags/{quote(tag, safe='')}"],
        capture=True,
        stdin=f'header = "Authorization: Bearer {token}"\n'
              f'header = "Accept: application/vnd.github+json"\n',
    )
    try:
        payload = json.loads(listed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{repo}@{tag}: release metadata was not JSON ({exc})") from exc
    return list(payload.get("assets") or [])


def pack_entry_from_release(release: dict, token: str, host_id: str, scratch: Path) -> dict:
    """Describe one published pack release (publish_pack.py layout) as a lock-style
    entry so the same download/verify/install path handles it.

    The release carries `artifact.json` (kind, id, version, variants with the
    host ids they were cooked for), `<bundle>.sha256`, and the bundle itself
    or its `.part-NNN` pieces. The content-addressed artifact_digest is not
    published; the product shell computes it while installing, so the entry
    leaves it empty and the installer records what the shell returns.
    """
    assets = {asset["name"]: asset for asset in release_assets(release, token)}
    sha_names = [name for name in assets if name.endswith(".sha256")
                 and name[:-len(".sha256")].endswith(PACK_SUFFIXES)]
    if len(sha_names) != 1 or "artifact.json" not in assets:
        raise RuntimeError(
            f"{release['repository']}@{release['tag']} is not a pack release "
            f"(needs artifact.json and exactly one <bundle>.sha256); assets: "
            f"{', '.join(sorted(assets)) or '(none)'}")
    bundle_name = sha_names[0][:-len(".sha256")]
    scratch.mkdir(parents=True, exist_ok=True)
    _download_named_asset(release, "artifact.json", token, scratch / "artifact.json")
    _download_named_asset(release, sha_names[0], token, scratch / sha_names[0])
    artifact = json.loads((scratch / "artifact.json").read_text(encoding="utf-8"))
    expected_sha = (scratch / sha_names[0]).read_text(encoding="utf-8").split()[0].lower()
    variants = artifact.get("variants") or []
    if not any(v.get("host_compatibility_id") == host_id for v in variants):
        cooked = sorted({str(v.get("host_compatibility_id")) for v in variants})
        raise RuntimeError(
            f"{bundle_name} was not cooked for the selected runtime host {host_id}; "
            f"its variants target {', '.join(cooked) or '(none)'}")
    kind = artifact.get("kind") or ("level" if bundle_name.endswith(".mnslevelpack") else "asset")
    pack_id, version = artifact["pack"]["id"], artifact["pack"]["version"]
    if bundle_name in assets:
        parts, size = [], int(assets[bundle_name]["size"])
    else:
        parts = sorted(name for name in assets if name.startswith(f"{bundle_name}.part-"))
        if not parts:
            raise RuntimeError(f"{release['repository']}@{release['tag']}: neither {bundle_name} nor its parts are attached")
        size = sum(int(assets[name]["size"]) for name in parts)
    return {
        "selection": pack_id,
        "kind": kind,
        "id": pack_id,
        "display_name": pack_id,
        "version": version,
        "asset_name": bundle_name,
        "parts": parts,
        "size_bytes": size,
        "sha256": expected_sha,
        "artifact_digest": "",
        "release": dict(release),
    }


def local_pack_archives(directory: Path) -> list[Path]:
    """Pack archives dropped into the pack mount directory (MNS_PACKS_DIR)."""
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.iterdir()
                  if path.is_file() and path.name.endswith(PACK_SUFFIXES))


def build_parser(lock: dict | None) -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Download, checksum, and install standalone-v2 demo packs.",
        epilog="Selections come from the lock; --lock or MNS_DEMO_PACK_LOCK picks it.",
    )
    result.add_argument("--lock", type=Path, default=env_path("MNS_DEMO_PACK_LOCK", DEFAULT_LOCK_PATH),
                        help="pack lock to install from (default: MNS_DEMO_PACK_LOCK or the UE 5.8.2 lock)")
    result.add_argument("--all", action="store_true", help="Install every level and object pack in the lock")
    result.add_argument("--objects", action="store_true", help="Install every object (asset) pack in the lock")
    for pack in (lock or {}).get("packs", []):
        result.add_argument(f"--{pack['selection']}", action="store_true",
                            help=f"Install {pack['display_name']} ({pack['kind']}, {pack['size_bytes'] / 1e6:.0f} MB)")
    result.add_argument("--dry-run", action="store_true", help="Print selected release assets without downloading")
    result.add_argument("--release-tag", action="append", default=[], metavar="TAG",
                        help="Install a published pack release that is not in the lock (repeatable), "
                             "e.g. pack-level-blocks-1.0.1; see --repo")
    result.add_argument("--repo", default="DinoHub/TEVV-Airsim",
                        help="Repository the --release-tag releases live on (default: %(default)s)")
    result.add_argument("--import", dest="import_dir", nargs="?", const="", default=None, metavar="DIR",
                        help="Install every .mnslevelpack/.mnsassetpack archive found in DIR "
                             "(default: the pack mount directory, MNS_PACKS_DIR)")
    result.add_argument("--remove", action="append", default=[], metavar="SELECTION",
                        help="Uninstall a pack from the store; repeatable. Refuses while a "
                             "generated stack uses it, or while it is staged unless --unstage")
    result.add_argument("--unstage", action="store_true",
                        help="With --remove: also drop it from ScenarioLab's staged index")
    result.add_argument("--remove-orphans", action="store_true",
                        help="Delete blob directories the store index no longer lists")
    result.add_argument("--status", action="store_true",
                        help="One prioritised list: what is locked, what is installed, and "
                             "what has been published since (needs GitHub credentials)")
    result.add_argument("--json", dest="json_out", nargs="?", const="", default=None, metavar="PATH",
                        help="With --status: write the report as JSON (default: "
                             "<store>/../pack-status.json, which the dashboard reads)")
    result.add_argument("--offline", action="store_true",
                        help="With --status: skip the published-version check")
    result.add_argument("--list-remote", action="store_true",
                        help="List the pack releases on --repo cooked for the selected host and exit")
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

    packs_dir = env_path("MNS_PACKS_DIR", ROOT / ".mns" / "packs")
    if args.remove or args.remove_orphans:
        authoring_data = authoring_data_root_for(store_root)
        removed, warnings = ([], [])
        if args.remove:
            removed, warnings = remove_packs(args.remove, lock, store_root, authoring_data,
                                             unstage=args.unstage)
        for warning in warnings:
            print(f"WARNING: {warning}", file=sys.stderr)
        for entry in removed:
            print(f"Removed {entry['id']}@{entry['version']} "
                  f"({entry['freed_bytes'] / 1e6:.1f} MB freed)")
        if args.remove_orphans:
            for orphan in remove_orphan_blobs(store_root):
                print(f"Removed orphan blob {orphan['digest'][:19]}… "
                      f"({orphan['bytes'] / 1e6:.1f} MB)")
        if removed and args.unstage:
            # Rebuild the staged index from what is left rather than editing it:
            # an asset pack is materialised inside every staged environment, so
            # a surgical delete would have to touch each one.
            image = os.environ.get("MNS_PRODUCT_SHELL_IMAGE", "").strip() \
                or lock["required_images"]["product_shell"]
            ensure_image(image)
            return stage(image, store_root)
        return 0

    if args.status:
        cache = env_path("MNS_DEMO_PACK_CACHE_DIR",
                         env_path("MNS_DEMO_PACK_DOWNLOAD_DIR", ROOT / ".mns" / "downloads") / "pack-cache")
        cache.mkdir(parents=True, exist_ok=True)
        status = pack_status(lock, lock_path, store_root, contract_id, args.repo, cache,
                             offline=args.offline)
        if args.json_out is not None:
            # Default beside the store, i.e. inside the channel directory, so the
            # dashboard backend finds it through the roots compose already hands it.
            target = Path(args.json_out).expanduser() if args.json_out else store_root.parent / "pack-status.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
            own_by_host(target)
            print(f"Wrote {target}")
        return render_status(status)

    if args.list_remote:
        return list_remote(args.repo, contract_id)

    selections = {pack["selection"] for pack in lock["packs"]}
    requested = {name for name in selections if getattr(args, name.replace("-", "_"), False)}
    if args.all:
        requested |= selections
    if args.objects:
        requested |= {pack["selection"] for pack in lock["packs"] if pack["kind"] == "asset"}
    extra_mode = bool(args.release_tag) or args.import_dir is not None
    if not requested and not extra_mode:
        argument_parser.error("select --all, --objects, at least one pack, --release-tag TAG, "
                              "--import [DIR] or --list-remote: "
                              + " ".join(f"--{s}" for s in sorted(selections)))
    selected = [pack for pack in lock["packs"] if pack["selection"] in requested]
    # Archives dropped into the pack mount directory need no download at all:
    # verify and install them straight from where they are.
    import_dir = None
    if args.import_dir is not None:
        import_dir = Path(args.import_dir).expanduser().resolve() if args.import_dir else packs_dir
        if args.check or args.missing:
            argument_parser.error("--import cannot be combined with --check/--missing")

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
            fetchable = bool(pack.get("release") or lock.get("release"))
            note = "" if fetchable else "  [no release - cannot be downloaded]"
            print(f"  missing:   {pack['id']}@{pack['version']} ({pack['selection']}){note}")
        unpublished = [pack for pack in missing
                       if not (pack.get("release") or lock.get("release"))]
        if unpublished:
            # A lock entry with a digest but no release can never be satisfied
            # by this installer: it names an archive that was assembled by hand
            # and never published. Saying so here is the difference between
            # "run the installer again" and "someone has to publish this pack".
            print(f"  {len(unpublished)} of {len(missing)} missing pack(s) declare no "
                  f"release and can never be downloaded: "
                  f"{', '.join(pack['id'] for pack in unpublished)}")
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

    image = os.environ.get("MNS_PRODUCT_SHELL_IMAGE", "").strip() or lock["required_images"]["product_shell"]
    if import_dir is not None:
        archives = local_pack_archives(import_dir)
        print(f"Pack mount directory: {import_dir} ({len(archives)} archive(s))")
        if not archives and not selected and not args.release_tag:
            print("  nothing to import; drop .mnslevelpack/.mnsassetpack archives there or use --release-tag")
        if args.dry_run:
            for archive in archives:
                print(f"  import: {archive.name}")
            if not selected and not args.release_tag:
                return 0
        elif archives:
            print(f"Product shell: {image}")
            ensure_image(image)
            store_root.mkdir(parents=True, exist_ok=True)
            own_by_host(store_root, store_root.parent)
            for archive in archives:
                digest = install_archive(image, archive, None, store_root)
                print(f"Installed {archive.name} as {digest}.")
            if not selected and not args.release_tag:
                return stage(image, store_root)

    if args.release_tag:
        token = github_token()
        if not token:
            raise RuntimeError("--release-tag needs GitHub credentials (GH_TOKEN, GITHUB_TOKEN, or `gh auth login`)")
        scratch_root = env_path("MNS_DEMO_PACK_DOWNLOAD_DIR", ROOT / ".mns" / "downloads") / "release-receipts"
        for tag in args.release_tag:
            release = {"repository": args.repo, "tag": tag}
            selected.append(pack_entry_from_release(release, token, contract_id, scratch_root / tag))

    print(f"Lock: {lock_path.name} -> {store_root}")
    for pack in selected:
        release = pack.get("release") or lock["release"]
        origin = f"{len(pack['parts'])} parts" if pack.get("parts") else pack["asset_name"]
        print(f"  {pack['selection']}: {origin} from {release['repository']}@{release['tag']} "
              f"({pack['artifact_digest'] or 'digest recorded at install'})")
    if args.dry_run:
        return 0

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
    # Resolved once, before anything is fetched, so a missing credential fails
    # immediately and by name instead of 404-ing on the first pack. Only needed
    # when something actually has to be downloaded -- a fully cached run, and
    # every --check/--dry-run run, stays credential-free.
    token = github_token() if to_download else None
    if to_download and not token:
        repos = sorted({pack_release(lock, pack)["repository"] for pack in to_download})
        raise RuntimeError(
            "no GitHub credentials, and the pack releases are on private "
            f"repositories ({', '.join(repos)}), which answer 404 rather than 401 "
            "to an anonymous client. Set GH_TOKEN (or GITHUB_TOKEN) to a token with "
            "read access, or run `gh auth login`."
        )

    # The product shell that runs `packs install` and `packs stage-authoring`.
    # The lock pins the release's image so a standalone CLI run is exact; the
    # Makefile exports the image of the selected channel/IMAGE_MODE instead,
    # so the dashboard's install and staging steps use one shell and the
    # ResolvedPacks/.staged-with stamp agrees with what
    # tools/stage-authoring-packs.sh will check on the next start.
    print(f"Product shell: {image}")
    ensure_image(image)
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
                download_asset(pack_release(lock, pack), pack, token, archive)
                if archive.stat().st_size != pack["size_bytes"]:
                    raise RuntimeError(f"size mismatch for {archive.name}")
                actual_sha256 = sha256_file(archive)
                if actual_sha256 != pack["sha256"]:
                    raise RuntimeError(
                        f"SHA-256 mismatch for {archive.name}: "
                        f"{actual_sha256} != {pack['sha256']}"
                    )
            try:
                digest = install_archive(image, archive, pack["artifact_digest"] or None, store_root)
            finally:
                # Free each downloaded archive as soon as it is installed.
                # Holding all of them until the TemporaryDirectory unwound
                # meant --all needed the full download at once, on top of
                # the copies written into the store. Cached archives stay.
                if archive != cached:
                    archive.unlink(missing_ok=True)
            print(f"Installed {pack['id']}@{pack['version']} ({digest}).")

    return stage(image, store_root)


# ---------------------------------------------------------------------------
# Removal
# ---------------------------------------------------------------------------

def pack_references(digest: str, authoring_data: Path, workspace: Path | None = None) -> dict:
    """Everything that still points at a pack digest.

    Liveness is read out of the index files, never inferred from inode link
    counts: v1.0.0's product shell hard-links payloads out of the store, so a
    link count says how the bytes are shared, not whether anything still needs
    the pack.

    Four holders, in descending order of how much a removal would hurt:
    a generated stack (launchable now), ScenarioLab's staged tree (the editor
    reads it on start), an exported ScenarioSpec or its AssetPacks.yaml (a
    record of what was authored), and the channel lock (reinstallable).
    """
    workspace = workspace or ROOT
    stacks = []
    for resolved in sorted(workspace.glob("generated/*/config/content-packs/resolved-pack-set.json")):
        try:
            document = json.loads(resolved.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        entries = [document.get("environment") or {}, *(document.get("asset_packs") or [])]
        if any(str(entry.get("artifact_digest")) == digest for entry in entries):
            stacks.append(resolved.parents[2].name)

    staged = False
    index = authoring_data / "ResolvedPacks" / "index.json"
    try:
        document = json.loads(index.read_text(encoding="utf-8"))
        staged = any(str(entry.get("artifact_digest")) == digest
                     for kind in ("level_packs", "asset_packs")
                     for entry in (document.get(kind) or []))
    except (OSError, json.JSONDecodeError):
        pass

    # A ScenarioSpec records the digest it was authored against, and a split
    # spec keeps its asset packs in a sibling file. Substring search rather than
    # a YAML parse: this only decides whether to warn, and it costs no new
    # dependency in a script that has none.
    specs = []
    for candidate in sorted(workspace.glob("scenarios/*/*.yaml")):
        try:
            if digest in candidate.read_text(encoding="utf-8"):
                specs.append(str(candidate.relative_to(workspace)))
        except OSError:
            continue

    return {"generated_stacks": stacks, "staged": staged, "scenario_files": specs}


def directory_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def remove_packs(selections: list[str], lock: dict, store_root: Path, authoring_data: Path,
                 unstage: bool = False, workspace: Path | None = None) -> tuple[list[dict], list[str]]:
    """Delete packs from the store, refusing while something still needs them.

    The store format is the platform's: `apps/scenario_launcher/launcher.py`
    owns `packs install` and `mns_contentpacks.registry.PackStore` owns the
    layout. Removal lives here because the alternative is a platform change plus
    a product-shell rebuild and republish, which only the release owner can do.
    PackStore.remove() exists there but is unreachable and index-only, so it
    would leave the blob behind as an orphan.
    """
    index_path = store_root / "index.json"
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"no readable pack store at {index_path}: {exc}") from exc

    by_selection = {pack["selection"]: pack for pack in lock["packs"]}
    removed, warnings = [], []
    for selection in selections:
        pack = by_selection.get(selection)
        if pack is None:
            raise RuntimeError(f"{selection!r} is not in {', '.join(sorted(by_selection))}")
        digest = str(pack["artifact_digest"])
        entry = next((e for e in index.get("packs") or [] if str(e.get("digest")) == digest), None)
        if entry is None:
            warnings.append(f"{pack['id']}@{pack['version']} is not installed; nothing to remove")
            continue

        references = pack_references(digest, authoring_data, workspace)
        if references["generated_stacks"]:
            raise RuntimeError(
                f"{pack['id']}@{pack['version']} is used by generated stack(s) "
                f"{', '.join(references['generated_stacks'])} — removing it would break a "
                f"stack you can still launch. Delete the stack first, or keep the pack.")
        if references["staged"] and not unstage:
            raise RuntimeError(
                f"{pack['id']}@{pack['version']} is staged for ScenarioLab — pass --unstage to "
                f"remove it and re-stage what is left.")
        for spec in references["scenario_files"]:
            warnings.append(f"{spec} was authored against {pack['id']}@{pack['version']}; "
                            f"it will not generate until the pack is reinstalled")

        blob = store_root / str(entry.get("blob") or f"blobs/sha256/{digest.removeprefix('sha256:')}")
        before = shutil.disk_usage(store_root).free
        if blob.is_dir():
            shutil.rmtree(blob)
        index["packs"] = [e for e in index["packs"] if str(e.get("digest")) != digest]
        index_path.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        freed = max(0, shutil.disk_usage(store_root).free - before)
        removed.append({"id": pack["id"], "version": pack["version"], "selection": selection,
                        "digest": digest, "freed_bytes": freed})
    return removed, warnings


def remove_orphan_blobs(store_root: Path) -> list[dict]:
    """Blob directories the index no longer lists.

    Nothing has ever produced one, because nothing has ever removed a pack. The
    platform's unreachable PackStore.remove() would start: it drops the index
    row and leaves the blob. Report them here so that never goes unnoticed.
    """
    blobs = store_root / "blobs" / "sha256"
    if not blobs.is_dir():
        return []
    try:
        index = json.loads((store_root / "index.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    known = {str(e.get("digest", "")).removeprefix("sha256:") for e in index.get("packs") or []}
    orphans = []
    for candidate in sorted(blobs.iterdir()):
        if candidate.is_dir() and candidate.name not in known:
            size = directory_size(candidate)
            shutil.rmtree(candidate)
            orphans.append({"digest": f"sha256:{candidate.name}", "bytes": size})
    return orphans


# ---------------------------------------------------------------------------
# Status: the join nothing else does
# ---------------------------------------------------------------------------

def pack_status(lock: dict, lock_path: Path, store_root: Path, host_id: str,
                repo: str, cache: Path, offline: bool = False) -> dict:
    """Join the three views of a pack: locked, installed, and published.

    `--check` compares the lock against the store and never looks remotely;
    `--list-remote` looks remotely and never reads the lock or the store.
    Neither answers the question an operator actually has, which is whether
    anything newer exists than what they are running. This joins all three.

    The remote half needs GitHub credentials, which only a host shell has (the
    dashboard backend and the product shell ship no `gh`), so `offline` drops
    it and reports the two local views alone rather than implying currency.
    """
    installed = installed_digests(store_root)
    latest: dict[tuple[str, str], dict[str, str]] = {}
    remote_error = ""
    if not offline:
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import build_pack_lock
            latest = build_pack_lock.discover_latest(repo, host_id, cache, quiet=True)
        except Exception as exc:                      # noqa: BLE001 - reported, not raised
            remote_error = f"{type(exc).__name__}: {exc}"

    from build_pack_lock import version_key

    rows = []
    for pack in lock["packs"]:
        digest = str(pack.get("artifact_digest") or "")
        newest = latest.get((pack["kind"], pack["id"])) or {}
        newer = bool(newest) and version_key(newest["version"]) > version_key(pack["version"])
        rows.append({
            "selection": pack["selection"],
            "id": pack["id"],
            "kind": pack["kind"],
            "locked_version": str(pack["version"]),
            "installed": digest in installed,
            "fetchable": bool(pack.get("release") or lock.get("release")),
            "latest_version": newest.get("version", ""),
            "latest_tag": newest.get("tag", ""),
            "update_available": newer,
        })

    locked_digests = {str(p.get("artifact_digest") or "") for p in lock["packs"]}
    unlocked = sorted(d for d in installed if d not in locked_digests)
    return {
        "schema": "mns.pack_status.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "channel": os.environ.get("MNS_CHANNEL", "").strip(),
        "capability_id": host_id,
        "lock": str(lock_path),
        "store": str(store_root),
        "remote_checked": not offline and not remote_error,
        "remote_error": remote_error,
        "packs": rows,
        "installed_not_locked": unlocked,
    }


def render_status(status: dict) -> int:
    """Print the status as one prioritised list, like tools/images.sh status.

    Same shape on purpose: NEEDS YOU first with a remediation per line, FYI
    after, and a nonzero exit when the first list is non-empty so CI can gate
    on it the way `make verify-images` gates on the image catalog.
    """
    rows = status["packs"]
    newer = [r for r in rows if r["update_available"]]
    absent = [r for r in rows if not r["installed"] and r["fetchable"]]
    unfetchable = [r for r in rows if not r["installed"] and not r["fetchable"]]
    current = [r for r in rows if r["installed"] and not r["update_available"]]

    print(f"Channel {status['channel'] or '(unset)'} -> {status['store']}")
    print(f"Lock {status['lock']}")
    if not status["remote_checked"]:
        why = status["remote_error"] or "not checked (offline)"
        print(f"Published versions NOT checked: {why}")

    total = len(newer) + len(absent) + len(unfetchable)
    print(f"\nNEEDS YOU ({total})")
    if newer:
        print(f"\n  newer published ({len(newer)}) - `make pack-lock`, review the diff, commit")
        for r in newer:
            print(f"    {r['id']}@{r['locked_version']} -> {r['latest_version']}  ({r['latest_tag']})")
    if absent:
        print(f"\n  locked but not installed ({len(absent)}) - tools/pull-packs.sh --all")
        for r in absent:
            print(f"    {r['id']}@{r['locked_version']} ({r['selection']})")
    if unfetchable:
        print(f"\n  no release ({len(unfetchable)}) - cannot be downloaded; import the "
              f"archive or publish it and relock")
        for r in unfetchable:
            print(f"    {r['id']}@{r['locked_version']} ({r['selection']})")
    if not total:
        print("  nothing")

    fyi = len(current) + len(status["installed_not_locked"])
    print(f"\nFYI ({fyi}) - known and deliberate, no action")
    if current:
        print(f"\n  current ({len(current)})")
        for r in current:
            print(f"    {r['id']}@{r['locked_version']}")
    if status["installed_not_locked"]:
        print(f"\n  installed but not in this lock ({len(status['installed_not_locked'])}) - "
              f"pulled by --release-tag or --import")
        for digest in status["installed_not_locked"]:
            print(f"    {digest}")
    return 1 if total else 0


def list_remote(repo: str, host_id: str) -> int:
    """Print every pack-* release on `repo` cooked for `host_id` (newest first per pack)."""
    token = github_token()
    if not token:
        raise RuntimeError("--list-remote needs GitHub credentials (GH_TOKEN, GITHUB_TOKEN, or `gh auth login`)")
    listed = run(["gh", "release", "list", "-R", repo, "--limit", "200", "--json", "tagName,isDraft,createdAt"],
                 capture=True)
    tags = [r["tagName"] for r in json.loads(listed.stdout) if not r.get("isDraft") and r["tagName"].startswith("pack-")]
    scratch = env_path("MNS_DEMO_PACK_DOWNLOAD_DIR", ROOT / ".mns" / "downloads") / "release-receipts"
    print(f"Pack releases on {repo} cooked for {host_id}:")
    shown = 0
    for tag in sorted(tags):
        receipts = scratch / tag
        try:
            if not (receipts / "artifact.json").is_file():
                receipts.mkdir(parents=True, exist_ok=True)
                _download_named_asset({"repository": repo, "tag": tag}, "artifact.json", token, receipts / "artifact.json")
            artifact = json.loads((receipts / "artifact.json").read_text(encoding="utf-8"))
        except (RuntimeError, OSError, json.JSONDecodeError):
            continue
        if any(v.get("host_compatibility_id") == host_id for v in artifact.get("variants") or []):
            print(f"  {tag}  ({artifact.get('kind')} {artifact['pack']['id']}@{artifact['pack']['version']})")
            shown += 1
    if not shown:
        print("  (none)")
    return 0


def authoring_data_root_for(store_root: Path) -> Path:
    """Where ScenarioLab's resolved index belongs for this store.

    A store and an authoring data root are the two halves of one channel --
    the Makefile's CHANNEL_ENV_EXPORTS always sets MNS_PACK_STORE_ROOT and
    MNS_AUTHORING_DATA_ROOT together -- but stage-authoring-packs.sh defaults
    them independently, each to its own hardcoded `.mns/ue582/...`. Passing
    only the store therefore staged into the ue582 channel whatever store was
    installed into, rewriting that channel's index to describe packs it does
    not have. Deriving the sibling root here keeps the two from disagreeing:
    `<channel>/pack-store` pairs with `<channel>/authoring-data`.
    """
    configured = os.environ.get("MNS_AUTHORING_DATA_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser()
    return store_root.parent / "authoring-data"


def stage(image: str, store_root: Path) -> int:
    """Refresh ScenarioLab's resolved index for the store with the same shell
    that installed into it (tools/stage-authoring-packs.sh)."""
    environment = os.environ.copy()
    environment["MNS_PRODUCT_SHELL_IMAGE"] = image
    environment["MNS_PACK_STORE_ROOT"] = str(store_root)
    environment["MNS_AUTHORING_DATA_ROOT"] = str(authoring_data_root_for(store_root))
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
        if tool == "curl" and exc.returncode != CURL_HTTP_ERROR:
            return (f"the download from GitHub kept failing (curl exit {exc.returncode}): "
                    "a network problem, not an access one. Re-run the same command; "
                    "packs already installed are kept.")
        if tool == "curl":
            return ("could not fetch from GitHub Releases. The pack releases are on "
                    "PRIVATE repositories, and GitHub answers 404 (not 401) to a client "
                    "with no credentials -- so a 404 here usually means the token is "
                    "missing, expired, or lacks access, NOT that the asset is gone. "
                    "Check `gh auth status` or GH_TOKEN, then network access to "
                    "github.com and api.github.com. MNS_SKIP_PACK_INSTALL=1 make dashboard "
                    "starts the dashboard without packs.")
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
