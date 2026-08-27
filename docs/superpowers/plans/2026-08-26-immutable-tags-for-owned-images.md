# Immutable, Source-Naming Tags for Owned Images — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop publishing the three images we own to mutable `-latest` tags; publish them under immutable tags that name their source commit, and make the catalog able to detect staleness for them.

**Architecture:** A new `traced` channel in the image catalog holds tags shaped `<component>-v<product_version>-g<short-sha>`. `images.py` learns the channel, a `product_version` field, and how to find the newest traced sibling (by registry push time, since sha tags do not sort). The dashboard build script mints those tags from `git rev-parse` and refuses to publish a dirty tree or overwrite an existing tag. `airsim_tools`, which has no known source commit, gets a dated re-tag on `channel: pinned` instead. Guardrails make a regression to `-latest` fail the existing offline PR gate.

**Tech Stack:** Python 3 (stdlib + pyyaml) for `tools/images.py`; bash + `docker buildx imagetools` for the publishing scripts; Docker Hub v2 API for tag search.

**Spec:** `docs/superpowers/specs/2026-08-26-immutable-tags-for-owned-images-design.md`

## Global Constraints

- Two repos. `M-S-Simulation-Runtime-Stack` (called **MSRS**; catalog + tooling) and `MnS-Integration-Platform` (called **platform**; the dashboard service under `services/tevv-web-dashboard`). Never assume one can read the other's files at runtime: any cross-repo check must degrade to "skipped", never "failed".
- `product_version` is `0.2.0`. Already committed in platform `mns-product.yaml` (`93ede30`).
- Traced tag shape, exactly: `<component>-v<major>.<minor>.<patch>-g<short-sha>` with an optional `.<n>` rebuild suffix. Regex: `-v\d+\.\d+\.\d+-g[0-9a-f]{7,}(\.\d+)?$`
- `airsim_tools` tag, exactly: `airsim-tools-v0.2.0-retag.2026-08-26`, `channel: pinned`.
- MSRS has no pytest harness for `tools/`. The test harness is `run_selftest()` in `tools/images.py`, run by `tools/images.sh selftest` and by `verify` before anything else. **All Python tests in this plan are assertions added to `run_selftest()`.**
- The catalog is the only place image facts live (ADR-0002). Never hardcode an image name in a second place; derive it from the catalog.
- Every `tools/images.sh` subcommand must keep working offline. `verify`, `selftest` and `status --offline` touch no network.
- Do not delete or repoint existing `-latest` tags on the registry. We stop pushing them; they stay.
- Tags in YAML are always double-quoted (an unquoted `3.4` parses as a float — there is already a selftest for this).

---

### Task 1: `traced` channel with a validated tag shape

**Files:**
- Modify: `tools/images.py:81` (VALID_CHANNELS), `tools/images.py:109-183` (`_validate_catalog`), `tools/images.py:533-580` (`run_selftest`)
- Test: `tools/images.py` `run_selftest()` — same file, per Global Constraints

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `TRACED_TAG_RE: re.Pattern` (module level) matching a traced tag's suffix; `channel: traced` accepted by `_validate_catalog`. Tasks 2, 5 and 6 use both.

- [ ] **Step 1: Write the failing test**

Add to `run_selftest()`, after the existing check 3:

```python
    # 4. A traced row's tag must name its source: -v<x.y.z>-g<sha>. Anything
    #    else is the same silent dead end a review row off the -review.N line
    #    is — the family walk finds no siblings and reports NO_TAGS_FOUND,
    #    which reads as "already current".
    traced_fixture = """\
schema: mns.images.v1

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
                 "thing-v1.10.3-g0123456789abcdef"):
        _validate_catalog(yaml.safe_load(traced_fixture.format(tag=good, d="0" * 64)))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/M-S-Simulation-Runtime-Stack && tools/images.sh selftest`
Expected: FAIL — `images/catalog.yaml: images.fixture.channel 'traced' not in {...}` raised for the *first good* tag (the bad ones "pass" for the wrong reason, which is why the good cases are in the same test).

- [ ] **Step 3: Write minimal implementation**

`tools/images.py:81` — add the channel:

```python
VALID_CHANNELS = {"review", "traced", "moving", "upstream", "local", "unpublished", "pinned"}
```

Next to it, the shape:

```python
# A traced tag names its source: <component>-v<x.y.z>-g<short-sha>, with an
# optional .N rebuild suffix for republishing the same commit against a new
# base image. ADR-0001: "New tags name their source ... so a pin answers
# 'what is in this?' with `git show`". The sha is >= 7 hex chars because that
# is git's own short-sha floor; longer is fine and stays valid as repos grow.
TRACED_TAG_RE = re.compile(r"-v\d+\.\d+\.\d+-g[0-9a-f]{7,}(\.\d+)?$")
```

In `_validate_catalog`, directly after the existing `channel == "review"` check:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `tools/images.sh selftest`
Expected: PASS — `selftest: ok (...)`
Then: `tools/images.sh verify` → still `verify: ok (...)`, because no catalog row is `traced` yet.

- [ ] **Step 5: Commit**

```bash
cd ~/M-S-Simulation-Runtime-Stack
git add tools/images.py
git commit -m "feat(images): a traced channel whose tags name their source commit"
```

---

### Task 2: `product_version` in the catalog, and the version ceiling

**Files:**
- Modify: `tools/images.py` (`_validate_catalog`, `run_selftest`), `images/catalog.yaml` (new top-level field)

**Interfaces:**
- Consumes: `TRACED_TAG_RE` (Task 1).
- Produces: `catalog["product_version"] -> str`; helper `_semver(v: str) -> tuple[int, int, int]`; `traced_tag_version(tag: str) -> tuple[int, int, int] | None`. Task 6 uses both helpers.

- [ ] **Step 1: Write the failing test**

Add to `run_selftest()`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `tools/images.sh selftest`
Expected: FAIL — `AssertionError: selftest FAILED: accepted a traced tag versioned ahead of product_version`

- [ ] **Step 3: Write minimal implementation**

Module level in `tools/images.py`, next to `TRACED_TAG_RE`:

```python
_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def _semver(value: str) -> tuple[int, int, int] | None:
    m = _SEMVER_RE.match(value or "")
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def traced_tag_version(tag: str) -> tuple[int, int, int] | None:
    """The version a traced tag claims, or None if the tag is not traced."""
    m = re.search(r"-v(\d+\.\d+\.\d+)-g[0-9a-f]{7,}(\.\d+)?$", tag)
    return _semver(m.group(1)) if m else None
```

In `_validate_catalog`, before the per-row loop:

```python
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
```

Inside the per-row loop, after the traced shape check from Task 1:

```python
        claimed = traced_tag_version(row["tag"]) if row["channel"] == "traced" else None
        if claimed is not None and claimed > _semver(product_version):
            raise CatalogError(
                f"images.{key} tag {row['tag']!r} claims version "
                f"{'.'.join(str(n) for n in claimed)}, ahead of product_version "
                f"{product_version} — a tag from the future is a typo or a bad mint. "
                f"A tag BEHIND product_version is fine (it was published before the bump)."
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `tools/images.sh selftest`
Expected: PASS

- [ ] **Step 5: Add the field to the real catalog**

At the top of `images/catalog.yaml`, directly under `schema:`:

```yaml
# The version segment of every tag we publish. Mirrors
# MnS-Integration-Platform/mns-product.yaml's `version`; see ADR-0003 for when
# it moves (pre-1.0: MINOR on a contract change or new capability, PATCH on
# fixes). Nothing is re-tagged when it moves — a published tag keeps the
# version it was built under, which is the point of a tag that names its source.
product_version: "0.2.0"
```

- [ ] **Step 6: Verify the real catalog still passes**

Run: `tools/images.sh verify`
Expected: `verify: ok (...)`. If it reports DRIFT, run `tools/images.sh sync` and inspect the diff — the generated artifacts must NOT gain a product_version line; if they do, a renderer is emitting unknown top-level keys and that renderer needs the field excluded.

- [ ] **Step 7: Commit**

```bash
git add tools/images.py images/catalog.yaml
git commit -m "feat(images): product_version, and a traced tag may not outrun it"
```

---

### Task 3: cross-repo check that `product_version` matches `mns-product.yaml`

**Files:**
- Modify: `tools/images.py` (`assert_invariants` at `:414`, `run_selftest`)

**Interfaces:**
- Consumes: `_semver` (Task 2), `catalog["product_version"]`.
- Produces: `platform_product_version() -> str | None` — the platform repo's declared version, or None when that checkout is not reachable.

- [ ] **Step 1: Write the failing test**

Add to `run_selftest()`:

```python
    # 6. The cross-repo version check must DEGRADE, never fail, when the
    #    platform checkout is absent — MSRS CI clones this repo alone.
    assert platform_product_version(Path("/nonexistent-checkout")) is None, (
        "selftest FAILED: platform_product_version must return None for a missing "
        "checkout, not raise — CI has no sibling clone and must still validate."
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `tools/images.sh selftest`
Expected: FAIL — `NameError: name 'platform_product_version' is not defined`

- [ ] **Step 3: Write minimal implementation**

Module level in `tools/images.py`:

```python
# The sibling platform checkout, resolved the way product-images-env.sh
# resolves the pin file in the other direction: an explicit env var wins, then
# the layout this repo is actually checked out in. Absent is normal (CI clones
# this repo alone), so every caller treats None as "cannot check", never as a
# failure.
def platform_product_version(root: Path | None = None) -> str | None:
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
        except Exception:
            return None                        # unreadable is "cannot check", not "wrong"
        version = (data or {}).get("version")
        return version if isinstance(version, str) else None
    return None
```

In `assert_invariants`, as a new numbered check at the end:

```python
    # N. product_version must agree with the platform repo it mirrors. Skipped
    # silently when that checkout is not present — see platform_product_version.
    declared = catalog.get("product_version")
    platform = platform_product_version()
    if platform is not None and platform != declared:
        raise CatalogError(
            f"product_version {declared!r} does not match MnS-Integration-Platform's "
            f"mns-product.yaml version {platform!r}. These drifted once already (0.1.0 "
            f"vs v0.2.0 in every image tag); bump both in one PR."
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `tools/images.sh selftest` → PASS
Run: `tools/images.sh verify` → `verify: ok`. This machine HAS the platform checkout at `~/MnS-Integration-Platform` with `version: 0.2.0`, so the check runs for real and agrees.

- [ ] **Step 5: Prove the check actually fires**

```bash
cd ~/M-S-Simulation-Runtime-Stack
sed -i 's/^product_version: "0.2.0"/product_version: "0.3.0"/' images/catalog.yaml
tools/images.sh verify   # expect: does not match ... mns-product.yaml version '0.2.0'
git checkout images/catalog.yaml
```

- [ ] **Step 6: Commit**

```bash
git add tools/images.py
git commit -m "feat(images): assert product_version matches the platform repo when reachable"
```

---

### Task 4: `published_by`, and the rule that our own images may not float

**Files:**
- Modify: `tools/images.py` (`_validate_catalog`, `run_selftest`)

**Interfaces:**
- Consumes: nothing new.
- Produces: optional row field `published_by: str`; rows carrying it are constrained to `channel: traced` or `pinned`. Task 5 reads the field to derive owned image names.

- [ ] **Step 1: Write the failing test**

Add to `run_selftest()`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `tools/images.sh selftest`
Expected: FAIL — `AssertionError: selftest FAILED: a published_by row was allowed on channel moving`

- [ ] **Step 3: Write minimal implementation**

In `_validate_catalog`'s per-row loop:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `tools/images.sh selftest` → PASS
Run: `tools/images.sh verify` → `verify: ok` (no real row has `published_by` yet)

- [ ] **Step 5: Commit**

```bash
git add tools/images.py
git commit -m "feat(images): published_by rows may not sit on a mutable tag"
```

---

### Task 5: fail on a bare `-latest` reference to an image we own

**Files:**
- Modify: `tools/images.py` (`assert_invariants`, `run_selftest`)

**Interfaces:**
- Consumes: `published_by` (Task 4).
- Produces: `owned_tag_prefixes(images: dict) -> set[str]` and `scan_for_mutable_refs(root: Path, prefixes: set[str]) -> list[tuple[str, int, str]]` returning `(relative_path, line_number, line)`.

- [ ] **Step 1: Write the failing test**

Add to `run_selftest()`:

```python
    # 8. A bare -latest reference to an image we own, anywhere in this repo,
    #    is how the repointed compose fallbacks would quietly revert.
    import tempfile
    prefixes = owned_tag_prefixes({
        "fixture": {"repo": "example/repo", "tag": "thing-v0.2.0-g6e6ae15",
                    "channel": "traced", "published_by": "x/tools/build.sh",
                    "digest": None, "purpose": "p"},
        "other": {"repo": "example/repo", "tag": "unowned-latest",
                  "channel": "moving", "digest": None, "purpose": "p"},
    })
    assert prefixes == {"example/repo:thing"}, f"unexpected owned prefixes: {prefixes}"
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "compose.yml").write_text(
            "services:\n  a:\n    image: ${X:-example/repo:thing-latest}\n"
            "  b:\n    image: example/repo:unowned-latest\n", encoding="utf-8")
        hits = scan_for_mutable_refs(root, prefixes)
        assert [(p, n) for p, n, _ in hits] == [("compose.yml", 3)], (
            f"selftest FAILED: expected exactly the owned -latest hit, got {hits}"
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `tools/images.sh selftest`
Expected: FAIL — `NameError: name 'owned_tag_prefixes' is not defined`

- [ ] **Step 3: Write minimal implementation**

Module level in `tools/images.py`:

```python
# Files worth scanning for a resurrected mutable reference. Compose files and
# docs are where the bare-tag fallbacks live; scanning everything would drag in
# generated artifacts and this plan's own spec, which quote the old tags on
# purpose.
_MUTABLE_SCAN_GLOBS = ("*.yml", "*.yaml", "*.sh", "*.md", "Makefile")
_MUTABLE_SCAN_SKIP_DIRS = {".git", "graphify-out", "generated", "images",
                            "docs/superpowers", "docs/adr"}


def owned_tag_prefixes(images: dict[str, Any]) -> set[str]:
    """`repo:component` for every row we publish ourselves, where component is
    the tag with its version/suffix stripped. Derived from the catalog so an
    image name is never written down twice."""
    out: set[str] = set()
    for row in images.values():
        if not row.get("published_by"):
            continue
        component = re.sub(r"-(v\d+\.\d+\.\d+|latest|review)\b.*$", "", row["tag"])
        out.add(f"{row['repo']}:{component}")
    return out


def scan_for_mutable_refs(root: Path, prefixes: set[str]) -> list[tuple[str, int, str]]:
    hits: list[tuple[str, int, str]] = []
    if not prefixes:
        return hits
    for pattern in _MUTABLE_SCAN_GLOBS:
        for path in sorted(root.rglob(pattern)):
            rel = path.relative_to(root).as_posix()
            if any(rel == d or rel.startswith(f"{d}/") for d in _MUTABLE_SCAN_SKIP_DIRS):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for n, line in enumerate(text.splitlines(), start=1):
                for prefix in prefixes:
                    if f"{prefix}-latest" in line:
                        hits.append((rel, n, line.strip()))
    return hits
```

In `assert_invariants`, as the next numbered check:

```python
    # N. No bare -latest reference to an image we publish. The pins live in
    # product-images.env; a fallback naming the mutable tag is how a fresh
    # clone silently runs whatever was pushed last.
    hits = scan_for_mutable_refs(ROOT, owned_tag_prefixes(images))
    if hits:
        detail = "; ".join(f"{p}:{n}" for p, n, _ in hits[:5])
        raise CatalogError(
            f"{len(hits)} reference(s) to a mutable -latest tag for an image we publish: "
            f"{detail}. Point them at the pinned traced tag instead."
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `tools/images.sh selftest` → PASS
Run: `tools/images.sh verify` → `verify: ok` (no row has `published_by` yet, so the scan finds nothing)

- [ ] **Step 5: Commit**

```bash
git add tools/images.py
git commit -m "feat(images): verify fails on a bare -latest ref to an image we publish"
```

---

### Task 6: walk the traced family by push time, and report lag

**Files:**
- Modify: `tools/images.py:637-653` (`_hub_tags`), `tools/images.py:786-855` (`_report_row`), `tools/images.py:1016-1022` (`_ACTIONABLE`), `tools/images.py` (`cmd_status`), `run_selftest`

**Interfaces:**
- Consumes: `TRACED_TAG_RE`, `traced_tag_version`, `_semver` (Tasks 1-2).
- Produces: `_hub_tags` now returns `list[tuple[str, str]]` of `(name, last_updated)` — **every existing caller must be updated**; `newest_traced(tags, component_prefix)` returning the most recently pushed traced tag name or None.

- [ ] **Step 1: Write the failing test**

Add to `run_selftest()`:

```python
    # 9. Traced tags do not sort: g6e6ae15 vs gaaaaaaa says nothing about
    #    which came first. Newest means most recently pushed.
    tags = [
        ("dash-backend-v0.2.0-gaaaaaaa", "2026-08-20T10:00:00Z"),
        ("dash-backend-v0.2.0-g6e6ae15", "2026-08-26T09:00:00Z"),
        ("dash-backend-v0.1.0-gbbbbbbb", "2026-08-01T10:00:00Z"),
        ("dash-backend-latest", "2026-08-27T10:00:00Z"),   # not traced: ignored
        ("dash-frontend-v0.2.0-gccccccc", "2026-08-27T11:00:00Z"),  # other component
    ]
    got = newest_traced(tags, "dash-backend")
    assert got == "dash-backend-v0.2.0-g6e6ae15", f"selftest FAILED: newest_traced -> {got!r}"
    assert newest_traced(tags, "dash-nothing") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `tools/images.sh selftest`
Expected: FAIL — `NameError: name 'newest_traced' is not defined`

- [ ] **Step 3: Write minimal implementation**

Change `_hub_tags` to keep the timestamp (Docker Hub already returns it):

```python
def _hub_tags(repo: str, family: str, token: str) -> tuple[list[tuple[str, str]], str | None]:
    """(name, last_updated) per tag. last_updated is what orders a traced
    family — sha tags carry no ordering of their own."""
    out: list[tuple[str, str]] = []
    url = f"https://hub.docker.com/v2/repositories/{repo}/tags/?page_size=100&name={family}"
    try:
        while url:
            req = urllib.request.Request(url, headers={"Authorization": f"JWT {token}"})
            d = json.load(urllib.request.urlopen(req, timeout=30))
            out += [(t["name"], t.get("last_updated") or "") for t in d["results"]]
            url = d.get("next")
        return out, None
    except urllib.error.HTTPError as exc:
        return out, f"HTTP {exc.code}"
    except urllib.error.URLError as exc:
        return out, f"network: {exc.reason}"
    except Exception as exc:
        return out, f"error: {exc}"
```

Fix the existing review-channel caller (`_report_row`, ~`:821`) which now receives tuples:

```python
        candidates = [(_review_num(t), t) for t, _ in family_tags if _review_num(t) is not None]
```

Add the picker:

```python
def newest_traced(tags: list[tuple[str, str]], component_prefix: str) -> str | None:
    """Most recently pushed traced tag for one component. Ordering is by
    registry push time because a sha tag has no intrinsic order — this is the
    whole reason the traced family walk cannot reuse the review one."""
    candidates = [
        (updated, name) for name, updated in tags
        if name.startswith(f"{component_prefix}-") and TRACED_TAG_RE.search(name)
    ]
    return max(candidates)[1] if candidates else None
```

Add the traced branch in `_report_row`, immediately after the `channel == "review"` block:

```python
    if channel == "traced":
        component = re.sub(r"-v\d+\.\d+\.\d+-g[0-9a-f]{7,}(\.\d+)?$", "", tag)
        family_tags, tags_err = _hub_tags(repo, component, token)
        if tags_err and not family_tags:
            return _row_result(key, row, status="NO_TAGS_FOUND",
                               detail=f"tag search failed: {tags_err}", live_digest=live)
        latest = newest_traced(family_tags, component)
        if not latest:
            return _row_result(key, row, status="NO_TAGS_FOUND",
                               detail="no traced siblings found", live_digest=live)
        if not pinned_digest:
            return _row_result(key, row, status="NO_DIGEST", detail=live[:19], live_digest=live)
        if live != pinned_digest:
            # An immutable tag that moved means someone force-pushed over it.
            return _row_result(key, row, status="TAG_MOVED", detail=live[:19], live_digest=live)
        if latest != tag:
            latest_live, latest_err = _hub_digest_for(repo, latest, token)
            detail = latest_live[:19] if latest_live else (latest_err or "")
            return _row_result(key, row, status="STALE", detail=detail,
                               latest_tag=latest, live_digest=latest_live)
        return _row_result(key, row, status="OK", detail=live[:19], live_digest=live)
```

Widen the STALE hint at `:1017`, which currently names only review:

```python
    "STALE": "a newer tag was published — tools/images.sh bump",
```

In `cmd_status`, after the follow-up collection, add the lag FYI:

```python
    # Rows published before the current product_version. Not actionable — a
    # published tag keeps the version it was built under — but the lag should
    # be visible, because "every image still says v0.1.0" is how a bump that
    # nobody rebuilt against looks from the outside.
    behind: list[tuple[str, str]] = []
    pv = _semver(catalog["product_version"])
    for key in sorted(images):
        claimed = traced_tag_version(images[key]["tag"])
        if claimed is not None and claimed < pv:
            behind.append((key, f"published under v{'.'.join(str(n) for n in claimed)}, "
                                f"product_version is {catalog['product_version']}"))
```

Render `behind` in the FYI block alongside `overrides`, using the same two-column
formatting that block already uses, under the heading
`published before the current product version ({n})`.

- [ ] **Step 4: Run test to verify it passes**

Run: `tools/images.sh selftest` → PASS
Run: `tools/images.sh verify` → `verify: ok`
Run: `tools/images.sh status` → unchanged output (no traced rows yet), and **no traceback** — this is the check that the `_hub_tags` return-type change did not break the review-row caller. If `mns_authoring`'s row now reports `NO_TAGS_FOUND` where it used to report `OK`/`STALE`, the review caller was missed.

- [ ] **Step 5: Commit**

```bash
git add tools/images.py
git commit -m "feat(images): order the traced family by push time; report version lag"
```

---

### Task 7: ADR-0003

**Files:**
- Create: `docs/adr/0003-traced-tags-and-product-version.md`
- Modify: `docs/adr/README.md` (if it indexes ADRs — check first)

**Interfaces:** documentation only.

- [ ] **Step 1: Read the existing ADRs for house style**

Run: `head -40 docs/adr/0002-one-image-catalog.md ~/MnS-Integration-Platform/docs/adr/0001-image-versioning-and-digest-pinning.md`
Both use: title line, Status/Date bullets, `## Context` (observed failures, not hypotheses), `## Decision` (bolded imperatives), `## Consequences` (Better/Worse/Unchanged). Match it.

- [ ] **Step 2: Write the ADR**

Cover exactly these decisions, each with the evidence already gathered:

1. **`traced` is the channel for images we build.** Tag shape `<component>-v<x.y.z>-g<short-sha>`. This implements ADR-0001's "New tags name their source", which was decided and never carried out — ADR-0002's tooling standardised on `-review.N` instead. Evidence for the cost: `dashboard_*` and `airsim_tools` sat on `-latest`, where staleness is undetectable by construction; the local dashboard build of 2026-08-25 and the registry's 2026-08-22 `-latest` were indistinguishable from inside the repo.
2. **`product_version` is the one source for the version segment**, mirrored from `mns-product.yaml`. Evidence: that file said `0.1.0` while every image tag said `v0.2.0`, and `mns-authoring-v0.3.0-review.1`/`.2` exist on the registry referenced by nothing.
3. **The pre-1.0 version policy**, verbatim from the spec's table: MAJOR reserved until the product is declared stable; MINOR on an incompatible contract change or a new capability (ScenarioSpec schema fields, generated stack layout, ROS topic/service names, dashboard API surface); PATCH on fixes with no contract impact. A bump edits `mns-product.yaml` and `product_version` in one PR; nothing is re-tagged.
4. **Newest means most recently pushed** for traced rows, because sha tags do not sort.
5. **`review` and `traced` coexist deliberately.** `mns_authoring`, `mns_stack_generator`, `mns_blocks`, `mns_product_shell` stay on `-review.N`; converting them is separate work with different publishing repos. State it so the mix is a decision, not drift.
6. **Consequences — Worse:** two tag schemes to read; a traced tag is longer and uglier; the family walk needs Hub tag search, so an unauthenticated runner cannot do it; `product_version` must be bumped in two files in one PR or `verify` fails where the checkouts sit side by side.

ADR-0003 may quote the old `-latest` references in full: `docs/adr` is in Task 5's scan skip
list precisely because an ADR must be able to name the practice it replaces.

- [ ] **Step 3: Verify links resolve**

Run: `grep -o "docs/[a-z0-9/._-]*" docs/adr/0003-traced-tags-and-product-version.md | sort -u | while read -r p; do [ -e "$p" ] || echo "MISSING: $p"; done`
Expected: no output.

- [ ] **Step 4: Commit**

```bash
git add docs/adr/0003-traced-tags-and-product-version.md docs/adr/README.md
git commit -m "docs(adr): 0003 traced tags and the product version policy"
```

---

### Task 8: mint traced tags in the dashboard build script

**Files:**
- Create: `services/tevv-web-dashboard/tools/traced-tags.sh` (platform repo)
- Modify: `services/tevv-web-dashboard/tools/build-dashboard-images.sh`

**Interfaces:**
- Consumes: `mns-product.yaml`'s `version` (platform repo root).
- Produces, all sourced from `traced-tags.sh`:
  - `product_version()` → `0.2.0`
  - `traced_tag <component>` → `<component>-v0.2.0-g<short-sha>`
  - `tag_exists <repo:tag>` → exit 0 when the tag resolves on the registry
  - `newest_traced_tag <repo> <component>` → newest traced tag name (used by Task 9)

- [ ] **Step 1: Write the helper**

`services/tevv-web-dashboard/tools/traced-tags.sh`:

```bash
#!/usr/bin/env bash
# Traced tag helpers, shared by build-dashboard-images.sh and
# pin-dashboard-images.sh so "what is the newest traced tag" has exactly one
# implementation. A traced tag names its source commit — see ADR-0003 in
# M-S-Simulation-Runtime-Stack.
#
# Sourced, not executed.

# The platform repo root: this file lives at <root>/services/tevv-web-dashboard/tools/.
_tt_platform_root() { (cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd); }

product_version() {
  local f; f="$(_tt_platform_root)/mns-product.yaml"
  [[ -f "$f" ]] || { echo "no mns-product.yaml at $f" >&2; return 1; }
  local v; v=$(sed -n 's/^version:[[:space:]]*//p' "$f" | head -1 | tr -d '"'"'"' ')
  [[ "$v" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "mns-product.yaml version is not semver: '$v'" >&2; return 1; }
  printf '%s\n' "$v"
}

# <component>-v<product_version>-g<short-sha>. The sha is this repo's HEAD, so
# the tag answers "what is in this?" with `git show`.
traced_tag() {
  local component="${1:?component}" version sha
  version=$(product_version) || return 1
  sha=$(git -C "$(_tt_platform_root)" rev-parse --short HEAD) || return 1
  printf '%s-v%s-g%s\n' "$component" "$version" "$sha"
}

tag_exists() {
  docker buildx imagetools inspect "${1:?ref}" >/dev/null 2>&1
}

# Most recently pushed traced tag for a component. Ordering is by push time:
# sha tags carry no order of their own.
newest_traced_tag() {
  local repo="${1:?repo}" component="${2:?component}"
  docker buildx imagetools inspect "$repo:$component" >/dev/null 2>&1 || true
  python3 - "$repo" "$component" <<'PY'
import json, subprocess, sys, re
repo, component = sys.argv[1], sys.argv[2]
out = subprocess.run(["docker", "run", "--rm", "curlimages/curl:8.10.1", "-s",
                      f"https://hub.docker.com/v2/repositories/{repo}/tags/?page_size=100&name={component}"],
                     capture_output=True, text=True)
try:
    data = json.loads(out.stdout)
except Exception:
    sys.exit(1)
pat = re.compile(r"-v\d+\.\d+\.\d+-g[0-9a-f]{7,}(\.\d+)?$")
cands = [(t.get("last_updated") or "", t["name"]) for t in data.get("results", [])
         if t["name"].startswith(component + "-") and pat.search(t["name"])]
print(max(cands)[1] if cands else "")
PY
}
```

**Note on `newest_traced_tag`:** the Hub tag-search API needs a JWT for a private repo. If the anonymous call above returns nothing for `dhdevspace/auto_mns`, replace the body with a call to MSRS's `tools/images.py` (`python3 "$MSRS_ROOT/tools/images.py" report --only <key>` already authenticates via `~/.docker/config.json`) rather than duplicating token handling here. Decide this by running Step 2 before writing the callers.

- [ ] **Step 2: Verify the helper against the real registry**

```bash
cd ~/MnS-Integration-Platform/services/tevv-web-dashboard
source tools/traced-tags.sh
product_version                                     # expect: 0.2.0
traced_tag tevv-web-dashboard-backend               # expect: tevv-web-dashboard-backend-v0.2.0-g<sha>
tag_exists dhdevspace/auto_mns:tevv-web-dashboard-backend-latest && echo EXISTS   # expect: EXISTS
newest_traced_tag dhdevspace/auto_mns tevv-web-dashboard-backend                  # expect: empty (none published yet)
```

If `newest_traced_tag` errors rather than printing an empty line, apply the fallback from Step 1's note now.

- [ ] **Step 3: Wire minting into the build script**

In `build-dashboard-images.sh`, after the existing arg parsing, add `--print-next-tag` and `--rebuild-suffix N` to the `case`, source the helper next to the existing `source "$ROOT/tools/product-images-env.sh"`, and replace the `-latest`-implied build target:

```bash
source "$ROOT/tools/traced-tags.sh"

component_of() {                       # service name -> image component name
  case "$1" in
    backend)  printf 'tevv-web-dashboard-backend\n' ;;
    frontend) printf 'tevv-web-dashboard-frontend\n' ;;
    *) echo "unknown service: $1" >&2; return 1 ;;
  esac
}

# A sha tag on a dirty tree is a false claim about its own contents: `git show`
# would not reproduce the image. Refuse rather than publish a lie.
assert_clean_tree() {
  local dirty
  dirty=$(git -C "$ROOT" status --porcelain -- backend frontend)
  if [[ -n "$dirty" ]]; then
    echo "refusing to publish: backend/ or frontend/ has uncommitted changes" >&2
    printf '%s\n' "$dirty" | sed 's/^/    /' >&2
    return 1
  fi
}

target_tag_for() {                     # service -> full repo:tag to build/push
  local component ref
  component=$(component_of "$1") || return 1
  ref="${REGISTRY:-dhdevspace/auto_mns}:$(traced_tag "$component")"
  [[ -n "${REBUILD_SUFFIX:-}" ]] && ref="${ref}.${REBUILD_SUFFIX}"
  printf '%s\n' "$ref"
}
```

Before `docker compose build`, export the targets so compose builds into them:

```bash
for s in "${services[@]}"; do
  case "$s" in
    backend)  export TEVV_DASHBOARD_BACKEND_IMAGE="$(target_tag_for backend)" ;;
    frontend) export TEVV_DASHBOARD_FRONTEND_IMAGE="$(target_tag_for frontend)" ;;
  esac
done
```

`--print-next-tag` prints each service's `target_tag_for` and exits 0 before any docker call.

- [ ] **Step 4: Verify the dry run**

```bash
./tools/build-dashboard-images.sh --print-next-tag
```
Expected: two lines, `dhdevspace/auto_mns:tevv-web-dashboard-backend-v0.2.0-g<sha>` and the frontend equivalent, where `<sha>` equals `git rev-parse --short HEAD`. No build, no network write.

- [ ] **Step 5: Add the push guards**

Replace the `if [[ $push -eq 1 ]]` block's body:

```bash
if [[ $push -eq 1 ]]; then
  assert_clean_tree || exit 1
  branch=$(git -C "$ROOT" rev-parse --abbrev-ref HEAD)
  if [[ "$branch" != "main" ]]; then
    echo "refusing to publish from '$branch': a traced tag must point at a commit on main," >&2
    echo "  or the sha it names may never exist there. Merge first." >&2
    exit 1
  fi
  # An immutable tag is only immutable if nothing overwrites it.
  for s in "${services[@]}"; do
    ref=$(target_tag_for "$s")
    if tag_exists "$ref"; then
      echo "refusing to publish: $ref already exists." >&2
      echo "  Rebuilding this same commit deliberately? re-run with --rebuild-suffix 2" >&2
      exit 1
    fi
  done
  docker compose push "${services[@]}"
  echo
  echo "pushed. Now re-pin:"
  echo "  ./tools/pin-dashboard-images.sh                          # this repo's .env"
  echo "  tools/images.sh bump --channel traced && tools/images.sh sync   # in the runtime stack"
fi
```

Note the branch check hardened from a warning to a refusal — the spec requires a traced tag to trace to a mainline commit.

- [ ] **Step 6: Verify the guards fire**

```bash
# dirty guard
touch backend/.guard-probe && ./tools/build-dashboard-images.sh --push backend   # expect: refusing to publish (uncommitted)
rm backend/.guard-probe
# branch guard (current branch is not main)
./tools/build-dashboard-images.sh --push backend                                  # expect: refusing to publish from '<branch>'
```
Both must exit non-zero **before** any `docker compose build` output appears.

- [ ] **Step 7: Commit**

```bash
cd ~/MnS-Integration-Platform
git add services/tevv-web-dashboard/tools/traced-tags.sh services/tevv-web-dashboard/tools/build-dashboard-images.sh
git commit -m "feat(dashboard): publish traced tags; refuse dirty trees, overwrites, non-main"
```

---

### Task 9: `.env` pinning follows the traced line

**Files:**
- Modify: `services/tevv-web-dashboard/tools/pin-dashboard-images.sh`
- Modify: `services/tevv-web-dashboard/docs/pinning-dashboard-images.md`

**Interfaces:**
- Consumes: `newest_traced_tag`, `product_version` from `traced-tags.sh` (Task 8).
- Produces: no new interface; `--check` gains a `STALE` verdict.

- [ ] **Step 1: Replace the fixed tag defaults**

```bash
source "$(dirname "$0")/traced-tags.sh"
REGISTRY="${REGISTRY:-dhdevspace/auto_mns}"
BACKEND_TAG="${BACKEND_TAG:-$REGISTRY:$(newest_traced_tag "$REGISTRY" tevv-web-dashboard-backend)}"
FRONTEND_TAG="${FRONTEND_TAG:-$REGISTRY:$(newest_traced_tag "$REGISTRY" tevv-web-dashboard-frontend)}"
for v in "$BACKEND_TAG" "$FRONTEND_TAG"; do
  [[ "$v" == *: ]] && { echo "no traced tag published yet for ${v%:} — run build-dashboard-images.sh --push first" >&2; exit 1; }
done
```

- [ ] **Step 2: Replace the TAG_MOVED branch in `--check`**

An immutable tag cannot move, so the question becomes whether a newer one exists:

```bash
    elif [[ "$have" != "$want" ]]; then
      # The tag is immutable, so this is never "the tag moved" — it is "you
      # pinned an older traced tag than the newest published one".
      echo "STALE     $key"
      echo "    pinned: $have"
      echo "    newest: $want"
      status=1
```

- [ ] **Step 3: Verify**

```bash
cd ~/MnS-Integration-Platform/services/tevv-web-dashboard
./tools/pin-dashboard-images.sh --print
```
Expected before Task 11 publishes anything: exits 1 with `no traced tag published yet for …`. That is correct behaviour, not a failure of this task — re-run it after Task 11 and expect two `…@sha256:…` lines.

- [ ] **Step 4: Update the doc**

In `docs/pinning-dashboard-images.md`, replace both `-latest` examples (lines ~20 and ~36) with a traced tag, and add a sentence: the tag names the commit it was built from, so `git show <sha>` answers what is in it; `--check` reports `STALE` when a newer traced tag exists.

- [ ] **Step 5: Commit**

```bash
git add services/tevv-web-dashboard/tools/pin-dashboard-images.sh services/tevv-web-dashboard/docs/pinning-dashboard-images.md
git commit -m "feat(dashboard): pin against the traced line; --check reports STALE"
```

---

### Task 10: re-tag `airsim_tools` and flip its row

**Files:**
- Modify: `images/catalog.yaml` (MSRS), and whatever `tools/images.sh sync` regenerates

**Interfaces:**
- Consumes: `published_by` rule (Task 4).
- Produces: `AIRSIM_TOOLS_IMAGE` in `product-images.env` now names an immutable tag.

- [ ] **Step 1: Copy the digest to an immutable name**

```bash
docker buildx imagetools create \
  -t dhdevspace/auto_mns:airsim-tools-v0.2.0-retag.2026-08-26 \
  dhdevspace/auto_mns@sha256:c6a3740b7b6e335b3cd265414f2a2e71b46e9f7578dd0e5390d039f8e30d2feb
```

- [ ] **Step 2: Verify it is the same image**

```bash
docker buildx imagetools inspect dhdevspace/auto_mns:airsim-tools-v0.2.0-retag.2026-08-26 \
  | awk '/^Digest:/ {print $2}'
```
Expected: `sha256:c6a3740b7b6e335b3cd265414f2a2e71b46e9f7578dd0e5390d039f8e30d2feb` — byte-identical, so nothing about the running stack changes.

- [ ] **Step 3: Update the row**

```yaml
  airsim_tools:
    repo: dhdevspace/auto_mns
    tag: "airsim-tools-v0.2.0-retag.2026-08-26"
    digest: sha256:c6a3740b7b6e335b3cd265414f2a2e71b46e9f7578dd0e5390d039f8e30d2feb
    channel: pinned
    published_by: "(re-tag only — no known build source)"
    # Not traced: this tag is a copy of the digest that was published as
    # airsim-tools-latest, and nobody knows which commit produced those bits.
    # rpc-clients/python/tools/Dockerfile in MnS-Integration-Platform may or may
    # not reproduce them. A -g<sha> tag would be a false claim, so this is a
    # dated re-tag on channel pinned instead: immutable, and honest about being
    # a copy rather than a build.
    purpose: ros2-tools image used by docker-compose-tools.yml.
    follow_up: >-
      airsim_tools has no build source: its tag is a re-tag of an image of
      unknown provenance. Give it a build script in MnS-Integration-Platform and
      republish on channel traced, then delete this note.
```

- [ ] **Step 4: Repoint airsim-tools' own consumers IN THIS TASK, before regenerating**

The bare-`-latest` guard runs inside `cmd_sync`, not only `verify`. The moment this row
carries `published_by`, any surviving `dhdevspace/auto_mns:airsim-tools-latest` reference
makes `sync` exit 1 — so `sync` cannot regenerate `product-images.env` until these are
repointed. Do it here, not in Task 12:

- `docker-compose-tools.yml:23`: `image: ${AIRSIM_TOOLS_IMAGE:-dhdevspace/auto_mns:airsim-tools-v0.2.0-retag.2026-08-26}`
- `tools.sh:15`: same tag in the comment.

- [ ] **Step 5: Regenerate and verify**

```bash
cd ~/M-S-Simulation-Runtime-Stack
tools/images.sh sync
git diff product-images.env      # expect only AIRSIM_TOOLS_IMAGE's tag changing; digest identical
tools/images.sh verify           # expect: verify: ok
```

- [ ] **Step 6: Smoke-test the consumer**

```bash
grep AIRSIM_TOOLS_IMAGE product-images.env
docker pull "$(grep '^AIRSIM_TOOLS_IMAGE=' product-images.env | cut -d= -f2-)"
```
Expected: pulls, and reports `Image is up to date` — it is the same digest already on disk.

- [ ] **Step 7: Commit**

```bash
git add images/catalog.yaml product-images.env images/*.generated.* docker-compose-tools.yml tools.sh
git commit -m "feat(images): airsim_tools on an immutable dated re-tag, off -latest"
```

---

### Task 11: publish the dashboard images and flip both rows

**Blocked on:** `feat/spec-file-browser-metrics-tabs-grafana-health` being merged to `main` in the platform repo. The Task 8 branch guard enforces this; do not work around it.

**Files:**
- Modify: `images/catalog.yaml` (MSRS) + regenerated artifacts

**Interfaces:**
- Consumes: Task 8's build script, Task 6's traced family walk.
- Produces: `DASHBOARD_BACKEND_IMAGE` / `DASHBOARD_FRONTEND_IMAGE` on traced tags.

- [ ] **Step 1: Confirm the merge landed — IN THE DASHBOARD REPO**

`services/tevv-web-dashboard` is its own git checkout, not a directory of the platform repo.
The branch to merge, the `main` to merge it into, and the sha the build script stamps into the
tag all belong to THAT repo. The platform repo's main is irrelevant here.

```bash
cd ~/MnS-Integration-Platform/services/tevv-web-dashboard
git checkout main && git pull
git log --oneline -3
```
Expected: the dashboard work (through `5f07450`) is present on `main`. If not, stop — this task
cannot start, and the build script's branch guard will refuse anyway.

- [ ] **Step 1b: Repoint the dashboard's own `-latest` references first**

Same reason as Task 10 Step 4: once these rows carry `published_by`, a surviving bare reference
makes `sync` exit 1 — and here it would fail *after* `bump` has already rewritten the catalog,
the worst intermediate state to be in. Repoint before flipping the rows:

- `tools/compose_retry.sh:9` (tooling repo): reword the quoted docker error so it does not carry
  a full `repo:tag` — use `dhdevspace/auto_mns:tevv-web-dashboard-backend-<tag>`.
- `docs/autonomy-developer-test-plan.md:45` (tooling repo): `docker pull` the traced frontend tag
  you are about to publish.
- `services/tevv-web-dashboard/docker-compose.yml:6,124` and
  `services/tevv-web-dashboard/docs/pinning-dashboard-images.md` (dashboard repo): the traced tags.

- [ ] **Step 2: Dry-run the tags**

```bash
cd services/tevv-web-dashboard && ./tools/build-dashboard-images.sh --print-next-tag
```
Record both tags; they must contain `main`'s short sha.

- [ ] **Step 3: Build and push**

```bash
./tools/build-dashboard-images.sh --push
```
Expected: pins resolved and echoed, baked-ARG verification prints `ok MNS_AUTHORING_IMAGE_DEFAULT` and `ok MNS_STACK_GENERATOR_IMAGE_DEFAULT`, then a push of both traced tags. **`-latest` must not appear in the push output.**

- [ ] **Step 4: Flip both rows**

In `images/catalog.yaml`, for `dashboard_backend` and `dashboard_frontend`: set `tag` to the pushed traced tag, `channel: traced`, add `published_by: MnS-Integration-Platform/services/tevv-web-dashboard/tools/build-dashboard-images.sh`, and delete the block comment explaining the `-latest` publishing model. Leave `digest` alone; the next step fills it.

- [ ] **Step 5: Pin and regenerate**

```bash
cd ~/M-S-Simulation-Runtime-Stack
tools/images.sh bump --channel traced
tools/images.sh sync
tools/images.sh verify        # expect: verify: ok
```

- [ ] **Step 6: Observe STALE on purpose**

This is the capability the whole change buys; confirm it works rather than assuming.

```bash
cd ~/MnS-Integration-Platform/services/tevv-web-dashboard
./tools/build-dashboard-images.sh --push --rebuild-suffix 2 backend   # a second traced tag
cd ~/M-S-Simulation-Runtime-Stack
tools/images.sh status                                                # do NOT bump yet
```
Expected: `dashboard_backend` reports `STALE — a newer tag was published — tools/images.sh bump`, naming the `.2` tag. Before this change the same situation reported `OK`, which is precisely the blindness being removed.

```bash
tools/images.sh bump --channel traced && tools/images.sh sync
tools/images.sh status                                                # STALE is gone
```

- [ ] **Step 7: Run the stack**

```bash
make dashboard
curl -sf localhost:8001/health && echo OK
make dashboard-down
```

- [ ] **Step 8: Commit**

```bash
git add images/catalog.yaml product-images.env images/*.generated.*
git commit -m "feat(images): dashboard images on traced tags, off -latest"
```

---

### Task 12: repoint every remaining bare `-latest` reference

**Files:**
- Modify (MSRS): `docker-compose-tools.yml:23`, `tools.sh:15`, `docs/autonomy-developer-test-plan.md:45`, `tools/compose_retry.sh:9`
- Modify (platform): `services/tevv-web-dashboard/docker-compose.yml:6,124`

**Interfaces:**
- Consumes: the tags published in Tasks 10-11, and Task 5's scan (which now has `published_by` rows to derive prefixes from, so it starts finding things).

- [ ] **Step 1: Let the guard tell you what is left**

```bash
cd ~/M-S-Simulation-Runtime-Stack && tools/images.sh verify
```
Expected: FAIL, listing `docker-compose-tools.yml:23`, `tools.sh:15`, `docs/autonomy-developer-test-plan.md:45`. This is the guard from Task 5 doing its job now that owned rows exist — it is the reason that task landed before the flips.

- [ ] **Step 2: Repoint them**

- `docker-compose-tools.yml:23`: `image: ${AIRSIM_TOOLS_IMAGE:-dhdevspace/auto_mns:airsim-tools-v0.2.0-retag.2026-08-26}`
- `tools.sh:15`: same tag in the comment.
- `docs/autonomy-developer-test-plan.md:45`: `docker pull` the traced frontend tag published in Task 11.
- `tools/compose_retry.sh:9`: this line quotes a real docker error message, so substituting a
  traced tag would make the quote fictional. Reword instead — replace the full reference with
  `dhdevspace/auto_mns:tevv-web-dashboard-backend-<tag>` so the comment stays honest and the
  guard stops matching it.

- [ ] **Step 3: Verify the guard clears**

```bash
tools/images.sh verify        # expect: verify: ok
```

- [ ] **Step 4: Repoint the platform repo's compose fallbacks**

`services/tevv-web-dashboard/docker-compose.yml` lines 6 and 124: replace the `-latest` fallbacks with the traced tags from Task 11.

- [ ] **Step 5: Add the platform-side equivalent guard**

`images.py` cannot see the platform repo, so the check lives in the build script. In `build-dashboard-images.sh`, before pushing:

```bash
# The MSRS catalog guard cannot reach this repo, so the same rule is enforced
# here: a fallback naming a mutable tag is how a fresh clone silently runs
# whatever was pushed last.
if grep -n 'dhdevspace/auto_mns:tevv-web-dashboard-[a-z]*-latest' docker-compose.yml; then
  echo "refusing to publish: docker-compose.yml still falls back to a -latest tag (above)" >&2
  exit 1
fi
```

- [ ] **Step 6: Verify a fresh clone still resolves**

```bash
cd ~/MnS-Integration-Platform/services/tevv-web-dashboard
env -u TEVV_DASHBOARD_BACKEND_IMAGE -u TEVV_DASHBOARD_FRONTEND_IMAGE \
  docker compose config --images | grep dhdevspace
```
Expected: the traced tags, and both must pull: `docker pull <each>`.

- [ ] **Step 7: Commit both repos**

```bash
cd ~/M-S-Simulation-Runtime-Stack
git add docker-compose-tools.yml tools.sh docs/autonomy-developer-test-plan.md
git commit -m "chore(images): point the last bare -latest fallbacks at immutable tags"

cd ~/MnS-Integration-Platform
git add services/tevv-web-dashboard/docker-compose.yml services/tevv-web-dashboard/tools/build-dashboard-images.sh
git commit -m "chore(dashboard): compose falls back to a traced tag, and publishing enforces it"
```

---

## Final verification

Run all of it, in order, and read the output rather than the exit codes alone:

```bash
cd ~/M-S-Simulation-Runtime-Stack
tools/images.sh selftest    # every new invariant, offline
tools/images.sh verify      # catalog + artifacts + cross-repo version + no -latest refs
tools/images.sh status      # three rows OK; none reporting TAG_MOVED
make dashboard && curl -sf localhost:8001/health && echo OK && make dashboard-down
make tools                  # ros2-tools resolves and runs
```

Then confirm the thing this was all for: `grep -c latest product-images.env` reports 0 for the three owned images, and `tools/images.sh status` can now say `STALE` about them — which, before this change, it structurally could not.
