#!/usr/bin/env python3
"""tools/images.py — one canonical image catalog: render, verify, report, bump.

See images/catalog.yaml (the single authored source) and
docs/adr/0002-one-image-catalog.md (why this exists). Subcommands:

  sync     regenerate every generated artifact from images/catalog.yaml (offline)
  verify   regenerate into a tmp dir, diff against the committed artifacts,
           exit 1 on any difference (offline — the CI gate)
  report   for each non-local image, show whether a newer tag/digest exists
           (online: Docker Hub v2 API for channel review/moving, `docker
           buildx imagetools inspect` for resolver: imagetools rows)
  bump     rewrite images/catalog.yaml LINE-TARGETED (never yaml.dump — that
           would destroy every `purpose:` comment) for rows that are behind,
           then re-parse and assert the structure. Only channel review/moving
           rows are ever written; upstream/local are refused.

Two small internal helpers, used by tools/images.sh's bash-side drift/baked
logic rather than meant for interactive use:

  resolve-var VAR       print the resolved ref product-images.env would carry
                        for VAR (reads generated product-images.env)
  baked-pins KEY        for an image row with a `bakes:` list (e.g.
                        dashboard_backend), print `<VAR>_DEFAULT<TAB><ref>`
                        for each baked row, so baked-pin checking follows the
                        catalog instead of hardcoded var names
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("tools/images.py needs pyyaml (pip install -r tools/requirements.txt)")

ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = ROOT / "images" / "catalog.yaml"
TEMPLATE_PATH = ROOT / "images" / "product-images.env.tmpl"
PRODUCT_ENV_PATH = ROOT / "product-images.env"
IMAGE_SET_PATH = ROOT / "images" / "image-set.generated.yaml"
PLATFORM_ENV_PATH = ROOT / "images" / "platform-images.generated.env"
ENV_EXAMPLE_PATH = ROOT / ".env.example"

GENERATED_MARKER = (
    "# GENERATED from images/catalog.yaml — see docs/adr/0002-one-image-catalog.md. "
    "Do not hand-edit; run tools/images.sh sync."
)

VALID_CHANNELS = {"review", "moving", "upstream", "local"}
VALID_RESOLVERS = {"hub", "imagetools"}

COMPOSE_FILE_FOR_GROUP = {
    "monitoring": "docker-compose-monitoring.yml",
    "metrics": "docker-compose-metrics.yml",
    "logs": "docker-compose-logs.yml",
    "dashboard": "docker-compose-dashboard.yml, inline images",
}


class CatalogError(SystemExit):
    def __init__(self, msg: str):
        super().__init__(f"images/catalog.yaml: {msg}")


# --------------------------------------------------------------------------
# Loading + validation
# --------------------------------------------------------------------------

def load_catalog(path: Path = CATALOG_PATH) -> dict[str, Any]:
    if not path.is_file():
        raise CatalogError(f"not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise CatalogError("must be a mapping at the top level")
    if data.get("schema") != "mns.images.v1":
        raise CatalogError(f"schema must be mns.images.v1, got {data.get('schema')!r}")
    images = data.get("images")
    if not isinstance(images, dict) or not images:
        raise CatalogError("images: must be a non-empty mapping")
    for key, row in images.items():
        if not isinstance(row, dict):
            raise CatalogError(f"images.{key} must be a mapping")
        for field in ("repo", "tag", "channel", "purpose"):
            if field not in row:
                raise CatalogError(f"images.{key} missing required field {field!r}")
        if "digest" not in row:
            raise CatalogError(f"images.{key} missing required field 'digest' (use null)")
        if row["channel"] not in VALID_CHANNELS:
            raise CatalogError(f"images.{key}.channel {row['channel']!r} not in {VALID_CHANNELS}")
        resolver = row.get("resolver", "hub")
        if resolver not in VALID_RESOLVERS:
            raise CatalogError(f"images.{key}.resolver {resolver!r} not in {VALID_RESOLVERS}")
        if row["channel"] == "local" and row["digest"] is not None:
            raise CatalogError(f"images.{key} is channel local but digest is not null")
        if row["channel"] == "local" and not str(row["repo"]).startswith("local/"):
            raise CatalogError(f"images.{key} is channel local but repo does not start with local/")
    consumers = data.get("consumers")
    if not isinstance(consumers, dict):
        raise CatalogError("consumers: must be a mapping")
    for group_name in ("product_env", "image_sets", "compose_env"):
        if group_name not in consumers:
            raise CatalogError(f"consumers.{group_name} missing")
    return data


def image_ref(images: dict[str, Any], key: str) -> str:
    if key not in images:
        raise CatalogError(f"consumer references unknown image key {key!r}")
    row = images[key]
    ref = f"{row['repo']}:{row['tag']}"
    if row.get("digest"):
        ref = f"{ref}@{row['digest']}"
    return ref


# --------------------------------------------------------------------------
# Renderers — each returns the exact text of one generated artifact
# --------------------------------------------------------------------------

def render_product_env(catalog: dict[str, Any]) -> str:
    images = catalog["images"]
    groups = catalog["consumers"]["product_env"]
    tmpl = TEMPLATE_PATH.read_text(encoding="utf-8")

    def fill(match: "re.Match[str]") -> str:
        group = match.group(1)
        if group not in groups:
            raise CatalogError(f"product-images.env.tmpl references unknown group {group!r}")
        lines = [f"{var}={image_ref(images, key)}" for var, key in groups[group].items()]
        return "\n".join(lines)

    body = re.sub(r"@@GROUP:([a-zA-Z0-9_]+)@@", fill, tmpl)
    return GENERATED_MARKER + "\n" + body


def _resolve_image_set(images: dict[str, Any], raw: dict[str, Any],
                        base: dict[str, Any] | None) -> dict[str, Any]:
    """Deep-merge `raw.images` (key -> catalog key) over `base` (already-resolved
    refs), matching MnS-Integration-Platform's merge_dicts (scalars replaced
    wholesale, mappings merged key-by-key)."""
    def merge(base_node: Any, overlay_keys: Any) -> Any:
        if isinstance(overlay_keys, dict):
            result = dict(base_node) if isinstance(base_node, dict) else {}
            for k, v in overlay_keys.items():
                result[k] = merge(result.get(k), v)
            return result
        # overlay_keys is a leaf: a catalog image key -> resolve to a ref
        return image_ref(images, overlay_keys)

    resolved = merge(base or {}, raw.get("images") or {})
    return {
        "pull_policy": raw.get("pull_policy", "if_not_present"),
        "images": resolved,
    }


def resolved_image_sets(catalog: dict[str, Any]) -> dict[str, dict[str, Any]]:
    images = catalog["images"]
    sets_cfg = catalog["consumers"]["image_sets"]
    resolved: dict[str, dict[str, Any]] = {}
    # two passes so `inherits` can point at a set defined earlier or later
    pending = dict(sets_cfg)
    order = list(pending.keys())
    for name in order:
        raw = pending[name]
        base_name = raw.get("inherits")
        base = resolved.get(base_name, {}).get("images") if base_name else None
        if base_name and base is None:
            base_raw = pending.get(base_name)
            if base_raw is None:
                raise CatalogError(f"image_sets.{name} inherits unknown set {base_name!r}")
            resolved[base_name] = _resolve_image_set(images, base_raw, None)
            base = resolved[base_name]["images"]
        resolved[name] = _resolve_image_set(images, raw, base)
    return resolved


def render_image_set(catalog: dict[str, Any]) -> str:
    header = (
        "schema: mns.image_sets.v1\n\n"
        f"{GENERATED_MARKER}\n"
        "# Overlay via MNS_IMAGE_SET_FILE (MnS-Integration-Platform stackgen's\n"
        "# existing overlay hook — see docs/adr/0002-one-image-catalog.md).\n\n"
    )
    body = {"image_sets": resolved_image_sets(catalog)}
    return header + yaml.safe_dump(body, sort_keys=False, default_flow_style=False)


def render_platform_env(catalog: dict[str, Any]) -> str:
    images = catalog["images"]
    groups = catalog["consumers"]["compose_env"]
    lines = [GENERATED_MARKER, ""]
    for group, mapping in groups.items():
        compose_file = COMPOSE_FILE_FOR_GROUP.get(group, group)
        lines.append(f"# {group} ({compose_file})")
        for var, key in mapping.items():
            lines.append(f"{var}={image_ref(images, key)}")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def render_all(catalog: dict[str, Any]) -> dict[Path, str]:
    return {
        PRODUCT_ENV_PATH: render_product_env(catalog),
        IMAGE_SET_PATH: render_image_set(catalog),
        PLATFORM_ENV_PATH: render_platform_env(catalog),
    }


# --------------------------------------------------------------------------
# Invariants (sync + verify both assert these)
# --------------------------------------------------------------------------

def _all_env_vars(catalog: dict[str, Any]) -> dict[str, str]:
    """var -> image key, across product_env and compose_env (image_sets has
    no env vars of its own)."""
    out: dict[str, str] = {}
    for group in catalog["consumers"]["product_env"].values():
        out.update(group)
    for group in catalog["consumers"]["compose_env"].values():
        out.update(group)
    return out


def assert_invariants(catalog: dict[str, Any]) -> None:
    images = catalog["images"]
    all_vars = _all_env_vars(catalog)

    # 1. no var collides with .env.example
    if ENV_EXAMPLE_PATH.is_file():
        example_vars = set(
            re.findall(r"(?m)^([A-Z][A-Z0-9_]*)=", ENV_EXAMPLE_PATH.read_text(encoding="utf-8"))
        )
        collide = sorted(set(all_vars) & example_vars)
        if collide:
            raise CatalogError(
                "catalog var(s) collide with .env.example (legacy stack): " + ", ".join(collide)
            )

    # 2. no two images map to one var — recompute per-group and check for a
    # var appearing twice with a DIFFERENT image key (same key twice is
    # harmless but still not expected; treat any repeat as an error).
    seen: dict[str, str] = {}
    for group_name, groups in (
        ("product_env", catalog["consumers"]["product_env"]),
        ("compose_env", catalog["consumers"]["compose_env"]),
    ):
        for group, mapping in groups.items():
            for var, key in mapping.items():
                if var in seen and seen[var] != key:
                    raise CatalogError(
                        f"var {var} maps to both {seen[var]!r} and {key!r} "
                        f"(in consumers.{group_name}.{group})"
                    )
                seen[var] = key

    # 3. no set containing a channel: local image uses pull_policy: always
    for name, resolved in resolved_image_sets(catalog).items():
        if resolved.get("pull_policy") != "always":
            continue
        local_keys = _flatten_leaf_keys(catalog["consumers"]["image_sets"][name].get("images") or {})
        base_name = catalog["consumers"]["image_sets"][name].get("inherits")
        if base_name:
            local_keys |= _flatten_leaf_keys(catalog["consumers"]["image_sets"][base_name].get("images") or {})
        bad = sorted(k for k in local_keys if images.get(k, {}).get("channel") == "local")
        if bad:
            raise CatalogError(
                f"image_sets.{name} has pull_policy: always but resolves local/ image(s): "
                + ", ".join(bad)
            )


def _flatten_leaf_keys(node: Any) -> set[str]:
    out: set[str] = set()
    if isinstance(node, dict):
        for v in node.values():
            out |= _flatten_leaf_keys(v)
    elif isinstance(node, str):
        out.add(node)
    return out


# --------------------------------------------------------------------------
# sync / verify
# --------------------------------------------------------------------------

def cmd_sync(_args: argparse.Namespace) -> int:
    catalog = load_catalog()
    assert_invariants(catalog)
    artifacts = render_all(catalog)
    for path, content in artifacts.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        print(f"wrote {path.relative_to(ROOT)}")
    return 0


def cmd_verify(_args: argparse.Namespace) -> int:
    catalog = load_catalog()
    assert_invariants(catalog)
    artifacts = render_all(catalog)
    drifted = []
    for path, content in artifacts.items():
        current = path.read_text(encoding="utf-8") if path.is_file() else None
        if current != content:
            drifted.append(path)
    if drifted:
        for path in drifted:
            print(f"DRIFT: {path.relative_to(ROOT)} does not match images/catalog.yaml", file=sys.stderr)
        print("run: tools/images.sh sync", file=sys.stderr)
        return 1
    print("verify: ok (product-images.env, images/image-set.generated.yaml, "
          "images/platform-images.generated.env all match images/catalog.yaml)")
    return 0


# --------------------------------------------------------------------------
# report / bump — Hub + imagetools resolution, ported from
# tools/check-image-pins.sh (report the old script's report(), keep the same
# STALE / TAG_MOVED / NO_DIGEST / NO_TAGS_FOUND / OK vocabulary).
# --------------------------------------------------------------------------

def _hub_token() -> str:
    cfg_path = Path("~/.docker/config.json").expanduser()
    if not cfg_path.is_file():
        sys.exit("no ~/.docker/config.json — run docker login")
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    auth = next((v["auth"] for k, v in cfg.get("auths", {}).items() if "docker.io" in k), None)
    if not auth:
        sys.exit("no docker.io credentials in ~/.docker/config.json — run docker login")
    user, pw = base64.b64decode(auth).decode().split(":", 1)
    req = urllib.request.Request(
        "https://hub.docker.com/v2/users/login",
        data=json.dumps({"username": user, "password": pw}).encode(),
        headers={"Content-Type": "application/json"},
    )
    return json.load(urllib.request.urlopen(req))["token"]


def _hub_digest_for(repo: str, tag: str, token: str) -> str:
    try:
        req = urllib.request.Request(
            f"https://hub.docker.com/v2/repositories/{repo}/tags/{tag}",
            headers={"Authorization": f"JWT {token}"},
        )
        return json.load(urllib.request.urlopen(req)).get("digest") or ""
    except Exception:
        return ""


def _hub_tags(repo: str, family: str, token: str) -> list[str]:
    out: list[str] = []
    url = f"https://hub.docker.com/v2/repositories/{repo}/tags/?page_size=100&name={family}"
    while url:
        req = urllib.request.Request(url, headers={"Authorization": f"JWT {token}"})
        d = json.load(urllib.request.urlopen(req))
        out += [t["name"] for t in d["results"]]
        url = d.get("next")
    return out


def _review_num(tag: str) -> int | None:
    m = re.search(r"-review\.(\d+)$", tag)
    return int(m.group(1)) if m else None


def _imagetools_digest(ref: str) -> str:
    """awk '/^Digest:/' idiom — NOT --format '{{.Manifest.Digest}}', which
    silently prints the wrong thing (or nothing useful) for a single-arch
    image. See MnS-Integration-Platform/services/tevv-web-dashboard/tools/
    pin-dashboard-images.sh:52-58."""
    try:
        out = subprocess.run(
            ["docker", "buildx", "imagetools", "inspect", ref],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if out.returncode != 0:
        return ""
    for line in out.stdout.splitlines():
        if line.startswith("Digest:"):
            digest = line.split(":", 1)[1].strip()
            return digest if re.match(r"^sha256:[0-9a-f]{64}$", digest) else ""
    return ""


def _report_row(key: str, row: dict[str, Any], token: str | None) -> dict[str, Any]:
    repo, tag, pinned_digest = row["repo"], row["tag"], row.get("digest") or ""
    channel = row["channel"]
    resolver = row.get("resolver", "hub")
    out = {"key": key, "repo": repo, "tag": tag, "channel": channel,
           "latest_tag": tag, "status": "SKIPPED", "live_digest": ""}

    if channel == "local":
        out["status"] = "SKIPPED (local)"
        return out

    if resolver == "imagetools":
        live = _imagetools_digest(f"{repo}:{tag}")
        if not live:
            out["status"] = "UNRESOLVABLE"
        elif not pinned_digest:
            out["status"] = "NO_DIGEST"
        elif live != pinned_digest:
            out["status"] = "DIGEST_CHANGED"
        else:
            out["status"] = "OK"
        out["live_digest"] = live
        return out

    # resolver == hub
    assert token is not None
    live = _hub_digest_for(repo, tag, token)
    if channel == "review":
        m = re.match(r"^(.*-review)\.\d+$", tag)
        family = m.group(1) if m else None
        candidates = [(_review_num(t), t) for t in (_hub_tags(repo, family, token) if family else [])
                      if _review_num(t) is not None]
        latest = max(candidates)[1] if candidates else tag
        if not family or not candidates:
            out["status"] = "NO_TAGS_FOUND"
        elif not pinned_digest:
            out["status"] = "NO_DIGEST"
        elif live and live != pinned_digest:
            out["status"] = "TAG_MOVED"
        elif latest != tag:
            out["status"] = "STALE"
        else:
            out["status"] = "OK"
        out["latest_tag"] = latest
        out["live_digest"] = _hub_digest_for(repo, latest, token) if latest != tag else live
        return out

    # channel == moving (mutable tag, no family)
    if not pinned_digest:
        out["status"] = "NO_DIGEST"
    elif live and live != pinned_digest:
        out["status"] = "TAG_MOVED"
    elif not live:
        out["status"] = "NO_TAGS_FOUND"
    else:
        out["status"] = "OK"
    out["live_digest"] = live
    return out


def _needs_hub(catalog: dict[str, Any]) -> bool:
    return any(
        row.get("resolver", "hub") == "hub" and row["channel"] != "local"
        for row in catalog["images"].values()
    )


def cmd_report(args: argparse.Namespace) -> int:
    catalog = load_catalog()
    images = catalog["images"]
    keys = [args.only] if args.only else sorted(images)
    token = _hub_token() if _needs_hub(catalog) else None
    rows = []
    for key in keys:
        if key not in images:
            sys.exit(f"unknown image key: {key}")
        if args.channel and images[key]["channel"] != args.channel:
            continue
        rows.append(_report_row(key, images[key], token))

    print(f"{'KEY':<26} {'CHANNEL':<9} {'TAG':<40} {'STATUS':<16} {'LATEST/LIVE'}")
    bad = False
    for r in rows:
        digest_col = r["live_digest"][:19] if r["live_digest"] else r["latest_tag"]
        print(f"{r['key']:<26} {r['channel']:<9} {r['tag']:<40} {r['status']:<16} {digest_col}")
        if r["status"] == "UNRESOLVABLE":
            bad = True
    if any(r["status"] == "NO_DIGEST" for r in rows if r["channel"] != "local"):
        print("\nNO_DIGEST: pinned by tag alone; images.sh bump can resolve it "
              "(channel review/moving only).", file=sys.stderr)
    if any(r["status"] in ("TAG_MOVED", "DIGEST_CHANGED") for r in rows):
        print("TAG_MOVED/DIGEST_CHANGED: the tag now resolves to a different image "
              "than what is pinned. images.sh bump re-pins it.", file=sys.stderr)
    if bad:
        print("UNRESOLVABLE: could not resolve at all — reported, not silently OK.",
              file=sys.stderr)
    return 1 if bad else 0


def _bump_line_targeted(text: str, key: str, new_tag: str, new_digest: str | None) -> str:
    start = text.index("\nimages:\n") + 1
    end = text.index("\nconsumers:\n") + 1
    head, images_block, tail = text[:start], text[start:end], text[end:]

    block_re = re.compile(rf"(?m)^(  {re.escape(key)}:\n)((?:    .*\n)*)")
    match = block_re.search(images_block)
    if not match:
        raise CatalogError(f"bump: could not find a line-targeted block for {key!r}")
    header, body = match.group(1), match.group(2)

    if not re.search(r"(?m)^    tag: .*\n", body):
        raise CatalogError(f"bump: no 'tag:' line found in {key!r}'s block")
    body = re.sub(r"(?m)^    tag: .*\n", f"    tag: {new_tag}\n", body, count=1)

    digest_literal = new_digest if new_digest else "null"
    if not re.search(r"(?m)^    digest: .*\n", body):
        raise CatalogError(f"bump: no 'digest:' line found in {key!r}'s block")
    body = re.sub(r"(?m)^    digest: .*\n", f"    digest: {digest_literal}\n", body, count=1)

    new_block = header + body
    new_images_block = images_block[: match.start()] + new_block + images_block[match.end():]
    return head + new_images_block + tail


def cmd_bump(args: argparse.Namespace) -> int:
    catalog = load_catalog()
    images = catalog["images"]
    keys = [args.only] if args.only else sorted(images)
    token = _hub_token() if _needs_hub(catalog) else None

    text = CATALOG_PATH.read_text(encoding="utf-8")
    changed = 0
    for key in keys:
        if key not in images:
            sys.exit(f"unknown image key: {key}")
        row = images[key]
        if args.channel and row["channel"] != args.channel:
            continue
        if row["channel"] in ("upstream", "local"):
            print(f"refused: {key} is channel {row['channel']} (never auto-bumped)", file=sys.stderr)
            continue
        report_row = _report_row(key, row, token)
        if report_row["status"] not in ("STALE", "TAG_MOVED", "NO_DIGEST"):
            continue
        new_digest = report_row["live_digest"]
        if not new_digest:
            print(f"skipped: {key} — could not resolve a digest for "
                  f"{row['repo']}:{report_row['latest_tag']}", file=sys.stderr)
            continue
        text = _bump_line_targeted(text, key, report_row["latest_tag"], new_digest)
        print(f"bumped: {key} {row['tag']} -> {report_row['latest_tag']}@{new_digest[:19]}… "
              f"({report_row['status']})")
        changed += 1

    if changed == 0:
        print("nothing to bump")
        return 0

    CATALOG_PATH.write_text(text, encoding="utf-8")
    # re-parse and assert: the whole catalog must still be valid.
    reparsed = load_catalog()
    print(f"images/catalog.yaml updated ({changed} row(s)); re-parsed ok, "
          f"{len(reparsed['images'])} image(s) total")
    print("now: tools/images.sh sync   # regenerate the artifacts from the new catalog")
    return 0


# --------------------------------------------------------------------------
# drift / baked support (bash side does the docker-heavy lifting; these are
# the catalog-aware lookups it shells out to)
# --------------------------------------------------------------------------

def cmd_resolve_var(args: argparse.Namespace) -> int:
    if not PRODUCT_ENV_PATH.is_file():
        sys.exit("product-images.env missing — run tools/images.sh sync first")
    for line in PRODUCT_ENV_PATH.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{args.var}="):
            print(line.split("=", 1)[1])
            return 0
    sys.exit(f"{args.var} not found in product-images.env")


def cmd_baked_pins(args: argparse.Namespace) -> int:
    catalog = load_catalog()
    images = catalog["images"]
    row = images.get(args.key)
    if row is None:
        sys.exit(f"unknown image key: {args.key}")
    bakes = row.get("bakes") or []
    if not bakes:
        return 0
    # map catalog key -> var name via consumers.product_env (the only place
    # baked images are also referenced by an env var)
    key_to_var = {}
    for group in catalog["consumers"]["product_env"].values():
        for var, key in group.items():
            key_to_var[key] = var
    for baked_key in bakes:
        var = key_to_var.get(baked_key)
        if not var:
            sys.exit(f"images.{args.key}.bakes references {baked_key!r}, which has "
                      "no consumers.product_env var")
        print(f"{var}_DEFAULT\t{image_ref(images, baked_key)}")
    return 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="images.py")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("sync").set_defaults(fn=cmd_sync)
    sub.add_parser("verify").set_defaults(fn=cmd_verify)

    p_report = sub.add_parser("report")
    p_report.add_argument("--only")
    p_report.add_argument("--channel", choices=sorted(VALID_CHANNELS))
    p_report.set_defaults(fn=cmd_report)

    p_bump = sub.add_parser("bump")
    p_bump.add_argument("--only")
    p_bump.add_argument("--channel", choices=sorted(VALID_CHANNELS))
    p_bump.set_defaults(fn=cmd_bump)

    p_rv = sub.add_parser("resolve-var")
    p_rv.add_argument("var")
    p_rv.set_defaults(fn=cmd_resolve_var)

    p_bp = sub.add_parser("baked-pins")
    p_bp.add_argument("key")
    p_bp.set_defaults(fn=cmd_baked_pins)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
