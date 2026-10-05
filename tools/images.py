#!/usr/bin/env python3
"""tools/images.py — one canonical image catalog: render, verify, report, bump.

See images/catalog.yaml (the single authored source) and
docs/adr/0002-one-image-catalog.md (why this exists). Subcommands:

  sync     regenerate every generated artifact from images/catalog.yaml (offline)
  verify   run selftest, then regenerate into a tmp dir and diff against the
           committed artifacts, exit 1 on any difference or selftest failure
           (offline — the CI gate). `verify --release` (automatic in CI when
           GITHUB_BASE_REF is release/v1.0.0 or main) also FAILS on any
           `pending:` row, any -rc tag in a release channel, and any tag-only
           ref in a channel env file or a pack lock: what ships is exact.
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
                        v1_dashboard_backend), print `<VAR>_DEFAULT<TAB><ref>`
                        for each baked row, so baked-pin checking follows the
                        catalog instead of hardcoded var names. VAR is the
                        baked row's release-channel (or product_env) variable.

Two more catalog-driven checks, lenient by design (a WARNING, never a
failure), run by `verify` and `status`:

  pending rows          a pinned row with `pending:` names its tag but not
                        its digest yet (an rc image not yet pinned)
  host pin labels       the host image's tevv.host.kit_image label must name
                        the pinned kit digest, and so must the authoring
                        image's tevv.authoring.kit_image; its
                        tevv.authoring.shared_set_id must equal the host's
                        tevv.host.shared_set_id; shared_plugins_source or
                        host_check_waived on it warn. `verify` reads local
                        images only (no registry); `status` also asks the
                        registry. `verify --release` fails on these warnings.

One copy taken from an image, never hand-edited:

  host contract         a release channel's `host_contract` file (the runtime
                        host's compatibility contract, which mns-stacks
                        generate reads without a Docker socket) is copied by
                        `sync` from the pinned host image's
                        /opt/tevv/host-contract/. `verify` fails when it
                        differs from that image; it is a NOTE when the host
                        image is not in the local image store.
"""
from __future__ import annotations

import argparse
import copy
import base64
import fnmatch
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
DEVELOPMENT_ENV_PATH = ROOT / "images" / "development.generated.env"
PLATFORM_ENV_PATH = ROOT / "images" / "platform-images.generated.env"
ENV_EXAMPLE_PATH = ROOT / ".env.example"
DOTENV_PATH = ROOT / ".env"

GENERATED_MARKER = (
    "# GENERATED from images/catalog.yaml — see docs/adr/0002-one-image-catalog.md. "
    "Do not hand-edit; run tools/images.sh sync."
)

VALID_CHANNELS = {"review", "moving", "upstream", "local", "unpublished", "pinned"}
VALID_RESOLVERS = {"hub", "imagetools"}

COMPOSE_FILE_FOR_GROUP = {
    "dashboard": "docker-compose-dashboard.yml, inline images",
}


class CatalogError(SystemExit):
    def __init__(self, msg: str):
        super().__init__(f"images/catalog.yaml: {msg}")


# --------------------------------------------------------------------------
# Loading + validation
# --------------------------------------------------------------------------

# A traced tag ends in -g<short sha> (tools/traced-tags.sh in the dashboard and
# the other component repos). That suffix is the only machine-readable link an
# image has back to the commit it was built from, short of pulling the image
# and reading its labels — which `status` deliberately does not do, since it is
# a registry-only command.
_TRACED_SHA_RE = re.compile(r"-g([0-9a-f]{7,40})$")


def _sha_from_tag(tag: str) -> str | None:
    m = _TRACED_SHA_RE.search(tag or "")
    return m.group(1) if m else None


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
        if "source" in row:
            src = row["source"]
            if not isinstance(src, dict):
                raise CatalogError(f"images.{key}.source must be a mapping")
            if not isinstance(src.get("repo"), str) or "/" not in src["repo"]:
                raise CatalogError(
                    f"images.{key}.source.repo must be 'owner/name' — the repository the "
                    f"image is BUILT FROM, so `status` can say when that source has moved "
                    f"past what is pinned here")
            for field in ("branch", "commit"):
                if field in src and not isinstance(src[field], str):
                    raise CatalogError(f"images.{key}.source.{field} must be a string")
            if "commit" in src and not re.fullmatch(r"[0-9a-f]{7,40}", src["commit"]):
                raise CatalogError(
                    f"images.{key}.source.commit must be a hex sha (7-40 chars), "
                    f"got {src['commit']!r}")
            if "commit" not in src and not _sha_from_tag(row["tag"]):
                raise CatalogError(
                    f"images.{key}.source needs a commit: the tag {row['tag']!r} carries no "
                    f"traced '-g<sha>' suffix to read it from")
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
        pending = row.get("pending")
        if pending is not None:
            if not isinstance(pending, str) or not pending.strip():
                raise CatalogError(
                    f"images.{key}.pending must be a non-empty string saying who pins "
                    f"the digest, and when")
            if row["channel"] != "pinned" or row["digest"] is not None:
                raise CatalogError(
                    f"images.{key} is pending, so it must be channel pinned with "
                    f"digest: null (drop `pending:` once the digest is pinned)")
        if "pull" in row and not isinstance(row["pull"], bool):
            raise CatalogError(f"images.{key}.pull must be true or false")
        if row["channel"] == "pinned" and not row["digest"] and pending is None:
            raise CatalogError(
                f"images.{key} is channel pinned but has no digest — a pinned row exists "
                f"precisely to name one exact image; without a digest it names nothing. "
                f"If the digest is not known yet, say so with `pending: \"<why>\"`."
            )
        # published_by is an ownership claim. Until the product's stable
        # release/tag policy is settled, owned rows use a deliberately frozen
        # pinned tag rather than a mutable publishing alias.
        publisher = row.get("published_by")
        if publisher is not None:
            if not isinstance(publisher, str) or not publisher.strip():
                raise CatalogError(f"images.{key}.published_by must be a non-empty string")
            if row["channel"] != "pinned":
                raise CatalogError(
                    f"images.{key} is published_by {publisher!r} but sits on channel "
                    f"{row['channel']!r}. An image we publish ourselves must use an "
                    f"immutable pinned tag until the stable release policy is defined."
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
    for group_name in ("product_env", "image_sets", "compose_env"):
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
            host_pin = spec.get("host_pin")
            if host_pin is not None:
                if not isinstance(host_pin, dict) or set(host_pin) - {"host", "kit", "authoring"} \
                        or "host" not in host_pin:
                    raise CatalogError(
                        f"consumers.release_channels.{name}.host_pin: a mapping with host "
                        f"and optionally kit and authoring")
                for role, image_key in host_pin.items():
                    if image_key not in images:
                        raise CatalogError(
                            f"consumers.release_channels.{name}.host_pin.{role} references "
                            f"unknown image key {image_key!r}")
            contract = spec.get("host_contract")
            if contract is not None and (not isinstance(contract, str) or not contract
                                         or not (host_pin or {}).get("host")):
                raise CatalogError(
                    f"consumers.release_channels.{name}.host_contract: a repo path, and the "
                    f"channel needs a host_pin.host to copy it from")
    # Optional: only a catalog that ships pack release locks declares these.
    locks = consumers.get("pack_locks")
    if locks is not None:
        if not isinstance(locks, dict):
            raise CatalogError("consumers.pack_locks: must be a mapping")
        for lock_path, mapping in locks.items():
            if not isinstance(mapping, dict) or not mapping:
                raise CatalogError(
                    f"consumers.pack_locks.{lock_path}: needs a non-empty "
                    f"lock-key -> image-key mapping")
            for lock_key, image_key in mapping.items():
                if image_key not in data["images"]:
                    raise CatalogError(
                        f"consumers.pack_locks.{lock_path}.{lock_key} "
                        f"references unknown image key {image_key!r}")


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


DEFAULT_CHANNEL = "v1"


def release_channel(catalog: dict[str, Any], name: str = DEFAULT_CHANNEL) -> dict[str, Any]:
    channels = catalog["consumers"].get("release_channels") or {}
    if name not in channels:
        raise CatalogError(
            f"consumers.release_channels.{name} is not declared; known channels: "
            + ", ".join(sorted(channels)) if channels else f"consumers.release_channels.{name} is not declared")
    return channels[name]


def channel_image_set(catalog: dict[str, Any], name: str = DEFAULT_CHANNEL) -> str:
    """The image_sets entry generated stacks use under this channel (MNS_IMAGE_SET)."""
    return str(release_channel(catalog, name).get("image_set") or name)


def channel_keys(catalog: dict[str, Any], name: str = DEFAULT_CHANNEL) -> set[str]:
    """Every catalog key the product touches under one release channel: the
    channel's own vars, its image set, and the channel-independent dashboard
    and tooling rows."""
    consumers = catalog["consumers"]
    keys = set(release_channel(catalog, name)["vars"].values())
    image_set = channel_image_set(catalog, name)
    if image_set not in consumers["image_sets"]:
        raise CatalogError(f"consumers.release_channels.{name}.image_set {image_set!r} is not an image_sets entry")
    keys |= _flatten_leaf_keys(resolved_image_set_keys(catalog, image_set))
    for group in ("dashboard", "tools"):
        keys |= set(consumers["product_env"].get(group, {}).values())
    keys |= set(consumers["compose_env"].get("dashboard", {}).values())
    return keys


def pullable_refs(catalog: dict[str, Any], *, all_catalog: bool = False, development: bool = False,
                  channel: str = DEFAULT_CHANNEL) -> list[str]:
    """Return unique active refs in production or tag-only development form.

    `channel: local` rows are built on this machine and have no registry
    counterpart, so they are never pullable: they are left out here and
    `tools/ensure-images.sh` / `tools/doctor.sh` check they exist locally
    (`local-refs`). `channel: unpublished` rows are refused: something the
    product needs that nobody has, anywhere. `pull: false` rows (the host
    kit image) are pinned for checks only and never pulled.
    """
    images = catalog["images"]
    if all_catalog:
        keys = {
            key for key, row in images.items()
            if row["channel"] not in ("local", "unpublished") and row.get("pull", True)
        }
    else:
        keys = channel_keys(catalog, channel)
        unavailable = sorted(
            key for key in keys if images[key]["channel"] == "unpublished"
        )
        if unavailable:
            raise CatalogError(
                "active product references unavailable image(s): " + ", ".join(unavailable))
        keys = {key for key in keys
                if images[key]["channel"] != "local" and images[key].get("pull", True)}
    ref_for = development_ref if development else image_ref
    return sorted({ref_for(images, key) for key in keys})


def local_refs(catalog: dict[str, Any], channel: str = DEFAULT_CHANNEL) -> list[str]:
    """`channel: local` rows a release channel depends on — images that must
    already exist in the local Docker store because nothing can pull them.
    Empty for a fully published channel (v1)."""
    images = catalog["images"]
    return sorted(image_ref(images, key) for key in channel_keys(catalog, channel)
                  if images[key]["channel"] == "local")



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
                        base: dict[str, Any] | None,
                        resolve_leaf: Any = image_ref) -> dict[str, Any]:
    """Deep-merge `raw.images` (key -> catalog key) over `base` (already-resolved
    refs), matching MnS-Integration-Platform's merge_dicts (scalars replaced
    wholesale, mappings merged key-by-key).

    `resolve_leaf` turns a catalog KEY into a ref. The development overlay
    passes development_ref here rather than post-processing the rendered
    production tree: a ref->ref rewrite table keys on the rendered string, so
    two rows sharing a repo:tag@digest would collapse onto whichever
    latest_tag iterated last, silently and with no error.
    """
    def merge(base_node: Any, overlay_keys: Any) -> Any:
        if isinstance(overlay_keys, dict):
            # deepcopy, not dict(): a shallow copy shares the untouched nested
            # mappings (autopilots: ...) with the inherited set, and
            # yaml.safe_dump then emits them as `*id001` aliases instead of
            # repeating the refs — valid YAML, unreadable generated file.
            result = copy.deepcopy(base_node) if isinstance(base_node, dict) else {}
            for k, v in overlay_keys.items():
                result[k] = merge(result.get(k), v)
            return result
        # overlay_keys is a leaf: a catalog image key -> resolve to a ref
        return resolve_leaf(images, overlay_keys)

    resolved = merge(base or {}, raw.get("images") or {})
    return {
        "pull_policy": raw.get("pull_policy", "if_not_present"),
        "images": resolved,
    }


def resolved_image_sets(catalog: dict[str, Any],
                        resolve_leaf: Any = image_ref) -> dict[str, dict[str, Any]]:
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
            resolved[base_name] = _resolve_image_set(images, base_raw, None, resolve_leaf)
            base = resolved[base_name]["images"]
        resolved[name] = _resolve_image_set(images, raw, base, resolve_leaf)
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
    header = (
        "schema: mns.image_sets.v1\n\n"
        f"{GENERATED_MARKER}\n"
        "# Development overlay: tag-only refs plus pull_policy: missing let a\n"
        "# local build win and pull the published tag only when it is absent.\n\n"
    )
    body = {"image_sets": resolved_image_sets(catalog, development_ref)}
    return header + yaml.safe_dump(body, sort_keys=False, default_flow_style=False)


def render_development_env(catalog: dict[str, Any]) -> str:
    """Dashboard/product defaults using tag-only development aliases."""
    images = catalog["images"]
    consumers = catalog["consumers"]
    groups = [
        consumers["release_channels"][DEFAULT_CHANNEL]["vars"],
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


def render_channel_env(catalog: dict[str, Any], name: str = DEFAULT_CHANNEL) -> str:
    """The release channel's env file (its `emits:`, e.g. images/v1.0.0.generated.env).

    Every row is rendered with its immutable release tag and manifest digest.
    Mutable -latest aliases are deliberately not emitted here: production
    runs remain reproducible until the catalog is advanced and regenerated.
    """
    images = catalog["images"]
    channel = catalog["consumers"]["release_channels"].get(name)
    if not channel or not channel.get("vars"):
        raise CatalogError(
            f"consumers.release_channels.{name}.vars is missing; "
            f"{channel.get('emits') if channel else name} has nothing to emit")
    group = channel["vars"]
    lines = [
        GENERATED_MARKER,
        "",
        f"# Coordinated {name.replace('_', '-')} release pins: mns-packs, mns-stacks,",
        "# ScenarioLab, the runtime host and its kit, the bridge and the dashboard.",
        "#",
        "# Every ref uses an immutable date/version tag and manifest digest.",
        "# The corresponding -latest aliases are for discovery and publishing;",
        "# production runs the exact refs below. Edit images/catalog.yaml and",
        "# re-run tools/images.sh sync to advance the approved release.",
    ]
    pending_rows = [key for key in group.values() if images[key].get("pending")]
    if pending_rows:
        lines += [
            "#",
            "# Tag-only refs below are catalog rows marked `pending:` (an rc image",
            "# whose digest is not pinned yet): " + ", ".join(pending_rows) + ".",
            "# tools/images.sh verify warns about each until it is filled in.",
        ]
    local_rows = [key for key in group.values() if images[key]["channel"] == "local"]
    if local_rows:
        lines += [
            "#",
            "# channel: local rows below carry no digest: they are built on this",
            "# machine (see the row's purpose in images/catalog.yaml) and nothing",
            "# can pull them. tools/images.sh refs --channel " + name + " lists what",
            "# IS pullable; the Makefile checks the local tags exist before launch.",
        ]
    lines.append("")
    lines += [f"{var}={image_ref(images, key)}" for var, key in group.items()]
    if channel.get("image_set"):
        lines += ["", "# The image_sets entry generated stacks select under this channel.",
                  f"MNS_IMAGE_SET={channel['image_set']}"]
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


def render_all(catalog: dict[str, Any]) -> dict[Path, str]:
    out = {
        PRODUCT_ENV_PATH: render_product_env(catalog),
        IMAGE_SET_PATH: render_image_set(catalog),
        DEVELOPMENT_IMAGE_SET_PATH: render_development_image_set(catalog),
        DEVELOPMENT_ENV_PATH: render_development_env(catalog),
        PLATFORM_ENV_PATH: render_platform_env(catalog),
    }
    for name, spec in (catalog["consumers"].get("release_channels") or {}).items():
        out[ROOT / spec["emits"]] = render_channel_env(catalog, name)
    return out


# --------------------------------------------------------------------------
# Invariants (sync + verify both assert these)
# --------------------------------------------------------------------------

def _all_env_vars(catalog: dict[str, Any]) -> dict[str, str]:
    """var -> image key, across product_env and compose_env (image_sets has
    no env vars of its own). Deliberately excludes release_channels: a
    channel exists precisely to bind the review channel's variable names to a
    different set of images, and it emits to its own file so the two never
    meet."""
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
    is only ever that it is invisible, which is what this surfaces."""
    if not DOTENV_PATH.is_file():
        return []
    env: dict[str, str] = {}
    for line in DOTENV_PATH.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\s*([A-Z][A-Z0-9_]*)\s*=\s*(.*?)\s*$", line)
        if m:
            env[m.group(1)] = m.group(2)
    var_to_key = dict(_all_env_vars(catalog))
    out = []
    for var, key in sorted(var_to_key.items()):
        if var in env:
            out.append((var, env[var], image_ref(catalog["images"], key)))
    return out


_MUTABLE_SCAN_GLOBS = ("*.yml", "*.yaml", "*.sh", "*.md", "Makefile")
_MUTABLE_SCAN_SKIP_DIRS = {
    ".git", "graphify-out", "generated", "images", "docs/adr",
}


def owned_tag_prefixes(images: dict[str, Any]) -> set[str]:
    """Return ``repo:component`` prefixes for images this project publishes."""
    out: set[str] = set()
    for key, row in images.items():
        if not row.get("published_by"):
            continue
        tag = row["tag"]
        component = re.sub(r"-(v\d+\.\d+\.\d+|latest|review)\b.*$", "", tag)
        if component == tag:
            raise CatalogError(
                f"images.{key}: published_by tag {tag!r} does not match "
                "-v<x.y.z>, -latest or -review<N>, so the mutable-tag guard "
                "cannot derive its component prefix"
            )
        out.add(f"{row['repo']}:{component}")
    return out


def _tracked_files(root: Path) -> list[Path] | None:
    """Return files Git tracks below root, or None when Git is unavailable."""
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    names = result.stdout.decode("utf-8", "surrogateescape").split("\0")
    return [root / name for name in names if name]


def scan_for_mutable_refs(root: Path, prefixes: set[str]) -> list[tuple[str, int, str]]:
    """Find tracked text references to a mutable ``-latest`` owned tag."""
    if not prefixes:
        return []
    tracked = _tracked_files(root)
    if tracked is not None:
        candidates = [
            path for path in tracked
            if any(fnmatch.fnmatch(path.name, pattern) for pattern in _MUTABLE_SCAN_GLOBS)
        ]
    else:
        candidates = [path for pattern in _MUTABLE_SCAN_GLOBS for path in root.rglob(pattern)]

    hits: list[tuple[str, int, str]] = []
    for path in sorted(set(candidates)):
        rel = path.relative_to(root).as_posix()
        if any(rel == directory or rel.startswith(f"{directory}/")
               for directory in _MUTABLE_SCAN_SKIP_DIRS):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            for prefix in prefixes:
                if f"{prefix}-latest" in line:
                    hits.append((rel, line_number, line.strip()))
    return hits


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
                "catalog var(s) collide with .env.example: " + ", ".join(collide)
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

    # 4. A release channel refreshes exact pins explicitly, then starts from the
    # verified local cache. Keep the authored catalog from silently restoring
    # per-run registry checks and defeating that workflow.
    release_channels = catalog["consumers"].get("release_channels") or {}
    for name, spec in release_channels.items():
        set_name = spec.get("image_set") or name
        image_set = (catalog["consumers"].get("image_sets") or {}).get(set_name) or {}
        if image_set.get("pull_policy") != "missing":
            raise CatalogError(
                f"image_sets.{set_name} must use pull_policy: missing for channel {name}; "
                "refresh images explicitly with tools/pull-all-images.sh"
            )

    # 5. A fallback naming an owned mutable alias bypasses the catalog's exact
    # tag+digest pin on a fresh checkout. Scan only tracked authored files so
    # untracked review notes cannot make verification machine-dependent.
    hits = scan_for_mutable_refs(ROOT, owned_tag_prefixes(images))
    if hits:
        detail = "; ".join(f"{path}:{line}" for path, line, _ in hits[:5])
        raise CatalogError(
            f"{len(hits)} reference(s) to a mutable -latest tag for an image we publish: "
            f"{detail}. Point them at the catalog's immutable pinned tag instead."
        )


def _flatten_leaf_keys(node: Any) -> set[str]:
    out: set[str] = set()
    if isinstance(node, dict):
        for v in node.values():
            out |= _flatten_leaf_keys(v)
    elif isinstance(node, str):
        out.add(node)
    return out


def resolved_image_set_keys(catalog: dict[str, Any], name: str) -> dict[str, Any]:
    """The catalog KEYS (not refs) an image set resolves to, `inherits` applied.

    `resolved_image_sets` turns keys into refs as it merges; this is the same
    merge with the identity resolver, so channel_keys() can ask "which rows
    does image set X touch" without parsing refs back into keys.
    """
    return resolved_image_sets(catalog, lambda _images, key: key)[name]["images"]


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
    for path, ref, content, why in host_contract_copies(catalog):
        rel = path.relative_to(ROOT)
        if content is None:
            print(f"NOTE: kept {rel}: cannot read {HOST_CONTRACT_IN_IMAGE} from {ref} ({why}); "
                  f"pull the host image and run sync again", file=sys.stderr)
        elif not path.is_file() or path.read_bytes() != content:
            path.write_bytes(content)
            print(f"wrote {rel} (copied from {ref})")
    return 0


HOST_CONTRACT_IN_IMAGE = "/opt/tevv/host-contract/runtime-host-compatibility.json"


def image_file(ref: str, path: str) -> tuple[bytes | None, str]:
    """(content, "") of one file inside a LOCAL image, or (None, why not).
    Never pulls and never starts the image: docker create + docker cp."""
    try:
        if subprocess.run(["docker", "image", "inspect", ref], capture_output=True,
                          timeout=30).returncode != 0:
            return None, "not in the local image store"
        made = subprocess.run(["docker", "create", ref, "true"], capture_output=True,
                              text=True, timeout=60)
        if made.returncode != 0:
            return None, (made.stderr or "docker create failed").strip().splitlines()[0]
        container = made.stdout.strip()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp) / Path(path).name
                copied = subprocess.run(["docker", "cp", f"{container}:{path}", str(out)],
                                        capture_output=True, text=True, timeout=60)
                if copied.returncode != 0:
                    return None, f"{path} is not in the image"
                return out.read_bytes(), ""
        finally:
            subprocess.run(["docker", "rm", container], capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"docker unavailable ({exc.__class__.__name__})"


def host_contract_copies(catalog: dict[str, Any], read: Any = None
                         ) -> list[tuple[Path, str, bytes | None, str]]:
    """[(repo path, host ref, the image's contract or None, why not)] for each
    release channel that declares a host_contract."""
    read = read or (lambda ref: image_file(ref, HOST_CONTRACT_IN_IMAGE))
    out = []
    for _name, spec in sorted((catalog["consumers"].get("release_channels") or {}).items()):
        if not spec.get("host_contract"):
            continue
        ref = image_ref(catalog["images"], spec["host_pin"]["host"])
        content, why = read(ref)
        out.append((ROOT / spec["host_contract"], ref, content, why))
    return out


def host_contract_findings(catalog: dict[str, Any], read: Any = None
                           ) -> list[tuple[str, str]]:
    """[(DRIFT|NOTE, message)]: DRIFT when the copy differs from the pinned host
    image's contract; NOTE when the image is not local, so nothing was compared."""
    out = []
    for path, ref, content, why in host_contract_copies(catalog, read):
        rel = path.relative_to(ROOT)
        if content is None:
            out.append(("NOTE", f"{rel} not compared with {ref}: {why}"))
        elif not path.is_file() or path.read_bytes() != content:
            out.append(("DRIFT", f"{rel} differs from {HOST_CONTRACT_IN_IMAGE} in {ref}"))
    return out


def pack_lock_drift(catalog: dict[str, Any]) -> list[str]:
    """Assert packs/*.lock.json restates the catalog's pins verbatim.

    The lock exists because tools/install_demo_packs.py must run mns-packs
    before this repo has resolved anything, so it cannot read the
    catalog. That makes it a second copy of a pin — the same shape that let
    product-images.env drift to review.20 against this repo's review.22 — so
    the copy is asserted here instead of trusted. Offline: pure file compare.
    """
    images = catalog["images"]
    problems: list[str] = []
    for lock_path, mapping in (catalog["consumers"].get("pack_locks") or {}).items():
        path = ROOT / lock_path
        if not path.is_file():
            problems.append(f"{lock_path}: declared in consumers.pack_locks but not on disk")
            continue
        try:
            lock = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            problems.append(f"{lock_path}: not valid JSON ({exc})")
            continue
        required = lock.get("required_images") or {}
        for lock_key, image_key in sorted(mapping.items()):
            want = image_ref(images, image_key)
            got = required.get(lock_key)
            if got is None:
                problems.append(
                    f"{lock_path}: required_images.{lock_key} is missing "
                    f"(catalog {image_key} = {want})")
            elif got != want:
                problems.append(
                    f"{lock_path}: required_images.{lock_key} does not match "
                    f"catalog {image_key}\n    lock:    {got}\n    catalog: {want}")
        for lock_key in sorted(set(required) - set(mapping)):
            problems.append(
                f"{lock_path}: required_images.{lock_key} is pinned but not declared "
                f"in consumers.pack_locks, so nothing checks it")
    return problems


def pending_rows(catalog: dict[str, Any]) -> list[tuple[str, str]]:
    """(key, note) for every row whose digest is still pending."""
    return [(key, " ".join(str(row["pending"]).split()))
            for key, row in sorted(catalog["images"].items()) if row.get("pending")]


_DIGEST_IN_RE = re.compile(r"sha256:[0-9a-f]{64}")

LABEL_HOST_KIT_IMAGE = "tevv.host.kit_image"
LABEL_HOST_SHARED_SET = "tevv.host.shared_set_id"
# The authoring image's kit-only labels (TEVV-Authoring #33): the kit it was
# built against, the shared plugin set it compiled, and two escape hatches.
LABEL_AUTHORING_KIT_IMAGE = "tevv.authoring.kit_image"
LABEL_AUTHORING_SHARED_SET = "tevv.authoring.shared_set_id"
LABEL_AUTHORING_PLUGINS_SOURCE = "tevv.authoring.shared_plugins_source"
LABEL_AUTHORING_CHECK_WAIVED = "tevv.authoring.host_check_waived"
# Before #33 the authoring image named the host instead of the kit.
LABEL_AUTHORING_HOST_IMAGE_OLD = "tevv.authoring.host_image"


def _label_set(value: str | None) -> bool:
    """A label that is present and not a spelled-out "off"."""
    return bool(value) and str(value).strip().lower() not in ("0", "false", "no", "none", "off")


def _digest_in(value: str | None) -> str | None:
    """The sha256:<hex> a label or ref names, or None. A label may carry a bare
    digest or a full repo[:tag]@sha256 ref; the digest is what is compared."""
    found = _DIGEST_IN_RE.findall(value or "")
    return found[-1] if found else None


def image_labels(ref: str, *, remote: bool) -> tuple[dict[str, str] | None, str]:
    """(labels, "") for an image, or (None, why not). Local image store first;
    the registry only with remote=True. Never pulls."""
    try:
        proc = subprocess.run(["docker", "image", "inspect", "--format",
                               "{{json .Config.Labels}}", ref],
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"docker unavailable ({exc.__class__.__name__})"
    if proc.returncode == 0:
        try:
            return json.loads(proc.stdout or "null") or {}, ""
        except json.JSONDecodeError:
            return None, "unreadable labels"
    if not remote:
        return None, "not in the local image store"
    try:
        proc = subprocess.run(["docker", "buildx", "imagetools", "inspect", ref,
                               "--format", "{{json .Image}}"],
                              capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"docker buildx unavailable ({exc.__class__.__name__})"
    if proc.returncode != 0:
        reason = (proc.stderr or proc.stdout or "").strip().splitlines()
        return None, reason[0] if reason else "imagetools inspect failed"
    try:
        image = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None, "unreadable imagetools output"
    # A multi-platform index maps platform -> image; any one carries the labels.
    if isinstance(image, dict) and "config" not in image and image:
        image = next(iter(image.values()))
    return ((image or {}).get("config") or {}).get("Labels") or {}, ""


def host_pin_findings(catalog: dict[str, Any], *, remote: bool,
                      labels_of: Any = None) -> list[tuple[str, str]]:
    """[(WARNING|NOTE, message)] for each release channel's host_pin.

    One kit, three places: the catalog's kit row, the host image's
    `tevv.host.kit_image` and the authoring image's `tevv.authoring.kit_image`
    must name the same digest, and the authoring image's
    `tevv.authoring.shared_set_id` must equal the host's
    `tevv.host.shared_set_id`. An authoring image built from a dev plugin source
    (`tevv.authoring.shared_plugins_source`) or with its host check waived
    (`tevv.authoring.host_check_waived`) is flagged too.

    Lenient: a disagreement is recoverable (repin, or rebuild the authoring
    image), so it is a WARNING; `verify --release` turns the WARNINGs into
    failures. A check that cannot run (image not local, digest pending) is a
    NOTE, never a pass in disguise.
    """
    images = catalog["images"]
    labels_of = labels_of or (lambda ref: image_labels(ref, remote=remote))
    out: list[tuple[str, str]] = []
    for name, spec in sorted((catalog["consumers"].get("release_channels") or {}).items()):
        pin = spec.get("host_pin") or {}
        if not pin:
            continue
        host_key, kit_key, auth_key = pin["host"], pin.get("kit"), pin.get("authoring")
        host_row = images[host_key]
        kit_digest = images[kit_key].get("digest") if kit_key else None
        kit_ref = image_ref(images, kit_key) if kit_key else "<the pinned host kit>"
        rebuild = (f"rebuild the authoring image with --kit {kit_ref} --strict"
                   if kit_key else "rebuild the authoring image against the pinned kit, --strict")

        host_labels: dict[str, str] | None = None
        if not host_row.get("digest"):
            out.append(("NOTE", f"{name}: host checks skipped: {host_key} has no digest yet"))
        else:
            host_ref = image_ref(images, host_key)
            host_labels, why = labels_of(host_ref)
            if host_labels is None:
                out.append(("NOTE", f"{name}: host checks skipped: {host_ref} {why}"))
        host_kit = _digest_in((host_labels or {}).get(LABEL_HOST_KIT_IMAGE))

        if host_labels is not None and kit_key:
            if not host_labels.get(LABEL_HOST_KIT_IMAGE):
                out.append(("WARNING", f"{name}: {host_key} has no {LABEL_HOST_KIT_IMAGE} label, "
                            f"so its kit cannot be confirmed; pin a host built with the kit image"))
            elif not kit_digest:
                out.append(("WARNING", f"{name}: {kit_key} is pending, but {host_key} names its kit "
                            f"{host_kit}: pin that digest (tools/images.sh bump --only {kit_key})"))
            elif host_kit != kit_digest:
                out.append(("WARNING", f"{name}: {host_key}'s {LABEL_HOST_KIT_IMAGE} is {host_kit}, "
                            f"but {kit_key} pins {kit_digest}: pin the kit the host was built with"))

        if not auth_key:
            continue
        auth_ref = image_ref(images, auth_key)
        auth_labels, why = labels_of(auth_ref)
        if auth_labels is None:
            out.append(("NOTE", f"{name}: authoring checks skipped: {auth_ref} {why}"))
            continue

        # The kit the authoring image was built against, against the pinned
        # kit (or the host's, while the kit row is still pending).
        want_kit = kit_digest or host_kit
        auth_kit = _digest_in(auth_labels.get(LABEL_AUTHORING_KIT_IMAGE))
        if not auth_labels.get(LABEL_AUTHORING_KIT_IMAGE):
            if auth_labels.get(LABEL_AUTHORING_HOST_IMAGE_OLD):
                out.append(("WARNING", f"{name}: {auth_key} predates the kit labels (it records "
                            f"{LABEL_AUTHORING_HOST_IMAGE_OLD}, not {LABEL_AUTHORING_KIT_IMAGE}), "
                            f"so its kit cannot be confirmed; {rebuild}"))
            else:
                out.append(("WARNING", f"{name}: {auth_key} has no {LABEL_AUTHORING_KIT_IMAGE} "
                            f"label, so the kit it was built against is unknown; {rebuild}"))
        elif not want_kit:
            out.append(("NOTE", f"{name}: {auth_key} names kit {auth_kit}; nothing to compare it "
                        f"with until {kit_key or 'the kit row'} is pinned"))
        elif auth_kit != want_kit:
            out.append(("WARNING", f"{name}: {auth_key} was built against kit {auth_kit or '?'}, "
                        f"but the pinned kit is {want_kit}; {rebuild}"))

        source = auth_labels.get(LABEL_AUTHORING_PLUGINS_SOURCE)
        if source:
            out.append(("WARNING", f"{name}: {auth_key} compiled its shared plugins from "
                        f"{source} ({LABEL_AUTHORING_PLUGINS_SOURCE}), not from the pinned host's "
                        f"commit; {rebuild}"))
        waived = auth_labels.get(LABEL_AUTHORING_CHECK_WAIVED)
        if _label_set(waived):
            out.append(("WARNING", f"{name}: {auth_key} was built with its host check waived "
                        f"({LABEL_AUTHORING_CHECK_WAIVED}={waived}); {rebuild}"))

        if host_labels is None:
            out.append(("NOTE", f"{name}: shared plugin set not compared: no host labels"))
            continue
        host_set = host_labels.get(LABEL_HOST_SHARED_SET)
        auth_set = auth_labels.get(LABEL_AUTHORING_SHARED_SET)
        if not host_set or not auth_set:
            missing = [label for label, value in ((LABEL_HOST_SHARED_SET, host_set),
                                                  (LABEL_AUTHORING_SHARED_SET, auth_set))
                       if not value]
            out.append(("WARNING", f"{name}: shared plugin set not comparable: "
                        f"{', '.join(missing)} missing"))
        elif host_set != auth_set:
            out.append(("WARNING", f"{name}: {auth_key} shared_set_id {auth_set} differs from "
                        f"{host_key}'s {host_set}: the editor compiled other shared plugins "
                        f"than the runtime (`mns-packs host check` lists them); {rebuild}"))
    return out


RELEASE_BASE_REFS = ("release/v1.0.0", "main")
_RC_TAG_RE = re.compile(r"-rc(?:[.\-]?\d+)?(?:$|-)")
# What a release may pin, as an allowlist: `<name>-vX.Y.Z`, and for the rows
# built with traced tags (the dashboard: tools/traced-tags.sh in its repo)
# `<name>-vX.Y.Z-g<sha>`. Anything else (`-rc.services.N`, `-live.N`, `-zones.N`,
# a branch build such as `...-v0.4.5-g<sha>` of a row that is not traced, a
# test tag) is not a release, however it is spelled.
_RELEASE_TAG_RE = re.compile(r"-v\d+\.\d+\.\d+$")
_TRACED_RELEASE_TAG_RE = re.compile(r"-v\d+\.\d+\.\d+-g[0-9a-f]{7,40}$")
TRACED_TAG_ROWS = frozenset({"v1_dashboard_backend", "v1_dashboard_frontend"})


def release_tag_problem(key: str, var: str, tag: str) -> str | None:
    """Why `tag` cannot be a release pin for catalog row `key`, or None."""
    if _RC_TAG_RE.search(tag):
        return (f"images.{key} ({var}) is on the release-candidate tag {tag!r}: retag the "
                f"accepted rc and pin it (tools/images.sh bump --only {key} --tag <release tag>)")
    if _RELEASE_TAG_RE.search(tag):
        return None
    if key in TRACED_TAG_ROWS and _TRACED_RELEASE_TAG_RE.search(tag):
        return None
    shape = "<name>-vX.Y.Z" + (" or <name>-vX.Y.Z-g<sha>" if key in TRACED_TAG_ROWS else "")
    return (f"images.{key} ({var}) is on {tag!r}, which is not a release tag ({shape}): "
            f"build it from the merged commit, push a release tag and pin it "
            f"(tools/images.sh bump --only {key} --tag <release tag>)")


def release_problems(catalog: dict[str, Any]) -> list[str]:
    """Why this catalog cannot ship as a release, or [] when it can.

    Lenient everywhere else, strict here: a release pins every image by an
    immutable tag AND its digest. Pending rows, release-candidate tags and
    tag-only refs are fine on a development branch while images are being
    filled in, and must all be gone before the merge into main. Tags are
    checked against an allowlist of release shapes (release_tag_problem).
    """
    images = catalog["images"]
    problems: list[str] = []
    for key, _note in pending_rows(catalog):
        problems.append(f"images.{key} is still pending (no digest): pin it with "
                        f"tools/images.sh bump --only {key} [--tag <release tag>]")
    channel_keys_seen: set[str] = set()
    for name, spec in sorted((catalog["consumers"].get("release_channels") or {}).items()):
        for var, key in spec["vars"].items():
            if key in channel_keys_seen:
                continue
            channel_keys_seen.add(key)
            problem = release_tag_problem(key, var, str(images[key]["tag"]))
            if problem:
                problems.append(problem)
        emitted = ROOT / spec["emits"]
        if emitted.is_file():
            for line in emitted.read_text(encoding="utf-8").splitlines():
                var, sep, ref = line.partition("=")
                if not sep or line.startswith("#") or not var.endswith("_IMAGE"):
                    continue
                if "@sha256:" not in ref:
                    problems.append(f"{spec['emits']}: {var}={ref} is not digest-pinned")
    for lock_path in sorted(catalog["consumers"].get("pack_locks") or {}):
        path = ROOT / lock_path
        try:
            required = json.loads(path.read_text(encoding="utf-8")).get("required_images") or {}
        except (OSError, json.JSONDecodeError) as exc:
            problems.append(f"{lock_path}: unreadable ({exc})")
            continue
        for role, ref in sorted(required.items()):
            if "@sha256:" not in str(ref):
                problems.append(f"{lock_path}: required_images.{role}={ref} is not digest-pinned")
    return problems


def release_mode(args: argparse.Namespace) -> bool:
    """--release, or a pull request into a release branch (GitHub Actions
    sets GITHUB_BASE_REF on pull_request events)."""
    if getattr(args, "release", False):
        return True
    return os.environ.get("GITHUB_BASE_REF", "").strip() in RELEASE_BASE_REFS


def cmd_verify(args: argparse.Namespace) -> int:
    run_selftest()  # regression guard: must pass before trusting the real catalog
    catalog = load_catalog()
    assert_invariants(catalog)
    lock_problems = pack_lock_drift(catalog)
    if lock_problems:
        for problem in lock_problems:
            print(f"DRIFT: {problem}", file=sys.stderr)
        print("regenerate the pack release lock, or fix consumers.pack_locks", file=sys.stderr)
        return 1
    # Lenient: a pin being filled in, or labels that disagree, is reported
    # and never fails the gate (see the module docstring).
    for key, note in pending_rows(catalog):
        print(f"WARNING: images.{key} is pending, pinned by tag only: {note}", file=sys.stderr)
    host_findings = host_pin_findings(catalog, remote=False)
    for level, message in host_findings:
        print(f"{level}: {message}", file=sys.stderr)
    artifacts = render_all(catalog)
    drifted = []
    for path, content in artifacts.items():
        current = path.read_text(encoding="utf-8") if path.is_file() else None
        if current != content:
            drifted.append(path)
    contract_findings = host_contract_findings(catalog)
    for level, message in contract_findings:
        if level == "NOTE":
            print(f"NOTE: {message}", file=sys.stderr)
    contract_drift = [m for level, m in contract_findings if level == "DRIFT"]
    if drifted or contract_drift:
        for path in drifted:
            print(f"DRIFT: {path.relative_to(ROOT)} does not match images/catalog.yaml", file=sys.stderr)
        for message in contract_drift:
            print(f"DRIFT: {message}", file=sys.stderr)
        print("run: tools/images.sh sync", file=sys.stderr)
        return 1
    names = sorted(str(p.relative_to(ROOT)) for p in artifacts)
    names += sorted(catalog["consumers"].get("pack_locks") or {})
    checked = ", ".join(names)
    if release_mode(args):
        # A host-pin disagreement warns on -next and fails a release. A NOTE
        # (the check could not run: image not in the local store) does not.
        problems = release_problems(catalog) + [
            f"host pin: {message}" for level, message in host_findings if level == "WARNING"]
        if problems:
            for problem in problems:
                print(f"RELEASE: {problem}", file=sys.stderr)
            print(f"verify --release: {len(problems)} problem(s); a release pins every image "
                  f"by an immutable tag and its digest, with one kit across host and authoring "
                  f"(docs/images.md, 'Releasing')",
                  file=sys.stderr)
            return 1
        print("verify --release: every release image is pinned by tag and digest")
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
"""


def run_selftest() -> None:
    _selftest_source_block()
    _selftest_pending_and_host_pin()

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
        "schema: mns.images.v1\n\nimages:\n"
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

    # 4. An owned row must not return to a mutable publishing channel.
    owned_fixture = {
        "schema": "mns.images.v1",
        "images": {
            "fixture": {
                "repo": "example/repo",
                "tag": "thing-v0.2.0-retag.2026-08-26",
                "digest": "sha256:" + "0" * 64,
                "channel": "moving",
                "published_by": "example-repo/tools/build.sh",
                "purpose": "selftest fixture, not a real image.",
            },
        },
        "consumers": {
            "product_env": {}, "image_sets": {}, "compose_env": {},
        },
    }
    try:
        _validate_catalog(owned_fixture)
    except CatalogError:
        pass
    else:
        raise AssertionError("selftest FAILED: a published_by row used channel moving")
    owned_fixture["images"]["fixture"]["channel"] = "pinned"
    _validate_catalog(owned_fixture)

    # 5. The mutable-reference guard scans tracked files, ignores untracked
    # scratch, and falls back to a filesystem walk when Git is unavailable.
    prefixes = owned_tag_prefixes(owned_fixture["images"])
    assert prefixes == {"example/repo:thing"}
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        tracked_path = root / "docker-compose-example.yml"
        tracked_path.write_text("image: example/repo:thing-latest\n", encoding="utf-8")
        subprocess.run(["git", "add", tracked_path.name], cwd=root, check=True)
        (root / "scratch.md").write_text(
            "image: example/repo:thing-latest\n", encoding="utf-8")
        hits = scan_for_mutable_refs(root, prefixes)
        assert [(path, line) for path, line, _ in hits] == [
            ("docker-compose-example.yml", 1)
        ], f"selftest FAILED: expected only the tracked mutable ref, got {hits}"
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "docker-compose-example.yml").write_text(
            "image: example/repo:thing-latest\n", encoding="utf-8")
        hits = scan_for_mutable_refs(root, prefixes)
        assert [(path, line) for path, line, _ in hits] == [
            ("docker-compose-example.yml", 1)
        ], f"selftest FAILED: filesystem fallback missed mutable ref: {hits}"

    # 6. An unparseable owned tag must fail loudly instead of silently making
    # the mutable-reference guard ineffective.
    try:
        owned_tag_prefixes({
            "fixture": {
                "repo": "example/repo", "tag": "thing-20260901",
                "published_by": "x/tools/build.sh",
            },
        })
    except CatalogError:
        pass
    else:
        raise AssertionError("selftest FAILED: unparseable owned tag did not raise")


def _selftest_source_block() -> None:
    """The optional `source:` block, which `status` uses to say when merged
    code has no image. Wrong shapes must be refused at load, not discovered as
    a traceback inside the weekly status run."""
    def catalog(**src):
        row = {"repo": "r/i", "tag": "img-v1.0.0-gee99b9d", "channel": "pinned",
               "digest": "sha256:" + "0" * 64, "purpose": "fixture"}
        if src:
            row["source"] = src
        return {"schema": "mns.images.v1", "images": {"fixture": row},
                "consumers": {"product_env": {}, "compose_env": {},
                              "image_sets": {}}}

    # the sha comes from the traced tag when the block does not state one
    assert _sha_from_tag("img-v1.0.0-gee99b9d") == "ee99b9d"
    assert _sha_from_tag("tevv-runtime-host-20260918.1") is None
    assert _sha_from_tag("something-latest") is None

    _validate_catalog(catalog())                                   # no block: fine
    _validate_catalog(catalog(repo="DinoHub/TEVV-Web-Dashboard"))   # sha from the tag
    _validate_catalog(catalog(repo="o/n", branch="release/v1.0.0", commit="a6f514c58"))

    for bad, why in (
        ({"repo": "no-slash"}, "repo without an owner"),
        ({"branch": "main"}, "no repo"),
        ({"repo": "o/n", "commit": "zzzzzzz"}, "non-hex commit"),
        ({"repo": "o/n", "branch": 7}, "non-string branch"),
    ):
        try:
            _validate_catalog(catalog(**bad))
        except CatalogError:
            continue
        raise AssertionError(f"selftest FAILED: _validate_catalog accepted {why}: {bad}")

    # a row whose tag carries no sha must say which commit it was built from,
    # or the check would silently do nothing for it
    doc = catalog(repo="o/n")
    doc["images"]["fixture"]["tag"] = "tevv-runtime-host-20260918.1"
    try:
        _validate_catalog(doc)
    except CatalogError:
        pass
    else:
        raise AssertionError(
            "selftest FAILED: a source: block on a tag with no -g<sha> and no "
            "commit: was accepted — the check would be a no-op for that row.")

    # a row with no source: block yields no finding, without any network call
    assert _source_ahead_row("fixture", catalog()["images"]["fixture"]) is None


def _selftest_pending_and_host_pin() -> None:
    """`pending:` rows and the host pin label checks (both lenient)."""
    zero, one, two = ("sha256:" + c * 64 for c in "012")

    def doc(**rows):
        base = {"repo": "r/i", "channel": "pinned", "purpose": "fixture"}
        return {"schema": "mns.images.v1",
                "images": {k: {**base, **v} for k, v in rows.items()},
                "consumers": {"product_env": {}, "compose_env": {}, "image_sets": {}}}

    # a pending pinned row validates with digest null; without pending it does not
    _validate_catalog(doc(a={"tag": "a-v1.0.0-rc", "digest": None, "pending": "phase 4"}))
    for bad, why in (
        ({"tag": "a-v1.0.0-rc", "digest": None}, "pinned row with no digest and no pending"),
        ({"tag": "a-v1.0.0-rc", "digest": zero, "pending": "x"}, "pending row with a digest"),
        ({"tag": "a-v1.0.0-rc", "digest": None, "pending": ""}, "empty pending note"),
        ({"tag": "a", "digest": zero, "pull": "no"}, "non-boolean pull"),
    ):
        try:
            _validate_catalog(doc(a=bad))
        except CatalogError:
            continue
        raise AssertionError(f"selftest FAILED: _validate_catalog accepted {why}")

    # bump fills a pending row and drops its (folded) note
    text = ("schema: mns.images.v1\n\nimages:\n  a:\n    repo: r/i\n    tag: a-rc\n"
            "    digest: null\n    channel: pinned\n    pending: >-\n      phase 4,\n"
            "      by the release owner\n    purpose: fixture\n\nconsumers:\n  product_env: {}\n")
    bumped = _bump_line_targeted(text, "a", "a-rc", one)
    row = yaml.safe_load(bumped)["images"]["a"]
    assert row["digest"] == one and "pending" not in row and row["purpose"] == "fixture", (
        f"selftest FAILED: bump did not fill the pending row cleanly: {row}")

    # host pin: agreement is silent; each disagreement warns; nothing raises
    catalog = doc(host={"tag": "h", "digest": zero}, kit={"tag": "k", "digest": one},
                  auth={"tag": "au", "digest": two})
    catalog["consumers"]["release_channels"] = {"v1": {
        "emits": "x.env", "vars": {"H": "host"},
        "host_pin": {"host": "host", "kit": "kit", "authoring": "auth"}}}
    _validate_catalog(catalog)

    def labels(host, auth):
        table = {image_ref(catalog["images"], "host"): host,
                 image_ref(catalog["images"], "auth"): auth}
        return lambda ref: (table.get(ref), "" if table.get(ref) is not None else "absent")

    good_host = {LABEL_HOST_KIT_IMAGE: f"r/i@{one}", LABEL_HOST_SHARED_SET: "tree1"}
    good_auth = {LABEL_AUTHORING_KIT_IMAGE: f"r/i:k@{one}", LABEL_AUTHORING_SHARED_SET: "tree1"}
    assert host_pin_findings(catalog, remote=False, labels_of=labels(good_host, good_auth)) == []

    def warnings(host, auth):
        return [m for level, m in host_pin_findings(catalog, remote=False,
                                                     labels_of=labels(host, auth))
                if level == "WARNING"]

    # host kit, authoring kit and shared set each disagree: three warnings
    found = warnings({**good_host, LABEL_HOST_KIT_IMAGE: two},
                     {LABEL_AUTHORING_KIT_IMAGE: zero, LABEL_AUTHORING_SHARED_SET: "tree2"})
    assert len(found) == 3, f"selftest FAILED: {found}"
    assert all("--strict" in m for m in found[1:]), f"selftest FAILED: fix text: {found}"
    # a dev plugin source and a waived host check each warn
    found = warnings(good_host, {**good_auth, LABEL_AUTHORING_PLUGINS_SOURCE: "/src/airsim",
                                 LABEL_AUTHORING_CHECK_WAIVED: "true"})
    assert len(found) == 2, f"selftest FAILED: {found}"
    assert warnings(good_host, {**good_auth, LABEL_AUTHORING_CHECK_WAIVED: "false"}) == []
    # an authoring image from before the kit labels warns that it predates them
    found = warnings(good_host, {LABEL_AUTHORING_HOST_IMAGE_OLD: f"r/i:h@{zero}",
                                 LABEL_AUTHORING_SHARED_SET: "tree1"})
    assert len(found) == 1 and "predates the kit labels" in found[0], f"selftest FAILED: {found}"
    # no images at all: NOTEs only, never a warning
    skipped = host_pin_findings(catalog, remote=False, labels_of=labels(None, None))
    assert [level for level, _ in skipped] == ["NOTE", "NOTE"], f"selftest FAILED: {skipped}"
    # authoring is checked against the catalog kit even when the host is not local
    found = warnings(None, {**good_auth, LABEL_AUTHORING_KIT_IMAGE: f"r/i@{two}"})
    assert len(found) == 1 and "built against kit" in found[0], f"selftest FAILED: {found}"

    # host contract copy: equal is silent, different is DRIFT, not local is a NOTE
    catalog["consumers"]["release_channels"]["v1"]["host_contract"] = "images/catalog.yaml"
    _validate_catalog(catalog)
    same = (ROOT / "images" / "catalog.yaml").read_bytes()
    assert host_contract_findings(catalog, read=lambda ref: (same, "")) == []
    found = host_contract_findings(catalog, read=lambda ref: (b"{}", ""))
    assert [level for level, _ in found] == ["DRIFT"], f"selftest FAILED: {found}"
    found = host_contract_findings(catalog, read=lambda ref: (None, "absent"))
    assert [level for level, _ in found] == ["NOTE"], f"selftest FAILED: {found}"
    no_host = copy.deepcopy(catalog)
    del no_host["consumers"]["release_channels"]["v1"]["host_pin"]
    try:
        _validate_catalog(no_host)
    except CatalogError:
        pass
    else:
        raise AssertionError("selftest FAILED: host_contract accepted without a host_pin.host")

    # the release guard's -rc detector
    for tag, rc in (("mns-stacks-v1.0.0-rc", True), ("mns-stacks-v1.0.0-rc.2", True),
                    ("x-v1.0.0-rc-g1234567", True), ("mns-stacks-v1.0.0", False),
                    ("tevv-web-dashboard-backend-v1.0.0-gee99b9d", False),
                    ("sim-real-eval-worker-latest", False)):
        assert bool(_RC_TAG_RE.search(tag)) is rc, f"selftest FAILED: -rc detection on {tag!r}"
    # ...and its allowlist of release tags
    for key, tag, ok in (("v1_stacks", "mns-stacks-v1.0.0", True),
                         ("v1_dashboard_backend", "tevv-web-dashboard-backend-v1.0.0-g3ffd351", True),
                         ("v1_stacks", "mns-stacks-v1.0.0-rc.services.28", False),
                         ("v1_dashboard_backend", "tevv-web-dashboard-backend-v1.0.0-live.22", False),
                         ("v1_runtime_host", "tevv-runtime-host-v1.0.0-zones.3", False),
                         ("v1_authoring", "mns-authoring-v1.0.0-rc.pkg.1", False),
                         ("v1_stacks", "tevv-jsonl-ingest-v0.4.5-gfc88f57", False),
                         ("v1_stacks", "mns-stacks-v1.0.0-m5b74d92", False),
                         ("v1_packs", "mns-packs-latest", False)):
        assert (release_tag_problem(key, "X", tag) is None) is ok, \
            f"selftest FAILED: release tag allowlist on {key}={tag!r}"


def cmd_selftest(_args: argparse.Namespace) -> int:
    run_selftest()
    print("selftest: ok (tag quoting + immutable owned-image guards + source: block"
          " + pending rows + host pin labels)")
    return 0


# --------------------------------------------------------------------------
# report / bump — Hub + imagetools resolution (the STALE / TAG_MOVED /
# NO_DIGEST / NO_TAGS_FOUND / OK vocabulary of the image-pin checks this
# replaced).
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


def _github_compare(repo: str, base: str, head: str) -> tuple[int | None, str]:
    """Commits on `head` that are not in `base`, via the GitHub compare API.

    Returns (ahead_by, "") or (None, reason). Shells out to `gh` rather than
    calling the API directly: six of the seven repositories in this product are
    private, and `gh` already holds the credentials a developer and CI both
    use. Anything that goes wrong — no gh, not logged in, no access to that
    repository, a network blip — comes back as a reason string, never an
    exception: a check that cannot run must not be mistaken for a check that
    passed, nor fail the command it is reporting inside.
    """
    try:
        proc = subprocess.run(
            ["gh", "api", f"repos/{repo}/compare/{base}...{head}",
             "--jq", ".status + \" \" + (.ahead_by|tostring)"],
            capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        return None, "gh not installed"
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"gh failed: {exc}"
    if proc.returncode != 0:
        err = (proc.stderr or "").strip().splitlines()
        detail = err[-1] if err else f"exit {proc.returncode}"
        return None, detail[:120]
    parts = proc.stdout.split()
    if len(parts) != 2 or not parts[1].isdigit():
        return None, f"unexpected compare output: {proc.stdout.strip()[:60]}"
    return int(parts[1]), ""


def _source_ahead_row(key: str, row: dict[str, Any]) -> tuple[str, str] | None:
    """One finding when the source branch has moved past the commit this row's
    image was built from, or None when it has not (or cannot be told).

    This is the gap nothing else covers. `report`/`bump` ask the registry
    whether the TAG still resolves where the catalog says — they cannot see
    that the code was merged and no image was ever built from it. A pinned row
    is OK by every other check for as long as nobody rebuilds, which is exactly
    how a merged fix reaches nobody.
    """
    src = row.get("source") or {}
    repo = src.get("repo")
    if not repo:
        return None
    built = src.get("commit") or _sha_from_tag(row["tag"])
    if not built:
        return None
    branch = src.get("branch") or "main"
    ahead, err = _github_compare(repo, built, branch)
    if ahead is None:
        return key, f"SOURCE_UNKNOWN — could not compare {repo}@{built[:9]} with {branch}: {err}"
    if ahead <= 0:
        return None
    plural = "" if ahead == 1 else "s"
    return key, (f"SOURCE_AHEAD — {repo} {branch} is {ahead} commit{plural} ahead of "
                 f"{built[:9]}, the commit this image was built from — rebuild the image "
                 f"and repin (bump will NOT fix this: the tag has not moved)")


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

    if row.get("pending"):
        # Tag named, digest not pinned yet. Resolving is the useful part: once
        # the rc is pushed this reports NO_DIGEST and `bump --only` fills it.
        # A tag that is not pushed yet is the expected state, not a failure.
        if resolver == "imagetools":
            live, err = _imagetools_digest(f"{repo}:{tag}")
        else:
            assert token is not None
            live, err = _hub_digest_for(repo, tag, token)
        if err or not live:
            return _row_result(key, row, status="PENDING",
                               detail=f"tag not resolvable yet ({err or 'no digest'})")
        return _row_result(key, row, status="NO_DIGEST", detail=live[:19], live_digest=live)

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
    if any(r["status"] == "PENDING" for r in rows):
        print("PENDING: the row names an rc tag whose digest is not pinned yet, and the "
              "tag does not resolve yet. Not an error; bump --only KEY pins it once "
              "the image is pushed.", file=sys.stderr)
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
    if new_digest:
        # A pending row is filled in: drop its `pending:` note, folded
        # continuation lines included, so the row reads as an ordinary pin.
        body = re.sub(r"(?m)^    pending: .*\n(?:      .*\n)*", "", body, count=1)

    new_block = header + body
    new_images_block = images_block[: match.start()] + new_block + images_block[match.end():]
    return head + new_images_block + tail


def cmd_bump(args: argparse.Namespace) -> int:
    if getattr(args, "tag", None) and not args.only:
        sys.exit("bump --tag NEWTAG retags one row: pass --only KEY")
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
        if getattr(args, "tag", None):
            if row["channel"] != "pinned":
                sys.exit(f"--tag retags channel: pinned rows only; {key} is {row['channel']}")
            live, err = (_imagetools_digest(f"{row['repo']}:{args.tag}")
                         if row.get("resolver", "hub") == "imagetools"
                         else _hub_digest_for(row["repo"], args.tag, token))
            if err or not live:
                print(f"skipped: {key} — {row['repo']}:{args.tag} does not resolve "
                      f"({err or 'no digest'})", file=sys.stderr)
                continue
            text = _bump_line_targeted(text, key, args.tag, live)
            print(f"retagged: {key} {row['tag']} -> {args.tag}@{live[:19]}…")
            changed += 1
            continue
        if row["channel"] == "pinned" and not row.get("pending"):
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
        if report_row["status"] == "PENDING":
            print(f"skipped: {key} is pending and {row['repo']}:{row['tag']} does not resolve "
                  f"yet ({report_row['detail']})", file=sys.stderr)
            continue
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
# PENDING is not in _ACTIONABLE: every pending row is already listed, with its
# note, in the `pending` group of `status`, whatever the registry says.


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
    stale_source: list[tuple[str, str]] = []  # code merged, image never rebuilt
    unknown_source: list[tuple[str, str]] = []  # the source check could not run
    followups: list[tuple[str, str]] = []   # someone must do something, later
    unpublished: list[tuple[str, str]] = []
    overrides: list[tuple[str, str]] = []
    pending = pending_rows(catalog)
    host_pin: list[tuple[str, str]] = []   # label disagreements (warn)
    host_pin_notes: list[tuple[str, str]] = []  # checks that could not run

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

    # 3b. source vs pin. The registry checks above answer "has this tag moved";
    # this one answers "has the CODE moved past the image", which nothing else
    # sees. A row opts in by naming where it is built from:
    #
    #   source:
    #     repo: DinoHub/TEVV-Web-Dashboard
    #     branch: main            # optional, defaults to main
    #     commit: ee99b9d         # optional when the tag ends -g<sha>
    #
    # Left off a row, nothing changes for it.
    if not args.offline:
        for key in sorted(images):
            finding = _source_ahead_row(key, images[key])
            if not finding:
                continue
            if finding[1].startswith("SOURCE_UNKNOWN"):
                unknown_source.append(finding)
            else:
                stale_source.append(finding)

    # 3c. one host pin: the kit and authoring labels against the host's.
    for level, message in host_pin_findings(catalog, remote=not args.offline):
        subject, _, note = message.partition(": ")
        (host_pin if level == "WARNING" else host_pin_notes).append((subject, note))

    # 4. ./.env overrides — never actionable (they are the intended escape
    # hatch), always worth stating, because a pin that does not take effect
    # looks exactly like a pin that does.
    for var, env_value, _ref in dotenv_overrides(catalog):
        overrides.append((var, env_value))

    total = len(broken) + len(pins) + len(stale_source) + len(followups) \
        + len(pending) + len(host_pin)
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
    if stale_source:
        print(f"\n  source ahead of the image ({len(stale_source)}) — merged code that no "
              f"image carries; rebuild and repin, `bump` does nothing here")
        _print_notes(stale_source, 4)
    if pending:
        print(f"\n  pending digests ({len(pending)}) — pinned by tag only; "
              f"tools/images.sh bump --only KEY once the image is pushed")
        _print_notes(pending, 4)
    if host_pin:
        print(f"\n  host pin ({len(host_pin)}) — kit/authoring labels disagree with the host")
        _print_notes(host_pin, 4)
    if followups:
        print(f"\n  follow-ups ({len(followups)}) — from images/catalog.yaml; "
              f"delete the entry when done")
        _print_notes(followups, 4)

    fyi_total = len(unpublished) + len(overrides) + len(unknown_source) + len(host_pin_notes)
    print(f"\nFYI ({fyi_total}) — known and deliberate, no action")
    if overrides:
        print(f"\n  overridden by ./.env ({len(overrides)}) — for these the catalog "
              f"pin is NOT what runs")
        _print_table(overrides, 4)
    if unpublished:
        print(f"\n  unpublished ({len(unpublished)})")
        _print_table(unpublished, 4)
    if unknown_source:
        print(f"\n  source not checked ({len(unknown_source)}) — needs `gh` logged in with "
              f"read access to the source repository")
        _print_notes(unknown_source, 4)
    if host_pin_notes:
        print(f"\n  host pin not checked ({len(host_pin_notes)})")
        _print_notes(host_pin_notes, 4)
    if suppressed:
        print(f"\n  ({suppressed} unpublished row(s) already named in a follow-up above)")
    if args.offline:
        print("\n  registry not checked (--offline)")

    print("\nNot covered here: tools/images.sh baked (needs docker), "
          "tools/images.sh drift (needs the mns-stacks image).")
    return 1 if total else 0


def cmd_refs(args: argparse.Namespace) -> int:
    catalog = load_catalog()
    assert_invariants(catalog)
    for ref in pullable_refs(catalog, all_catalog=args.all_catalog, development=args.development,
                             channel=args.channel):
        print(ref)
    return 0


def cmd_local_refs(args: argparse.Namespace) -> int:
    """`channel: local` images a release channel needs present in the Docker
    store; empty for a fully published channel."""
    catalog = load_catalog()
    assert_invariants(catalog)
    for ref in local_refs(catalog, channel=args.channel):
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
    # catalog key -> the variable the product passes it as: a release channel
    # var (MNS_STACKS_IMAGE, MNS_AUTHORING_IMAGE, ...) or a product_env var.
    # The image bakes <VAR>_DEFAULT for it.
    key_to_var = {}
    for group in catalog["consumers"]["product_env"].values():
        for var, key in group.items():
            key_to_var[key] = var
    for spec in (catalog["consumers"].get("release_channels") or {}).values():
        for var, key in spec["vars"].items():
            key_to_var.setdefault(key, var)
    for baked_key in bakes:
        var = key_to_var.get(baked_key)
        if not var:
            sys.exit(f"images.{args.key}.bakes references {baked_key!r}, which has "
                      "no release-channel or consumers.product_env var")
        print(f"{var}_DEFAULT\t{image_ref(images, baked_key)}")
    return 0


# --------------------------------------------------------------------------
# effective-image-set: the release channel's .env image overrides, applied to
# the image set generated stacks run
# --------------------------------------------------------------------------

def image_set_slots(catalog: dict[str, Any], image_set: str) -> dict[str, list[tuple[str, ...]]]:
    """var -> the image-set slots it overrides, e.g. MNS_RUNTIME_HOST_IMAGE ->
    [("simulators", "tevv_runtime_host")]. A release channel var binds a
    catalog key; the image set's slots name catalog keys too, so a var owns
    every slot holding its key. Only channels that select this image set count."""
    sets = catalog["consumers"].get("image_sets") or {}
    if image_set not in sets:
        raise CatalogError(f"consumers.image_sets.{image_set} is not declared; known: {', '.join(sets)}")
    by_key: dict[str, list[tuple[str, ...]]] = {}

    def walk(node: Any, path: tuple[str, ...]) -> None:
        if isinstance(node, dict):
            for name, child in node.items():
                walk(child, path + (str(name),))
        elif isinstance(node, str):
            by_key.setdefault(node, []).append(path)

    walk(sets[image_set].get("images") or {}, ())
    out: dict[str, list[tuple[str, ...]]] = {}
    for spec in (catalog["consumers"].get("release_channels") or {}).values():
        if spec.get("image_set") != image_set:
            continue
        for var, key in spec["vars"].items():
            if key in by_key:
                out[var] = by_key[key]
    return out


def _dotenv(path: Path) -> dict[str, str]:
    """KEY -> value from a dotenv file, last assignment winning, quotes stripped
    (the same reading as tools/load-images-env.sh's dotenv_value)."""
    env: dict[str, str] = {}
    if not path.is_file():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line)
        if m:
            value = m.group(2).strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            env[m.group(1)] = value
    return env


def effective_overrides(catalog: dict[str, Any], image_set: str, environ: dict[str, str],
                        dotenv: dict[str, str]) -> list[tuple[str, str, str, tuple[str, ...]]]:
    """(var, value, where, slot) for every channel image var the shell or ./.env
    sets to something other than its pin. The shell wins over ./.env, as in
    load-images-env.sh. A value equal to the pin in either form (tag@digest or
    the bare tag) is not an override: make exports the pins into the shell."""
    images = catalog["images"]
    channels = catalog["consumers"].get("release_channels") or {}
    var_key = {var: key for spec in channels.values() if spec.get("image_set") == image_set
               for var, key in spec["vars"].items()}
    out = []
    for var, slots in sorted(image_set_slots(catalog, image_set).items()):
        if environ.get(var):
            value, where = environ[var], "the environment"
        elif dotenv.get(var):
            value, where = dotenv[var], "./.env"
        else:
            continue
        key = var_key[var]
        if value in {image_ref(images, key), development_ref(images, key),
                     f"{images[key]['repo']}:{images[key]['tag']}"}:
            continue
        for slot in slots:
            out.append((var, value, where, slot))
    return out


def cmd_effective_image_set(args: argparse.Namespace) -> int:
    """Print the image-set file generated stacks should run: --in unchanged when
    no channel image var is overridden, else --out, a copy of --in with each
    overridden slot replaced (a NOTE per override on stderr). This is how
    `MNS_RUNTIME_HOST_IMAGE=...` in ./.env reaches make fly / make campaign /
    dashboard stacks, which take their images from the image-set file only."""
    catalog = load_catalog()
    image_set = args.image_set or os.environ.get("MNS_IMAGE_SET") or "v1"
    src = Path(args.src)
    overrides = effective_overrides(catalog, image_set, dict(os.environ),
                                    _dotenv(Path(args.dotenv)))
    if not overrides:
        print(src)
        return 0
    doc = yaml.safe_load(src.read_text(encoding="utf-8")) or {}
    try:
        node = doc["image_sets"][image_set]["images"]
    except (KeyError, TypeError):
        sys.exit(f"image set {image_set!r} is not in {src}")
    for var, value, where, slot in overrides:
        target = node
        for name in slot[:-1]:
            target = target.setdefault(name, {})
        old = target.get(slot[-1])
        target[slot[-1]] = value
        print(f"NOTE: {var} from {where} overrides the image set's {'.'.join(slot)} "
              f"({old}): generated stacks run {value}", file=sys.stderr)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    header = (f"# GENERATED by tools/images.py effective-image-set from {src},\n"
              "# with the image overrides the shell or ./.env sets (a NOTE names each).\n"
              "# Rewritten on every make fly / make campaign / make dashboard; do not edit.\n")
    out.write_text(header + yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    print(out)
    return 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="images.py")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("sync").set_defaults(fn=cmd_sync)
    p_verify = sub.add_parser("verify")
    p_verify.add_argument("--release", action="store_true",
                          help="also fail on pending rows, -rc tags and tag-only refs "
                               "(automatic when GITHUB_BASE_REF is release/v1.0.0 or main)")
    p_verify.set_defaults(fn=cmd_verify)
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
    p_bump.add_argument("--tag", metavar="NEWTAG",
                        help="with --only KEY on a channel: pinned row: move it to NEWTAG and "
                             "that tag's digest (the phase-6 rc -> release retag)")
    p_bump.set_defaults(fn=cmd_bump)

    p_refs = sub.add_parser("refs")
    p_refs.add_argument("--all-catalog", action="store_true")
    p_refs.add_argument("--development", action="store_true")
    p_refs.add_argument("--channel", default=DEFAULT_CHANNEL,
                        help=f"release channel whose active set to list (default: {DEFAULT_CHANNEL})")
    p_refs.set_defaults(fn=cmd_refs)

    p_local = sub.add_parser("local-refs")
    p_local.add_argument("--channel", default=DEFAULT_CHANNEL)
    p_local.set_defaults(fn=cmd_local_refs)

    p_rv = sub.add_parser("resolve-var")
    p_rv.add_argument("var")
    p_rv.set_defaults(fn=cmd_resolve_var)

    p_eis = sub.add_parser("effective-image-set",
                           help="apply the channel's .env/shell image overrides to an image-set file")
    p_eis.add_argument("--in", dest="src", required=True, help="the selected image-set file")
    p_eis.add_argument("--out", required=True, help="where the overridden copy goes")
    p_eis.add_argument("--image-set", default=None, help="image set name (default: $MNS_IMAGE_SET or v1)")
    p_eis.add_argument("--dotenv", default=str(DOTENV_PATH), help="the dotenv file (default: ./.env)")
    p_eis.set_defaults(fn=cmd_effective_image_set)

    p_bp = sub.add_parser("baked-pins")
    p_bp.add_argument("key")
    p_bp.set_defaults(fn=cmd_baked_pins)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
