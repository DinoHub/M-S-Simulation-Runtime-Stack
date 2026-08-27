#!/usr/bin/env python3
"""tools/images.py — one canonical image catalog: render, verify, report, bump.

See images/catalog.yaml (the single authored source) and
docs/adr/0002-one-image-catalog.md (why this exists). Subcommands:

  sync     regenerate every generated artifact from images/catalog.yaml (offline)
  verify   run selftest, then regenerate into a tmp dir and diff against the
           committed artifacts, exit 1 on any difference or selftest failure
           (offline — the CI gate)
  selftest regression guard on synthetic fixtures, no real catalog or network
           touched: asserts an unquoted numeric-looking tag (e.g. `3.4`) fails
           catalog validation, and that `bump`'s line-targeted rewrite always
           emits a double-quoted tag. `verify` always runs this first.
  report   for each non-local, non-unpublished image, show whether a newer
           tag/digest exists (online: Docker Hub v2 API for channel
           review/moving, `docker buildx imagetools inspect` for resolver:
           imagetools rows). A failed lookup is reported as UNRESOLVABLE with
           the reason (404 / auth / network) in the detail column — never as
           NO_DIGEST, and never by printing the tag as if it were live data.
           `report` exits nonzero if any non-local/non-unpublished row is
           UNRESOLVABLE, so it can gate. `channel: unpublished` rows (known to
           be absent from the registry — see images/catalog.yaml) are shown
           as UNPUBLISHED without attempting a lookup.
  bump     rewrite images/catalog.yaml LINE-TARGETED (never yaml.dump — that
           would destroy every `purpose:` comment) for rows that are behind,
           then re-parse and assert the structure. channel review/moving rows
           bump freely (in bulk or via --only). channel upstream rows are
           refused in bulk (never auto-bumped to a newer version) but CAN be
           bumped one at a time with `--only KEY` — this only ever resolves
           the digest of the version tag already pinned; it never walks
           versions forward. local/unpublished are refused unconditionally.

  refs [--all-catalog] [--development] print exact production refs or
                       tag-only development refs for the active product.

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
import shutil
import subprocess
import sys
import tempfile
import textwrap
import urllib.error
import urllib.parse
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
DEVELOPMENT_IMAGE_SET_PATH = ROOT / "images" / "image-set.development.generated.yaml"
DEVELOPMENT_ENV_PATH = ROOT / "images" / "standalone-v2-development.generated.env"
PLATFORM_ENV_PATH = ROOT / "images" / "platform-images.generated.env"
LEGACY_ENV_PATH = ROOT / "images" / "legacy-images.generated.env"
STANDALONE_V2_ENV_PATH = ROOT / "images" / "standalone-v2-images.generated.env"
ENV_EXAMPLE_PATH = ROOT / ".env.example"
DOTENV_PATH = ROOT / ".env"

GENERATED_MARKER = (
    "# GENERATED from images/catalog.yaml — see docs/adr/0002-one-image-catalog.md. "
    "Do not hand-edit; run tools/images.sh sync."
)

VALID_CHANNELS = {"review", "traced", "moving", "upstream", "local", "unpublished", "pinned"}
VALID_RESOLVERS = {"hub", "imagetools"}

# A traced tag names its source: <component>-v<x.y.z>-g<short-sha>, with an
# optional .N rebuild suffix for republishing the same commit against a new
# base image. ADR-0001: "New tags name their source ... so a pin answers
# 'what is in this?' with `git show`". The sha is >= 7 hex chars because that
# is git's own short-sha floor; longer is fine and stays valid as repos grow.
TRACED_TAG_RE = re.compile(r"-v\d+\.\d+\.\d+-g[0-9a-f]{7,}(\.\d+)?$")

_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def _semver(value: str) -> tuple[int, int, int] | None:
    m = _SEMVER_RE.match(value or "")
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def traced_tag_version(tag: str) -> tuple[int, int, int] | None:
    """The version a traced tag claims, or None if the tag is not traced."""
    m = re.search(r"-v(\d+\.\d+\.\d+)-g[0-9a-f]{7,}(\.\d+)?$", tag)
    return _semver(m.group(1)) if m else None


def platform_product_version(root: Path | None = None) -> str | None:
    """The sibling platform checkout's declared version, or None when that checkout
    is not reachable. The sibling platform checkout is resolved the way
    product-images-env.sh resolves the pin file in the other direction: an explicit
    env var wins, then the layout this repo is actually checked out in. Absent is
    normal (CI clones this repo alone), so every caller treats None as "cannot check",
    never as a failure."""
    if root is not None:                      # explicit root: used by the selftest
        candidates = [root]
    elif os.environ.get("MNS_PLATFORM_ROOT"):
        candidates = [Path(os.environ["MNS_PLATFORM_ROOT"])]
    else:
        candidates = [ROOT.parent / "MnS-Integration-Platform"]
    for c in candidates:
        path = c / "mns-product.yaml"
        if not path.is_file():
            continue
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return None                    # non-mapping is "cannot check", not "wrong"
            version = data.get("version")
            return version if isinstance(version, str) else None
        except Exception:
            return None                        # unreadable is "cannot check", not "wrong"
    return None

COMPOSE_FILE_FOR_GROUP = {
    "monitoring": "docker-compose-monitoring.yml",
    "metrics": "docker-compose-metrics.yml",
    "logs": "docker-compose-logs.yml",
    "dashboard": "docker-compose-dashboard.yml, inline images",
}

COMPOSE_FILE_FOR_LEGACY_GROUP = {
    "ardupilot_condo": "compose/ardupilot-condo/docker-compose.yml",
    "ardupilot_urbansim": "compose/ardupilot-urbansim/docker-compose.yml",
    "ardupilot_xfs": "compose/ardupilot-xfs/docker-compose.yml",
    "px4_condo": "compose/px4-condo/docker-compose.yml",
    "px4_xfs": "compose/px4-xfs/docker-compose.yml",
}


class CatalogError(SystemExit):
    def __init__(self, msg: str):
        super().__init__(f"images/catalog.yaml: {msg}")


# --------------------------------------------------------------------------
# Loading + validation
# --------------------------------------------------------------------------

def _validate_catalog(data: Any) -> None:
    """The structural checks load_catalog() applies to a parsed catalog dict.
    Split out from load_catalog() so tools/images.py's self-test can run it
    against a synthetic in-memory fixture, with no file on disk."""
    if not isinstance(data, dict):
        raise CatalogError("must be a mapping at the top level")
    if data.get("schema") != "mns.images.v1":
        raise CatalogError(f"schema must be mns.images.v1, got {data.get('schema')!r}")
    images = data.get("images")
    if not isinstance(images, dict) or not images:
        raise CatalogError("images: must be a non-empty mapping")
    # The version segment of every tag we publish comes from here, and this
    # comes from MnS-Integration-Platform/mns-product.yaml. Before this field
    # existed the number was a string copied into each new tag: the product
    # file said 0.1.0, every image tag said v0.2.0, and two orphaned
    # mns-authoring-v0.3.0-review tags sat on the registry that no repo
    # referenced. See ADR-0003.
    product_version = data.get("product_version")
    if not isinstance(product_version, str) or _semver(product_version) is None:
        raise CatalogError(
            f"product_version must be a semver string like \"0.2.0\", got {product_version!r}"
        )
    for key, row in images.items():
        if not isinstance(row, dict):
            raise CatalogError(f"images.{key} must be a mapping")
        for field in ("repo", "tag", "channel", "purpose"):
            if field not in row:
                raise CatalogError(f"images.{key} missing required field {field!r}")
        if "digest" not in row:
            raise CatalogError(f"images.{key} missing required field 'digest' (use null)")
        # A tag like `3.4` or `8.12` is valid YAML float syntax; an unquoted
        # `tag: 3.4` parses as the number 3.4, not the string "3.4" — silently
        # corrupting the ref everywhere it's rendered. `3.4.2` happens to be
        # safe (two dots isn't a valid number), which is exactly what let this
        # slip through once already (loki's tag briefly lost its quotes to an
        # unrelated `bump` rewrite). Catch every case, not just the two-dot one.
        if not isinstance(row["tag"], str):
            raise CatalogError(
                f"images.{key}.tag must be a string, got {type(row['tag']).__name__} "
                f"({row['tag']!r}) — YAML parsed an unquoted numeric-looking tag as a "
                f"number. Quote it: tag: \"{row['tag']}\""
            )
        if "latest_tag" in row:
            if not isinstance(row["latest_tag"], str) or not row["latest_tag"].endswith("-latest"):
                raise CatalogError(
                    f"images.{key}.latest_tag must be a string ending in '-latest'")
        if row["channel"] not in VALID_CHANNELS:
            raise CatalogError(f"images.{key}.channel {row['channel']!r} not in {VALID_CHANNELS}")
        if "follow_up" in row and not isinstance(row["follow_up"], str):
            raise CatalogError(
                f"images.{key}.follow_up must be a string (a note to whoever reads "
                f"`tools/images.sh status` next)"
            )
        resolver = row.get("resolver", "hub")
        if resolver not in VALID_RESOLVERS:
            raise CatalogError(f"images.{key}.resolver {resolver!r} not in {VALID_RESOLVERS}")
        if row["channel"] in ("local", "unpublished") and row["digest"] is not None:
            raise CatalogError(f"images.{key} is channel {row['channel']} but digest is not null")
        if row["channel"] == "local" and not str(row["repo"]).startswith("local/"):
            raise CatalogError(f"images.{key} is channel local but repo does not start with local/")
        # A review row whose tag is off the -review.N line is a silent dead
        # end: bump derives the tag family from the tag itself, finds no
        # siblings, and reports NO_TAGS_FOUND — which reads as "already
        # current" and is how an off-family pin sits stale for weeks. Say it
        # at edit time instead. A deliberate off-line pin is channel: pinned.
        if row["channel"] == "review" and not re.search(r"-review\.\d+$", row["tag"]):
            raise CatalogError(
                f"images.{key} is channel review but tag {row['tag']!r} is not on the "
                f"-review.N line, so bump can never advance it. Either pin a -review.N "
                f"tag, or declare the off-line pin honestly with channel: pinned."
            )
        # Same reasoning as the review check above: a traced row whose tag is
        # off the -v<x.y.z>-g<sha> line cannot be walked, so `bump` would
        # silently never advance it.
        if row["channel"] == "traced" and not TRACED_TAG_RE.search(row["tag"]):
            raise CatalogError(
                f"images.{key} is channel traced but tag {row['tag']!r} does not name its "
                f"source (expected <component>-v<x.y.z>-g<short-sha>), so bump can never "
                f"advance it. Either publish a traced tag, or declare the off-line pin "
                f"honestly with channel: pinned."
            )
        claimed = traced_tag_version(row["tag"]) if row["channel"] == "traced" else None
        if claimed is not None and claimed > _semver(product_version):
            raise CatalogError(
                f"images.{key} tag {row['tag']!r} claims version "
                f"{'.'.join(str(n) for n in claimed)}, ahead of product_version "
                f"{product_version} — a tag from the future is a typo or a bad mint. "
                f"A tag BEHIND product_version is fine (it was published before the bump)."
            )
        if row["channel"] == "pinned" and not row["digest"]:
            raise CatalogError(
                f"images.{key} is channel pinned but has no digest — a pinned row exists "
                f"precisely to name one exact image; without a digest it names nothing."
            )
        # published_by names the repo+script that pushes this image. Carrying
        # it is a claim of ownership, and we do not publish our own images to
        # a tag that can move under a pin: traced (built, names its source) or
        # pinned (a deliberate one-off, e.g. a re-tag with no source commit).
        publisher = row.get("published_by")
        if publisher is not None:
            if not isinstance(publisher, str) or not publisher.strip():
                raise CatalogError(f"images.{key}.published_by must be a non-empty string")
            if row["channel"] not in ("traced", "pinned"):
                raise CatalogError(
                    f"images.{key} is published_by {publisher!r} but sits on channel "
                    f"{row['channel']!r}. An image we publish ourselves must be on an "
                    f"immutable tag: channel traced (built from a commit) or pinned "
                    f"(a deliberate one-off). See ADR-0003."
                )
    follow_ups = data.get("follow_ups")
    if follow_ups is not None and (
        not isinstance(follow_ups, list)
        or not all(isinstance(n, str) for n in follow_ups)
    ):
        raise CatalogError("follow_ups: must be a list of strings")
    consumers = data.get("consumers")
    if not isinstance(consumers, dict):
        raise CatalogError("consumers: must be a mapping")
    for group_name in ("product_env", "image_sets", "compose_env", "legacy_env"):
        if group_name not in consumers:
            raise CatalogError(f"consumers.{group_name} missing")
    # Optional: a catalog with no pre-release channel is an ordinary catalog.
    # Shape-checked when present so a typo fails at load rather than emitting
    # an empty pin file that reads as "no images pinned".
    channels = consumers.get("release_channels")
    if channels is not None:
        if not isinstance(channels, dict):
            raise CatalogError("consumers.release_channels: must be a mapping")
        for name, spec in channels.items():
            if not isinstance(spec, dict) or not isinstance(spec.get("vars"), dict) \
                    or not spec["vars"]:
                raise CatalogError(
                    f"consumers.release_channels.{name}: needs a non-empty vars mapping")
            if not spec.get("emits"):
                raise CatalogError(
                    f"consumers.release_channels.{name}: needs an emits path")


def load_catalog(path: Path = CATALOG_PATH) -> dict[str, Any]:
    if not path.is_file():
        raise CatalogError(f"not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    _validate_catalog(data)
    return data


def image_ref(images: dict[str, Any], key: str) -> str:
    if key not in images:
        raise CatalogError(f"consumer references unknown image key {key!r}")
    row = images[key]
    ref = f"{row['repo']}:{row['tag']}"
    if row.get("digest"):
        ref = f"{ref}@{row['digest']}"
    return ref


def development_ref(images: dict[str, Any], key: str) -> str:
    """Tag-only ref used by the local-first development workflow."""
    if key not in images:
        raise CatalogError(f"consumer references unknown image key {key!r}")
    row = images[key]
    return f"{row['repo']}:{row.get('latest_tag') or row['tag']}"


def pullable_refs(catalog: dict[str, Any], *, all_catalog: bool = False, development: bool = False) -> list[str]:
    """Return unique active refs in production or tag-only development form."""
    images = catalog["images"]
    if all_catalog:
        keys = {
            key for key, row in images.items()
            if row["channel"] not in ("local", "unpublished")
        }
    else:
        consumers = catalog["consumers"]
        keys = set(consumers["release_channels"]["standalone_v2"]["vars"].values())
        keys |= _flatten_leaf_keys(consumers["image_sets"]["published"]["images"])
        for group in ("dashboard", "tools"):
            keys |= set(consumers["product_env"].get(group, {}).values())
        keys |= set(consumers["compose_env"].get("dashboard", {}).values())
        unavailable = sorted(
            key for key in keys
            if images[key]["channel"] in ("local", "unpublished")
        )
        if unavailable:
            raise CatalogError(
                "active product references unavailable image(s): " + ", ".join(unavailable))
    ref_for = development_ref if development else image_ref
    return sorted({ref_for(images, key) for key in keys})



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


def render_development_image_set(catalog: dict[str, Any]) -> str:
    """Tag-only v2 image set for local-first development.

    The catalog remains the source of image names. Digests are removed only in
    this development artifact so a locally built matching tag wins; Compose
    pulls the tag only when it is absent from the Docker image store.
    """
    images = catalog["images"]
    development_refs = {
        image_ref(images, key): development_ref(images, key)
        for key in images
    }

    def tag_only(node: Any) -> Any:
        if isinstance(node, dict):
            return {key: tag_only(value) for key, value in node.items()}
        if isinstance(node, list):
            return [tag_only(value) for value in node]
        if isinstance(node, str):
            return development_refs.get(node, node)
        return node

    header = (
        "schema: mns.image_sets.v1\n\n"
        f"{GENERATED_MARKER}\n"
        "# Development overlay: tag-only refs plus pull_policy: missing let a\n"
        "# local build win and pull the published tag only when it is absent.\n\n"
    )
    body = {"image_sets": tag_only(resolved_image_sets(catalog))}
    return header + yaml.safe_dump(body, sort_keys=False, default_flow_style=False)


def render_development_env(catalog: dict[str, Any]) -> str:
    """Dashboard/product defaults using tag-only development aliases."""
    images = catalog["images"]
    consumers = catalog["consumers"]
    groups = [
        consumers["release_channels"]["standalone_v2"]["vars"],
        consumers["product_env"].get("dashboard", {}),
        consumers["product_env"].get("tools", {}),
        consumers["compose_env"].get("dashboard", {}),
    ]
    mapping: dict[str, str] = {}
    for group in groups:
        mapping.update(group)
    lines = [
        GENERATED_MARKER,
        "",
        "# Local-first dashboard defaults. Matching local tags win; missing",
        "# tags are pulled by tools/ensure-images.sh. Production uses the",
        "# digest-pinned generated env files instead.",
        "",
    ]
    lines += [f"{var}={development_ref(images, key)}" for var, key in mapping.items()]
    return "\n".join(lines).rstrip("\n") + "\n"


def render_standalone_v2_env(catalog: dict[str, Any]) -> str:
    """images/standalone-v2-images.generated.env — the coordinated v2 set.

    A separate file rather than another group in product-images.env, because
    three of these bind the SAME variable names as the review group. Both in
    one file and the later line silently wins; a consumer picks a channel by
    picking a file, which is a choice it can make explicitly and a reader can
    see.

    Every row is rendered with its immutable release tag and manifest digest.
    Mutable -latest aliases are deliberately not emitted here: production
    runs remain reproducible until the catalog is advanced and regenerated.
    """
    images = catalog["images"]
    channel = catalog["consumers"]["release_channels"].get("standalone_v2")
    if not channel or not channel.get("vars"):
        raise CatalogError(
            "consumers.release_channels.standalone_v2.vars is missing; "
            "images/standalone-v2-images.generated.env has nothing to emit")
    group = channel["vars"]
    lines = [
        GENERATED_MARKER,
        "",
        "# Coordinated standalone-v2 production pins. Sourced INSTEAD of",
        "# product-images.env by the v2 product shell, not alongside it.",
        "#",
        "# Every ref uses an immutable date/version tag and manifest digest.",
        "# The corresponding -latest aliases are for discovery and publishing;",
        "# production runs the exact refs below. Edit images/catalog.yaml and",
        "# re-run tools/images.sh sync to advance the approved release.",
        "",
    ]
    lines += [f"{var}={image_ref(images, key)}" for var, key in group.items()]
    return "\n".join(lines).rstrip("\n") + "\n"


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


def render_legacy_env(catalog: dict[str, Any]) -> str:
    """images/legacy-images.generated.env — the legacy static scenario
    stacks (compose/<scenario>/docker-compose.yml). Unlike compose_env,
    consumers.legacy_env is grouped by SCENARIO, and the same var name can
    legitimately be bound to a DIFFERENT image key in two scenario groups
    (AIRSIM_IMAGE, AIRSIM_CONDO_IMAGE — see images/catalog.yaml's "Legacy
    static scenario stacks" section and docs/adr/0002-one-image-catalog.md
    "Legacy scenario stacks: conflicting defaults"). A var whose resolved
    ref is identical across every scenario that binds it gets ONE line
    here; a var that disagrees gets NO line — emitting either side's value
    would silently override the other scenario's default the moment a
    developer removes the var from ./.env, so this renderer refuses to
    guess and documents the disagreement in a comment block instead.
    """
    images = catalog["images"]
    groups = catalog["consumers"]["legacy_env"]

    # var -> {ref: [group, ...]}
    var_refs: dict[str, dict[str, list[str]]] = {}
    for group, mapping in groups.items():
        for var, key in mapping.items():
            ref = image_ref(images, key)
            var_refs.setdefault(var, {}).setdefault(ref, []).append(group)

    consistent = {var: refs for var, refs in var_refs.items() if len(refs) == 1}
    conflicting = {var: refs for var, refs in var_refs.items() if len(refs) > 1}

    lines = [
        GENERATED_MARKER,
        "",
        "# Legacy static scenario stacks (compose/<scenario>/docker-compose.yml).",
        "# Loaded by tools/load-images-env.sh from launch.sh/stop.sh/logs.sh: a key",
        "# below is exported ONLY if it is unset in the shell AND absent from",
        "# ./.env. Precedence: shell env > ./.env > this file > the compose",
        "# ${VAR:-default} fallback (itself UNCHANGED by this file). Until a var",
        "# is removed from ./.env, its value below does NOT take effect for that",
        "# developer — see docs/adr/0002-one-image-catalog.md \"Legacy scenario",
        "# stacks\".",
        "",
    ]
    for var in sorted(consistent):
        (ref,) = consistent[var].keys()
        used_by = ", ".join(
            COMPOSE_FILE_FOR_LEGACY_GROUP.get(g, g) for g in sorted(consistent[var][ref])
        )
        lines.append(f"# {var} — {used_by}")
        lines.append(f"{var}={ref}")
        lines.append("")

    if conflicting:
        lines.append(
            "# NOT emitted below — each var's bound image differs by scenario, so no"
        )
        lines.append(
            "# single value here would be safe. See docs/adr/0002-one-image-catalog.md"
        )
        lines.append("# \"Legacy scenario stacks: conflicting defaults\":")
        for var in sorted(conflicting):
            for ref, group_list in sorted(conflicting[var].items()):
                compose_files = ", ".join(
                    COMPOSE_FILE_FOR_LEGACY_GROUP.get(g, g) for g in sorted(group_list)
                )
                lines.append(f"#   {var}={ref}   ({compose_files})")
        lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"


def render_all(catalog: dict[str, Any]) -> dict[Path, str]:
    out = {
        PRODUCT_ENV_PATH: render_product_env(catalog),
        IMAGE_SET_PATH: render_image_set(catalog),
        DEVELOPMENT_IMAGE_SET_PATH: render_development_image_set(catalog),
        DEVELOPMENT_ENV_PATH: render_development_env(catalog),
        PLATFORM_ENV_PATH: render_platform_env(catalog),
        LEGACY_ENV_PATH: render_legacy_env(catalog),
    }
    if (catalog["consumers"].get("release_channels") or {}).get("standalone_v2"):
        out[STANDALONE_V2_ENV_PATH] = render_standalone_v2_env(catalog)
    return out


# --------------------------------------------------------------------------
# Invariants (sync + verify both assert these)
# --------------------------------------------------------------------------

def _all_env_vars(catalog: dict[str, Any]) -> dict[str, str]:
    """var -> image key, across product_env and compose_env (image_sets has
    no env vars of its own). Deliberately excludes legacy_env and
    release_channels: legacy_env's vars
    are expected to already be in .env.example (that IS the legacy stack's
    .env — see images/legacy-images.generated.env's header), and legacy_env
    is also the one group where the same var name is allowed to map to a
    different key per scenario (AIRSIM_IMAGE, AIRSIM_CONDO_IMAGE), which
    the invariant below would otherwise reject. release_channels is excluded
    for the same reason from the other direction: a channel exists precisely to
    bind the review channel's variable names to a different set of images, and
    it emits to its own file so the two never meet."""
    out: dict[str, str] = {}
    for group in catalog["consumers"]["product_env"].values():
        out.update(group)
    for group in catalog["consumers"]["compose_env"].values():
        out.update(group)
    return out


def dotenv_overrides(catalog: dict[str, Any]) -> list[tuple[str, str, str]]:
    """(var, dotenv_value, catalog_ref) for every catalog var that ./.env also
    sets. Compose auto-loads ./.env, and tools/load-images-env.sh deliberately
    skips any key .env already defines, so for these vars the catalog's pin is
    NOT what runs. That is the intended local-override escape hatch — the bug
    is only ever that it is invisible, which is what this surfaces. Unlike
    _all_env_vars() this DOES include legacy_env: those are precisely the vars
    .env sets today, so excluding them would blind the check to every real
    case. A legacy var mapping to different keys per scenario is reported
    against the first key seen; the point is that .env wins, not which pin it
    beat."""
    if not DOTENV_PATH.is_file():
        return []
    env: dict[str, str] = {}
    for line in DOTENV_PATH.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\s*([A-Z][A-Z0-9_]*)\s*=\s*(.*?)\s*$", line)
        if m:
            env[m.group(1)] = m.group(2)
    var_to_key = dict(_all_env_vars(catalog))
    for group in catalog["consumers"]["legacy_env"].values():
        for var, key in group.items():
            var_to_key.setdefault(var, key)
    out = []
    for var, key in sorted(var_to_key.items()):
        if var in env:
            out.append((var, env[var], image_ref(catalog["images"], key)))
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

    # 4. Standalone v2 refreshes exact pins explicitly, then starts from the
    # verified local cache. Keep the authored catalog from silently restoring
    # per-run registry checks and defeating that workflow.
    release_channels = catalog["consumers"].get("release_channels") or {}
    if "standalone_v2" in release_channels:
        published = (catalog["consumers"].get("image_sets") or {}).get("published") or {}
        if published.get("pull_policy") != "missing":
            raise CatalogError(
                "image_sets.published must use pull_policy: missing for standalone_v2; "
                "refresh images explicitly with tools/pull-all-images.sh"
            )

    # 5. product_version must agree with the platform repo it mirrors. Skipped
    # silently when that checkout is not present — see platform_product_version.
    declared = catalog.get("product_version")
    platform = platform_product_version()
    if platform is not None and platform != declared:
        raise CatalogError(
            f"product_version {declared!r} does not match MnS-Integration-Platform's "
            f"mns-product.yaml version {platform!r}. These drifted once already (0.1.0 "
            f"vs v0.2.0 in every image tag); bump both in one PR."
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
    run_selftest()  # regression guard: must pass before trusting the real catalog
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
    checked = ", ".join(sorted(str(p.relative_to(ROOT)) for p in artifacts))
    print(f"verify: ok ({checked} all match images/catalog.yaml)")
    return 0


# --------------------------------------------------------------------------
# selftest — synthetic fixtures, no file on disk, no network. Exists so the
# "unquoted numeric-looking tag" bug (loki's tag briefly became the float
# 3.4 after a `bump` rewrite dropped its quotes) cannot regress silently:
# `tools/images.sh verify` runs this on every invocation, offline.
# --------------------------------------------------------------------------

_SELFTEST_FIXTURE = """\
schema: mns.images.v1
product_version: "0.2.0"

images:
  fixture:
    repo: example/repo
    tag: {tag}
    digest: null
    channel: moving
    purpose: selftest fixture, not a real image.

consumers:
  product_env: {{}}
  image_sets: {{}}
  compose_env: {{}}
  legacy_env: {{}}
"""


def run_selftest() -> None:
    # 1. An unquoted two-component numeric tag must be rejected: YAML parses
    #    `tag: 3.4` as the float 3.4, not the string "3.4".
    unquoted = yaml.safe_load(_SELFTEST_FIXTURE.format(tag="3.4"))
    assert isinstance(unquoted["images"]["fixture"]["tag"], float), (
        "selftest fixture assumption broken: expected YAML to parse an unquoted "
        "'3.4' as a float — if this fails, PyYAML's number grammar changed and "
        "the invariant below needs re-checking, not just this fixture."
    )
    try:
        _validate_catalog(unquoted)
    except CatalogError:
        pass
    else:
        raise AssertionError(
            "selftest FAILED: _validate_catalog accepted a non-string tag "
            "(images.fixture.tag == 3.4, a float) — the tag-must-be-a-string "
            "invariant regressed."
        )

    # 2. The quoted form of the same tag must be accepted, and stay a string.
    quoted = yaml.safe_load(_SELFTEST_FIXTURE.format(tag='"3.4"'))
    _validate_catalog(quoted)  # must not raise
    assert quoted["images"]["fixture"]["tag"] == "3.4"
    assert isinstance(quoted["images"]["fixture"]["tag"], str)

    # 3. `bump`'s line-targeted rewrite must ALWAYS emit a quoted tag, even
    #    when asked to write a value that would be unsafe unquoted (`3.4`),
    #    and the result must re-parse as a string.
    catalog_text = (
        "schema: mns.images.v1\nproduct_version: \"0.2.0\"\n\nimages:\n"
        "  fixture:\n    repo: example/repo\n    tag: old-tag\n    digest: null\n"
        "    channel: moving\n    purpose: selftest fixture.\n\nconsumers:\n"
        "  product_env: {}\n"
    )
    rewritten = _bump_line_targeted(catalog_text, "fixture", "3.4", "sha256:" + "0" * 64)
    m = re.search(r"(?m)^    tag: (.*)$", rewritten)
    assert m, "selftest FAILED: bump did not rewrite the 'tag:' line at all"
    assert m.group(1) == '"3.4"', (
        f"selftest FAILED: bump must always double-quote tags — got {m.group(1)!r} "
        "for a rewrite to '3.4', which is unsafe unquoted (parses as a float)."
    )
    reparsed = yaml.safe_load(rewritten)
    tag_value = reparsed["images"]["fixture"]["tag"]
    assert isinstance(tag_value, str) and tag_value == "3.4", (
        f"selftest FAILED: bumped catalog text re-parses tag as {tag_value!r} "
        f"({type(tag_value).__name__}), expected the string '3.4'"
    )

    # 4. A traced row's tag must name its source: -v<x.y.z>-g<sha>. Anything
    #    else is the same silent dead end a review row off the -review.N line
    #    is — the family walk finds no siblings and reports NO_TAGS_FOUND,
    #    which reads as "already current".
    traced_fixture = """\
schema: mns.images.v1
product_version: "0.2.0"

images:
  fixture:
    repo: example/repo
    tag: "{tag}"
    digest: sha256:{d}
    channel: traced
    purpose: selftest fixture, not a real image.

consumers:
  product_env: {{}}
  image_sets: {{}}
  compose_env: {{}}
  legacy_env: {{}}
"""
    for bad in ("thing-latest", "thing-review.4", "thing-v0.2.0", "thing-g6e6ae15",
                "thing-v0.2-g6e6ae15", "thing-v0.2.0-gZZZZZZZ"):
        try:
            _validate_catalog(yaml.safe_load(traced_fixture.format(tag=bad, d="0" * 64)))
        except CatalogError:
            pass
        else:
            raise AssertionError(
                f"selftest FAILED: _validate_catalog accepted {bad!r} as a traced tag"
            )
    for good in ("thing-v0.2.0-g6e6ae15", "thing-v0.2.0-g6e6ae15.2",
                 "thing-v0.1.9-g0123456789abcdef"):
        _validate_catalog(yaml.safe_load(traced_fixture.format(tag=good, d="0" * 64)))

    # 5. product_version is required, must be semver, and a traced tag may not
    #    claim a version ahead of it (that is a typo or a bad mint). Behind is
    #    legal: an image published before a version bump keeps its version.
    pv_fixture = """\
schema: mns.images.v1
product_version: "{pv}"

images:
  fixture:
    repo: example/repo
    tag: "{tag}"
    digest: sha256:{d}
    channel: traced
    purpose: selftest fixture, not a real image.

consumers:
  product_env: {{}}
  image_sets: {{}}
  compose_env: {{}}
  legacy_env: {{}}
"""
    # ahead of product_version -> rejected
    try:
        _validate_catalog(yaml.safe_load(
            pv_fixture.format(pv="0.2.0", tag="thing-v0.3.0-g6e6ae15", d="0" * 64)))
    except CatalogError:
        pass
    else:
        raise AssertionError(
            "selftest FAILED: accepted a traced tag versioned ahead of product_version"
        )
    # equal and behind -> accepted
    for tag in ("thing-v0.2.0-g6e6ae15", "thing-v0.1.9-g6e6ae15"):
        _validate_catalog(yaml.safe_load(pv_fixture.format(pv="0.2.0", tag=tag, d="0" * 64)))
    # missing / non-semver product_version -> rejected
    for pv in ("", "0.2", "v0.2.0", "latest"):
        try:
            _validate_catalog(yaml.safe_load(
                pv_fixture.format(pv=pv, tag="thing-v0.2.0-g6e6ae15", d="0" * 64)))
        except CatalogError:
            pass
        else:
            raise AssertionError(f"selftest FAILED: accepted product_version {pv!r}")

    # 6. The cross-repo version check must DEGRADE, never fail, when the
    #    platform checkout is absent or malformed — MSRS CI clones this repo alone.
    assert platform_product_version(Path("/nonexistent-checkout")) is None, (
        "selftest FAILED: platform_product_version must return None for a missing "
        "checkout, not raise — CI has no sibling clone and must still validate."
    )
    # Also test that non-mapping YAML documents degrade gracefully
    with tempfile.TemporaryDirectory() as tmpdir:
        # Test YAML list: [1, 2, 3]
        list_yaml = Path(tmpdir) / "mns-product.yaml"
        list_yaml.write_text("[1, 2, 3]", encoding="utf-8")
        assert platform_product_version(Path(tmpdir)) is None, (
            "selftest FAILED: platform_product_version must return None for a YAML list, "
            "not raise AttributeError"
        )
        # Test bare scalar: 42
        list_yaml.write_text("42", encoding="utf-8")
        assert platform_product_version(Path(tmpdir)) is None, (
            "selftest FAILED: platform_product_version must return None for a bare scalar, "
            "not raise AttributeError"
        )

    # 7. A row we publish ourselves may not sit on a mutable tag. This is the
    #    regression guard for the whole change: without it, one PR flipping a
    #    row back to channel: moving undoes it silently.
    owned_fixture = """\
schema: mns.images.v1
product_version: "0.2.0"

images:
  fixture:
    repo: example/repo
    tag: "{tag}"
    digest: sha256:{d}
    channel: {channel}
    published_by: example-repo/tools/build.sh
    purpose: selftest fixture, not a real image.

consumers:
  product_env: {{}}
  image_sets: {{}}
  compose_env: {{}}
  legacy_env: {{}}
"""
    try:
        _validate_catalog(yaml.safe_load(owned_fixture.format(
            tag="thing-latest", channel="moving", d="0" * 64)))
    except CatalogError:
        pass
    else:
        raise AssertionError(
            "selftest FAILED: a published_by row was allowed on channel moving"
        )
    _validate_catalog(yaml.safe_load(owned_fixture.format(
        tag="thing-v0.2.0-g6e6ae15", channel="traced", d="0" * 64)))
    _validate_catalog(yaml.safe_load(owned_fixture.format(
        tag="thing-v0.2.0-retag.2026-08-26", channel="pinned", d="0" * 64)))


def cmd_selftest(_args: argparse.Namespace) -> int:
    run_selftest()
    print("selftest: ok (tag-must-be-string invariant, bump always-quotes-tags, "
          "traced-tag shape check, product_version validation, cross-repo version check)")
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


def _hub_digest_for(repo: str, tag: str, token: str) -> tuple[str, str | None]:
    """Returns (digest, error). error is None on success — even if Hub's
    response happens to carry no digest field, which is not the same as a
    failed lookup and must not be conflated with it (that conflation was the
    bug: a 404 rendered identically to 'not pinned yet')."""
    try:
        req = urllib.request.Request(
            f"https://hub.docker.com/v2/repositories/{repo}/tags/{tag}",
            headers={"Authorization": f"JWT {token}"},
        )
        resp = json.load(urllib.request.urlopen(req, timeout=30))
        return resp.get("digest") or "", None
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return "", "404 not found"
        if exc.code in (401, 403):
            return "", f"HTTP {exc.code} auth"
        return "", f"HTTP {exc.code}"
    except urllib.error.URLError as exc:
        return "", f"network: {exc.reason}"
    except Exception as exc:  # malformed JSON, timeout, etc.
        return "", f"error: {exc}"


def _hub_tags(repo: str, family: str, token: str) -> tuple[list[str], str | None]:
    out: list[str] = []
    url = f"https://hub.docker.com/v2/repositories/{repo}/tags/?page_size=100&name={family}"
    try:
        while url:
            req = urllib.request.Request(url, headers={"Authorization": f"JWT {token}"})
            d = json.load(urllib.request.urlopen(req, timeout=30))
            out += [t["name"] for t in d["results"]]
            url = d.get("next")
        return out, None
    except urllib.error.HTTPError as exc:
        return out, f"HTTP {exc.code}"
    except urllib.error.URLError as exc:
        return out, f"network: {exc.reason}"
    except Exception as exc:
        return out, f"error: {exc}"


def _review_num(tag: str) -> int | None:
    m = re.search(r"-review\.(\d+)$", tag)
    return int(m.group(1)) if m else None


def _docker_imagetools_digest(ref: str) -> tuple[str, str | None]:
    """awk '/^Digest:/' idiom — NOT --format '{{.Manifest.Digest}}', which
    silently prints the wrong thing (or nothing useful) for a single-arch
    image. See MnS-Integration-Platform/services/tevv-web-dashboard/tools/
    pin-dashboard-images.sh:52-58. Returns (digest, error); error is None only
    on a clean resolve, so a 'not found' / auth / network failure is never
    indistinguishable from 'digest not pinned yet'."""
    try:
        out = subprocess.run(
            ["docker", "buildx", "imagetools", "inspect", ref],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "", f"error: {exc}"
    if out.returncode != 0:
        reason = (out.stderr or out.stdout or "unknown imagetools failure").strip().splitlines()
        return "", reason[0] if reason else "unknown imagetools failure"
    for line in out.stdout.splitlines():
        if line.startswith("Digest:"):
            digest = line.split(":", 1)[1].strip()
            if re.match(r"^sha256:[0-9a-f]{64}$", digest):
                return digest, None
            return "", f"unparseable Digest line: {digest!r}"
    return "", "no Digest: line in imagetools output"


_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_MANIFEST_ACCEPT = ", ".join([
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
])


def _anonymous_registry_digest(repo: str, tag: str) -> tuple[str, str | None]:
    """Fallback for when `docker buildx imagetools inspect` fails because of a
    LOCAL credential problem (e.g. a stale token for this ref's registry in
    ~/.docker/config.json) rather than the image genuinely being unreachable.
    Resolves the manifest digest directly via the anonymous Docker Registry
    HTTP API v2 flow: an unauthenticated HEAD, then — on 401 — a token
    request against whatever realm/service/scope the registry's own
    WWW-Authenticate header names, the same protocol `docker pull` uses for a
    public image with no stored credential at all. Never the primary path;
    only tried after the primary fails, so a broken local credential does not
    make an otherwise-public image UNRESOLVABLE."""
    host, sep, path = repo.partition("/")
    if not sep or ("." not in host and ":" not in host and host != "localhost"):
        # bare Docker Hub repo (e.g. "prom/pushgateway") — no registry host
        # in `repo` at all.
        host, path = "registry-1.docker.io", repo
        default_realm, default_service = "https://auth.docker.io/token", "registry.docker.io"
    else:
        default_realm = default_service = None

    manifest_url = f"https://{host}/v2/{path}/manifests/{tag}"

    def _token(realm: str, service: str, scope: str) -> str:
        url = f"{realm}?service={urllib.parse.quote(service)}&scope={urllib.parse.quote(scope)}"
        with urllib.request.urlopen(urllib.request.Request(url), timeout=20) as resp:
            data = json.load(resp)
        return data.get("token") or data.get("access_token") or ""

    def _head(headers: dict[str, str]) -> str:
        req = urllib.request.Request(manifest_url, headers=headers, method="HEAD")
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.headers.get("Docker-Content-Digest", "")

    try:
        digest = _head({"Accept": _MANIFEST_ACCEPT})
    except urllib.error.HTTPError as exc:
        if exc.code != 401:
            return "", f"anonymous fallback: HTTP {exc.code} on {manifest_url}"
        www_auth = exc.headers.get("WWW-Authenticate", "") if exc.headers else ""
        realm = (re.search(r'realm="([^"]+)"', www_auth) or [None, None])[1]
        service = (re.search(r'service="([^"]+)"', www_auth) or [None, None])[1]
        scope = (re.search(r'scope="([^"]+)"', www_auth) or [None, None])[1]
        realm, service = realm or default_realm, service or default_service
        scope = scope or f"repository:{path}:pull"
        if not realm or not service:
            return "", f"anonymous fallback: no realm/service in WWW-Authenticate ({www_auth!r})"
        try:
            token = _token(realm, service, scope)
        except Exception as exc2:
            return "", f"anonymous fallback: token request failed: {exc2}"
        if not token:
            return "", "anonymous fallback: token endpoint returned no token"
        try:
            digest = _head({"Accept": _MANIFEST_ACCEPT, "Authorization": f"Bearer {token}"})
        except Exception as exc3:
            return "", f"anonymous fallback: manifest request failed: {exc3}"
    except Exception as exc:
        return "", f"anonymous fallback failed: {exc}"

    if digest and _DIGEST_RE.match(digest):
        return digest, None
    return "", "anonymous fallback: no Docker-Content-Digest header in response"


def _imagetools_digest(ref: str) -> tuple[str, str | None]:
    """Resolve `ref`'s digest via `docker buildx imagetools inspect`; if that
    fails, retry once via the anonymous registry-API fallback above before
    giving up. On total failure, the error names BOTH what the primary path
    said and what the fallback said, so a real registry gap is never
    confused with a local credential problem, or vice versa."""
    repo, _, tag = ref.rpartition(":")
    digest, primary_err = _docker_imagetools_digest(ref)
    if not primary_err:
        return digest, None
    digest, fallback_err = _anonymous_registry_digest(repo, tag)
    if not fallback_err:
        return digest, None
    return "", f"{primary_err} (anonymous fallback also failed: {fallback_err})"


def _row_result(key: str, row: dict[str, Any], *, status: str, detail: str,
                 latest_tag: str | None = None, live_digest: str = "") -> dict[str, Any]:
    return {
        "key": key, "repo": row["repo"], "tag": row["tag"], "channel": row["channel"],
        "latest_tag": latest_tag or row["tag"], "status": status,
        "live_digest": live_digest, "detail": detail,
    }


def _report_row(key: str, row: dict[str, Any], token: str | None) -> dict[str, Any]:
    repo, tag, pinned_digest = row["repo"], row["tag"], row.get("digest") or ""
    channel = row["channel"]
    resolver = row.get("resolver", "hub")

    if channel == "local":
        return _row_result(key, row, status="SKIPPED (local)", detail="-")

    if channel == "unpublished":
        # Known, deliberately, to not exist on the registry — see the row's
        # purpose: for where it's referenced from and why. No lookup: we
        # already know what it would say, and a stale network error here
        # must never be mistaken for "this one just started failing".
        return _row_result(key, row, status="UNPUBLISHED",
                            detail="not on registry (see purpose:)")

    if resolver == "imagetools":
        live, err = _imagetools_digest(f"{repo}:{tag}")
        if err:
            return _row_result(key, row, status="UNRESOLVABLE", detail=err)
        if not pinned_digest:
            return _row_result(key, row, status="NO_DIGEST", detail=live[:19], live_digest=live)
        if live != pinned_digest:
            return _row_result(key, row, status="DIGEST_CHANGED", detail=live[:19], live_digest=live)
        return _row_result(key, row, status="OK", detail=live[:19], live_digest=live)

    # resolver == hub
    assert token is not None
    live, err = _hub_digest_for(repo, tag, token)
    if err:
        return _row_result(key, row, status="UNRESOLVABLE", detail=err)

    if channel == "review":
        m = re.match(r"^(.*-review)\.\d+$", tag)
        family = m.group(1) if m else None
        family_tags: list[str] = []
        if family:
            family_tags, tags_err = _hub_tags(repo, family, token)
            if tags_err and not family_tags:
                # Current tag resolved fine above; only the sibling-tag
                # enumeration failed. Degrade to "can't tell if newer exists"
                # rather than masking it as OK.
                return _row_result(key, row, status="NO_TAGS_FOUND",
                                    detail=f"tag search failed: {tags_err}", live_digest=live)
        candidates = [(_review_num(t), t) for t in family_tags if _review_num(t) is not None]
        latest = max(candidates)[1] if candidates else tag
        if not family or not candidates:
            return _row_result(key, row, status="NO_TAGS_FOUND", detail="no -review.N siblings found",
                                live_digest=live)
        if not live:
            # Hub resolved the request but returned no digest for an
            # existing tag — rare, and distinct from both a failed lookup
            # (UNRESOLVABLE, handled above) and "not pinned yet" (NO_DIGEST).
            return _row_result(key, row, status="NO_TAGS_FOUND",
                                detail="Hub returned no digest for this tag")
        if not pinned_digest:
            return _row_result(key, row, status="NO_DIGEST", detail=live[:19], live_digest=live)
        if live != pinned_digest:
            return _row_result(key, row, status="TAG_MOVED", detail=live[:19], live_digest=live)
        if latest != tag:
            latest_live, latest_err = _hub_digest_for(repo, latest, token)
            detail = latest_live[:19] if latest_live else (latest_err or "")
            return _row_result(key, row, status="STALE", detail=detail,
                                latest_tag=latest, live_digest=latest_live)
        return _row_result(key, row, status="OK", detail=live[:19], live_digest=live)

    # channel == moving / upstream / pinned: no tag family to walk, so the
    # only question is whether the pinned digest still resolves.
    if not live:
        return _row_result(key, row, status="NO_TAGS_FOUND",
                            detail="Hub returned no digest for this tag")
    if not pinned_digest:
        return _row_result(key, row, status="NO_DIGEST", detail=live[:19], live_digest=live)
    if live != pinned_digest:
        return _row_result(key, row, status="TAG_MOVED", detail=live[:19], live_digest=live)
    return _row_result(key, row, status="OK", detail=live[:19], live_digest=live)


def _needs_hub(catalog: dict[str, Any]) -> bool:
    return any(
        row.get("resolver", "hub") == "hub" and row["channel"] not in ("local", "unpublished")
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

    print(f"{'KEY':<26} {'CHANNEL':<12} {'TAG':<40} {'STATUS':<16} {'DETAIL'}")
    bad = False
    for r in rows:
        print(f"{r['key']:<26} {r['channel']:<12} {r['tag']:<40} {r['status']:<16} {r['detail']}")
        if r["status"] == "UNRESOLVABLE":
            bad = True
    if any(r["status"] == "NO_DIGEST" for r in rows):
        print("\nNO_DIGEST: pinned by tag alone; images.sh bump can resolve it "
              "(channel review/moving only).", file=sys.stderr)
    if any(r["status"] in ("TAG_MOVED", "DIGEST_CHANGED") for r in rows):
        print("TAG_MOVED/DIGEST_CHANGED: the tag now resolves to a different image "
              "than what is pinned. images.sh bump re-pins it.", file=sys.stderr)
    if any(r["status"] == "UNPUBLISHED" for r in rows):
        print("UNPUBLISHED: known-absent from the registry (see the row's purpose: in "
              "images/catalog.yaml). Not an error; needs a push or a compose-reference "
              "removal, tracked separately.", file=sys.stderr)
    overrides = dotenv_overrides(catalog)
    if overrides and not args.only and not args.channel:
        print("\nOVERRIDDEN BY ./.env — for these vars the pin above is NOT what runs.",
              file=sys.stderr)
        print("This is the intended local-override path (compose auto-loads .env and",
              file=sys.stderr)
        print("tools/load-images-env.sh skips keys .env already sets); remove the key",
              file=sys.stderr)
        print("from .env to fall back to the catalog.", file=sys.stderr)
        for var, env_value, catalog_ref in overrides:
            print(f"  {var:<34} .env: {env_value}", file=sys.stderr)
            print(f"  {'':<34} cat.: {catalog_ref}", file=sys.stderr)
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
    # ALWAYS double-quote: an unquoted numeric-looking tag (`3.4`, `8.12`)
    # is valid YAML float syntax and would silently become a number instead
    # of a string. json.dumps gives a valid YAML double-quoted scalar (YAML's
    # double-quote escaping is a superset of JSON's) with no assumptions
    # about which tags happen to be "safe" unquoted.
    quoted_tag = json.dumps(new_tag)
    body = re.sub(r"(?m)^    tag: .*\n", lambda _m: f"    tag: {quoted_tag}\n", body, count=1)

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
        if row["channel"] == "pinned":
            print(f"refused: {key} is channel pinned (off the release line; edit tag+digest "
                  f"in images/catalog.yaml by hand, or move it back to channel review once "
                  f"the image ships on the -review.N line)", file=sys.stderr)
            continue
        if row["channel"] in ("local", "unpublished"):
            print(f"refused: {key} is channel {row['channel']} (never auto-bumped)", file=sys.stderr)
            continue
        if row["channel"] == "upstream" and not args.only:
            # Never auto-bumped in bulk — a version bump for someone else's
            # image is a deliberate upgrade, not routine maintenance. But an
            # explicit `--only KEY` is exactly that deliberate action: it
            # resolves the digest of the version tag ALREADY pinned (no
            # version-walking happens for upstream rows — see _report_row),
            # it just turns "tag pinned" into "tag+digest pinned".
            print(f"refused: {key} is channel upstream (never auto-bumped in bulk; "
                  f"use --only {key} to pin the currently-pinned version's digest)", file=sys.stderr)
            continue
        report_row = _report_row(key, row, token)
        # DIGEST_CHANGED is the imagetools-resolver counterpart of TAG_MOVED
        # (_report_row:663) — a drifted upstream row reports DIGEST_CHANGED,
        # never TAG_MOVED, so omitting it here made --only a documented no-op
        # for exactly the rows it exists to re-pin.
        if report_row["status"] not in ("STALE", "TAG_MOVED", "NO_DIGEST", "DIGEST_CHANGED"):
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
# status — the one command. Everything the other subcommands each know a
# corner of, merged into a single "what needs you" list, so nobody has to
# remember which of six commands answers a given question.
# --------------------------------------------------------------------------

# Statuses that mean a human has to do something, and the one line each.
_ACTIONABLE = {
    "STALE": "newer -review.N published — tools/images.sh bump",
    "TAG_MOVED": "tag now resolves elsewhere — tools/images.sh bump",
    "DIGEST_CHANGED": "tag now resolves elsewhere — tools/images.sh bump",
    "NO_DIGEST": "pinned by tag alone — tools/images.sh bump",
    "UNRESOLVABLE": "could not resolve at all — check the registry/network",
}


def _term_width() -> int:
    """Honour COLUMNS when it is set (CI logs set it to something sane), fall
    back to the terminal, then to 100. Clamped: below ~60 the wrapping is
    worse than not wrapping, above ~110 long notes become hard to track back
    to their subject."""
    try:
        cols = int(os.environ["COLUMNS"])
    except (KeyError, ValueError):
        cols = shutil.get_terminal_size(fallback=(100, 24)).columns
    return max(60, min(cols, 110))


def _print_notes(items: list[tuple[str, str]], indent: int) -> None:
    """Subject on its own line, note wrapped underneath. Long prose in a
    fixed-width column is unreadable; long prose on one unwrapped line is
    worse."""
    pad = " " * indent
    body = " " * (indent + 2)
    for subject, note in items:
        print(f"{pad}{subject}")
        for line in textwrap.wrap(note, width=_term_width() - indent - 2) or [""]:
            print(f"{body}{line}")


def _print_table(items: list[tuple[str, str]], indent: int) -> None:
    """Two aligned columns, for the short one-fact-each rows."""
    if not items:
        return
    width = max(len(subject) for subject, _ in items)
    for subject, note in items:
        print(f"{' ' * indent}{subject:<{width}}   {note}")


def cmd_status(args: argparse.Namespace) -> int:
    catalog = load_catalog()
    images = catalog["images"]
    broken: list[tuple[str, str]] = []      # catalog/artifacts wrong right now
    pins: list[tuple[str, str]] = []        # registry says the pin is stale
    followups: list[tuple[str, str]] = []   # someone must do something, later
    unpublished: list[tuple[str, str]] = []
    overrides: list[tuple[str, str]] = []

    # 1. offline: is the catalog valid, and are the artifacts in step with it?
    try:
        assert_invariants(catalog)
        drifted = [path.relative_to(ROOT) for path, content in render_all(catalog).items()
                    if (path.read_text(encoding="utf-8") if path.is_file() else None) != content]
        if drifted:
            broken.append((", ".join(str(d) for d in drifted),
                           "out of step with the catalog — run: tools/images.sh sync"))
    except CatalogError as exc:
        broken.append(("images/catalog.yaml", f"invalid: {exc}"))

    # 2. follow_up: notes. The whole point of the field: a reminder that
    # lives next to the thing it is about, not in somebody's head. Rows carry
    # their own; catalog-level `follow_ups:` holds the ones that belong to no
    # single row (a migration still gated on a hardware cycle, say).
    for key in sorted(images):
        note = images[key].get("follow_up")
        if note:
            followups.append((key, note))
    for note in catalog.get("follow_ups") or []:
        followups.append(("(repo)", note))
    followup_text = " ".join(note for _, note in followups).lower()

    # 3. online: pin vs registry. Skipped entirely with --offline so this
    # command still works on a plane, or in CI with no registry credentials.
    suppressed = 0
    if not args.offline:
        token = _hub_token() if _needs_hub(catalog) else None
        for key in sorted(images):
            row = images[key]
            r = _report_row(key, row, token)
            if r["status"] in _ACTIONABLE:
                pins.append((key, f"{r['status']} — {_ACTIONABLE[r['status']]}"))
            elif r["status"] == "UNPUBLISHED":
                # Don't say it twice. A follow-up that already names this row
                # (by key or by tag) carries the pending decision; repeating
                # the bare fact underneath only pads the list.
                if key.lower() in followup_text or str(row["tag"]).lower() in followup_text:
                    suppressed += 1
                else:
                    unpublished.append((key, "referenced by this repo, absent from the registry"))

    # 4. ./.env overrides — never actionable (they are the intended escape
    # hatch), always worth stating, because a pin that does not take effect
    # looks exactly like a pin that does.
    for var, env_value, _ref in dotenv_overrides(catalog):
        overrides.append((var, env_value))

    total = len(broken) + len(pins) + len(followups)
    print(f"NEEDS YOU ({total})")
    if not total:
        checked = "catalog and artifacts agree" if args.offline else \
                  "catalog, artifacts and registry agree"
        print(f"  nothing — {checked}")
    if broken:
        print(f"\n  wrong now ({len(broken)})")
        _print_notes(broken, 4)
    if pins:
        print(f"\n  stale pins ({len(pins)}) — tools/images.sh bump fixes all of these")
        _print_table(pins, 4)
    if followups:
        print(f"\n  follow-ups ({len(followups)}) — from images/catalog.yaml; "
              f"delete the entry when done")
        _print_notes(followups, 4)

    fyi_total = len(unpublished) + len(overrides)
    print(f"\nFYI ({fyi_total}) — known and deliberate, no action")
    if overrides:
        print(f"\n  overridden by ./.env ({len(overrides)}) — for these the catalog "
              f"pin is NOT what runs")
        _print_table(overrides, 4)
    if unpublished:
        print(f"\n  unpublished ({len(unpublished)})")
        _print_table(unpublished, 4)
    if suppressed:
        print(f"\n  ({suppressed} unpublished row(s) already named in a follow-up above)")
    if args.offline:
        print("\n  registry not checked (--offline)")

    print("\nNot covered here: tools/images.sh baked (needs docker), "
          "tools/images.sh drift (needs the generator image).")
    return 1 if total else 0


def cmd_refs(args: argparse.Namespace) -> int:
    catalog = load_catalog()
    assert_invariants(catalog)
    for ref in pullable_refs(catalog, all_catalog=args.all_catalog, development=args.development):
        print(ref)
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
    sub.add_parser("selftest").set_defaults(fn=cmd_selftest)

    p_status = sub.add_parser("status")
    p_status.add_argument("--offline", action="store_true",
                          help="skip every registry lookup")
    p_status.set_defaults(fn=cmd_status)

    p_report = sub.add_parser("report")
    p_report.add_argument("--only")
    p_report.add_argument("--channel", choices=sorted(VALID_CHANNELS))
    p_report.set_defaults(fn=cmd_report)

    p_bump = sub.add_parser("bump")
    p_bump.add_argument("--only")
    p_bump.add_argument("--channel", choices=sorted(VALID_CHANNELS))
    p_bump.set_defaults(fn=cmd_bump)

    p_refs = sub.add_parser("refs")
    p_refs.add_argument("--all-catalog", action="store_true")
    p_refs.add_argument("--development", action="store_true")
    p_refs.set_defaults(fn=cmd_refs)

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
