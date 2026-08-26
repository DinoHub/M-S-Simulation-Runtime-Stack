# Immutable, source-naming tags for the images we publish ourselves

Date: 2026-08-26
Status: approved, not yet implemented
Repos touched: `M-S-Simulation-Runtime-Stack`, `MnS-Integration-Platform` (dashboard service)

## Why

ADR-0001 removed tag mutability from the release path by pinning digests, and ADR-0002
made `images/catalog.yaml` the one place those pins live. Three rows in that catalog
still publish to a mutable tag:

| key | tag | channel |
|---|---|---|
| `dashboard_backend` | `tevv-web-dashboard-backend-latest` | `moving` |
| `dashboard_frontend` | `tevv-web-dashboard-frontend-latest` | `moving` |
| `airsim_tools` | `airsim-tools-latest` | `moving` |

All three carry a digest, so a *release* is reproducible. What is not reproducible is
everything around it. A `moving` row cannot be checked for staleness: there is no tag
family to walk, so `_report_row` can only ask "does the pinned digest still resolve",
and the answer stays `OK` while the tag drifts arbitrarily far ahead. The only signal a
rebuild happened is `TAG_MOVED`, which appears *after* someone re-pushed over the name
and says nothing about what changed. Anyone pulling the bare tag — the fallback in two
compose files, a `docker pull` in the test plan — gets whatever was pushed last, with
nothing recording which build that was.

Today's state makes the cost concrete: the dashboard's local images were rebuilt
2026-08-25, the registry's `-latest` still resolves to the 2026-08-22 build the catalog
pins, and nothing in either repo distinguishes those two facts.

### The version question, answered

Nothing currently determines the `v0.2.0` in an image tag. It is a literal string
copied into each new tag. `mns-product.yaml` says `version: 0.1.0`; the tags say
`v0.2.0`; nothing reconciles them. ADR-0001 diagnosed this in its own words — *"`v0.2.0`
was never true. The base version did not move across twenty revisions of real behaviour
change, so the semver part conveyed nothing and the prerelease suffix carried all the
meaning"* — and decided the fix: **"New tags name their source. Publish as
`<component>-<version>-<short-sha>` rather than an opaque counter."**

That decision was never implemented; ADR-0002's tooling standardised on `-review.N`
instead. Evidence the gap is live: `mns-authoring-v0.3.0-review.1` and `.2` exist on the
registry (created 2026-08-17) and **nothing in either repo references them** — someone
bumped the base version by hand and it left no trace.

This spec implements ADR-0001's decision for the three rows in scope, and writes down
what the version segment means so it can stop being a frozen string.

The other 21 `moving` rows stay as they are: sims, autopilots and monitoring images
whose build sources are not reachable from these checkouts.

## What changes

### 1. A `traced` channel, and what the version means

New channel `traced`: an immutable tag of the form

```
<component>-v<product_version>-g<short-sha>
```

e.g. `tevv-web-dashboard-backend-v0.2.0-g6e6ae15`. The sha is the commit in the
publishing repo that the image was built from, so a pin answers "what is in this?" with
`git show`, needing no maintained mapping between counters and commits.

`images/catalog.yaml` gains a top-level `product_version:` field, seeded from
`mns-product.yaml`. That file currently says `0.1.0` while every published image tag
says `v0.2.0`; the two were never reconciled. **Part of this work bumps
`mns-product.yaml` to `0.2.0`**, adopting the number the images have used all along, so
the traced line is continuous with the existing review tags rather than appearing to
roll back. Validation rules:

- A `traced` row's tag must match `-v\d+\.\d+\.\d+-g[0-9a-f]{7,}(\.\d+)?$`.
- Its version segment must be **≤ `product_version`**. A tag from the future is an
  error (it is a typo or a bad mint). A tag from the past is *not* an error — images
  published before a version bump legitimately lag — but `status` reports those rows as
  "published before the current product version" so the lag is visible.
- When the `MnS-Integration-Platform` checkout is reachable, an extra check asserts
  `product_version` equals `mns-product.yaml`'s `version`. Unreachable is skipped, not
  failed, so validation still works in a lone clone and in CI.

**Version policy** (recorded in a new ADR, see §5). The product is pre-1.0, so:

| segment | moves when |
|---|---|
| MAJOR (`1.x.x`) | reserved until the product is declared stable. Nothing bumps it yet. |
| MINOR (`0.N.0`) | a contract a consumer outside the repo depends on changes incompatibly, or a new capability lands: ScenarioSpec schema fields, generated stack layout, ROS topic/service names, dashboard API surface. |
| PATCH (`0.x.N`) | fixes and internal changes with no contract impact. |

The bump is a deliberate edit to `mns-product.yaml` mirrored into `product_version`, in
one PR, and images published after it carry the new version. Nothing is re-tagged on a
bump — old tags keep the version they were built under, which is the point of a tag that
names its source.

### 2. Row changes

| key | after | channel |
|---|---|---|
| `dashboard_backend` | `tevv-web-dashboard-backend-v0.2.0-g<sha>` | `traced` |
| `dashboard_frontend` | `tevv-web-dashboard-frontend-v0.2.0-g<sha>` | `traced` |
| `airsim_tools` | `airsim-tools-v0.2.0-retag.2026-08-26` | `pinned` |

`airsim_tools` is a re-tag, not a build: the published image's provenance is unknown and
`rpc-clients/python/tools/Dockerfile` may no longer reproduce it, so `-g<sha>` has
nothing truthful to put in it. `pinned` is the honest channel — there is no source line
for `bump` to walk. One command, same bits, nothing about the running stack changes:

```
docker buildx imagetools create \
  -t dhdevspace/auto_mns:airsim-tools-v0.2.0-retag.2026-08-26 \
  dhdevspace/auto_mns@sha256:c6a3740b7b6e335b3cd265414f2a2e71b46e9f7578dd0e5390d039f8e30d2feb
```

The row records that it was copied rather than built, and carries a follow-up: rebuild
from source, on `traced`, once someone owns it.

The comment blocks in `images/catalog.yaml` explaining why the dashboard and ros2-tools
images float on `-latest` are deleted with the practice they describe.

### 3. Tooling: `images.py`

- **Channel.** Add `traced` to `VALID_CHANNELS` with the tag-shape and version rules above.
- **Family walk.** For a `traced` row, the family is the component prefix
  (`tevv-web-dashboard-backend-`), *not* the version — so a version bump does not orphan
  the row. Candidate tags are those matching the `traced` shape; **newest is the most
  recently pushed**, since sha tags do not sort. `_hub_tags` currently keeps only
  `t["name"]`; it also has to keep `t["last_updated"]`, which the Hub API already returns.
- **`bump`.** Works unchanged once the walk returns a latest tag: `STALE` / `TAG_MOVED` /
  `NO_DIGEST` already drive it.
- **`status`.** Adds the "behind `product_version`" FYI described in §1.

### 4. Publishing (`services/tevv-web-dashboard/tools/build-dashboard-images.sh`)

The script already resolves pins, builds through compose, and verifies the baked
`MNS_*_IMAGE_DEFAULT` ARGs. It gains:

- **Mint.** `traced_tag <component>` = `<component>-v<product_version>-g$(git rev-parse
  --short HEAD)`. `product_version` is read from `mns-product.yaml` in the same repo.
- **Dirty guard.** Refuse to publish when the tree is dirty under `backend/` or
  `frontend/`. A sha tag on a dirty build is a false claim about its own contents, which
  is worse than no claim.
- **Overwrite guard.** Resolve the target tag before pushing; abort if it exists with a
  different digest. Rebuilding the same commit deliberately (new base image, say) uses
  `--rebuild-suffix`, producing `-g<sha>.2` — the shape rule allows the suffix precisely
  so this case does not require a junk commit.
- **Tag routing.** `image:` resolves through `${TEVV_DASHBOARD_*_IMAGE:-…}`, so the
  script exports those to the minted tag before `docker compose build`.
  `unpin_for_build` stays (a `.env`-pinned digest still needs stripping), but the
  deadlock it documents disappears: what gets built is a fresh tag, never the pinned one.
- **`--print-next-tag`** for a dry run that touches no registry state.
- Post-push instructions name `tools/images.sh bump --channel traced`, not the
  deprecated `check-image-pins.sh --bump`.

### 5. Pin flow, fallbacks, guardrails

Release order: build+push → `tools/images.sh bump --channel traced` → `tools/images.sh
sync` → commit.

`tools/pin-dashboard-images.sh` keeps working with two changes: `BACKEND_TAG` /
`FRONTEND_TAG` can no longer default to a fixed string, so both scripts source one
`tools/traced-tags.sh` for "what is the newest traced tag"; and `--check`'s `TAG_MOVED`
branch, unreachable against an immutable tag, is replaced by `STALE` — *a newer traced
tag exists than the one pinned* — which is the question that still has an answer.

Five places name a bare `-latest`; each gets a concrete traced tag:

- `services/tevv-web-dashboard/docker-compose.yml:6,124`
- `services/tevv-web-dashboard/docs/pinning-dashboard-images.md:20,36`
- `docker-compose-tools.yml:23`
- `tools.sh:15` (comment)
- `docs/autonomy-developer-test-plan.md:45` (`docker pull`)

These go stale as new tags land. Accepted: they are a floor for a fresh clone, not the
release path, which reads `product-images.env`.

Guardrails:

- Catalog rows gain optional `published_by:` (the repo+script that publishes). Validator
  rule: **a row with `published_by` may not be `channel: moving`** — `traced` or `pinned`
  only. Offline, so the existing PR gate (`images.sh verify`) catches any future attempt
  to point one of our own images back at a mutable tag, with no credentials involved.
  A `selftest` fixture covers it.
- `verify` fails when a tracked file **in this repo** references
  `dhdevspace/auto_mns:<name>-latest` for a name owned by a `published_by` row; owned
  names are derived from the catalog, not listed twice. `images.py` cannot see the
  dashboard repo, so `build-dashboard-images.sh` gets the equivalent check on its own
  compose file: refuse to push while it still names a `-latest` fallback for an image it
  publishes.
- **ADR-0003** in this repo records the `traced` channel and the version policy, and
  notes that `mns_authoring` / `mns_stack_generator` / `mns_blocks` /
  `mns_product_shell` remain on `-review.N` for now, so the coexistence is a stated
  decision rather than an accident.

Existing `-latest` tags stay in the registry untouched: we stop pushing them, we do not
delete them.

## Rollout order

The guard must not land before the thing it guards, and a tag must trace to a mainline
commit.

1. **Version reconciliation.** Bump `mns-product.yaml` to `0.2.0` (see §1), so
   `product_version` has something true to be seeded from.
2. **MSRS tooling.** `traced` channel, `product_version`, `last_updated` in `_hub_tags`,
   `published_by` rule, bare-`-latest` grep, selftest fixtures, ADR-0003. The three rows
   stay `moving`, so `verify` still passes.
3. **Platform build script.** Mint, dirty guard, overwrite guard, `traced-tags.sh`,
   `--print-next-tag`, compose-fallback check.
4. **airsim-tools.** Re-tag → flip the row to `pinned` with the dated tag → `sync`.
   Smoke: `tools.sh` still resolves its image.
5. **Dashboard.** Merge `feat/spec-file-browser-metrics-tabs-grafana-health` to main
   first, then publish backend+frontend from main → flip both rows to `traced` →
   `bump --channel traced` → `sync` → commit. **Blocked on that merge**: a sha tag
   pointing at a commit that exists only on a feature branch defeats its own purpose.
6. **Fallbacks.** Repoint the five references; delete the `-latest` rationale comments.

## Verification

- `tools/images.sh selftest` — new validator rules on synthetic fixtures, offline:
  tag shape, version ≤ `product_version`, `published_by` not `moving`.
- `tools/images.sh verify` — generated artifacts still match the catalog; the new rules
  pass on the real catalog; a planted `-latest` reference in a tracked file fails it.
- `tools/images.sh status` — the three rows report `OK`; a deliberately un-bumped row
  must report `STALE` rather than `OK`. **Observe this once, on purpose** — staleness
  detection is the capability this change buys and it would be easy to ship without ever
  confirming it works.
- `build-dashboard-images.sh --print-next-tag` — mints the expected tag without pushing;
  refuses on a dirty tree; refuses to overwrite an existing tag with different content.
- `make dashboard` up, backend health endpoint responds; `make tools` resolves and runs.
- Fresh-clone check: with no `product-images.env` sourced, the compose fallbacks still
  name a real, pullable image.

## Out of scope

- The other 21 `channel: moving` rows.
- Migrating the four existing `-review.N` rows to `traced`. ADR-0003 states the
  coexistence; converting them is separate work with its own publishing repos.
- CI-side publishing (Actions building and pushing, so a tag can only come from a
  commit). The natural follow-up to the mint guards, blocked today on there being no
  dashboard CI, no runner sizing, and no registry credentials in that repo.
- Deleting or repointing existing `-latest` tags on the registry.
