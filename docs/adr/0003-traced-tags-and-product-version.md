# 0003. Traced tags and the product version policy

- **Status:** Accepted. The `traced` channel, `product_version`, and their
  validation rules are implemented in this repo (`da878cb`..`d24d269`).
  `dashboard_backend`, `dashboard_frontend`, and `airsim_tools` still publish
  to `channel: moving` today — converting those rows, and the publishing
  script that mints traced tags, is follow-up work tracked outside this ADR.
- **Date:** 2026-08-27
- **Repos touched:** `M-S-Simulation-Runtime-Stack` (this repo, the catalog
  and its tooling); `MnS-Integration-Platform` (`mns-product.yaml`, the
  version this repo's `product_version` mirrors).

## Context

ADR-0001 (`MnS-Integration-Platform/docs/adr/0001-image-versioning-and-digest-pinning.md`)
decided how a tag should read: *"New tags name their source. Publish as
`<component>-<version>-<short-sha>` rather than an opaque counter."* That
decision was never carried out. ADR-0002 (`docs/adr/0002-one-image-catalog.md`)
generalized digest pinning to every image in `images/catalog.yaml`, but the
tooling it standardised on for images this repo builds is `channel: review`,
`<component>-v<x.y.z>-review.N` — a counter again, just relocated.

Three rows in the catalog still publish to a bare mutable tag instead of
either scheme: `dashboard_backend` (`tevv-web-dashboard-backend-latest`),
`dashboard_frontend` (`tevv-web-dashboard-frontend-latest`), and
`airsim_tools` (`airsim-tools-latest`). A `moving` row cannot be checked for
staleness — there is no tag family to walk, so the only signal a rebuild
happened is `TAG_MOVED`, which appears after the fact and says nothing about
what changed. That gap was not hypothetical: the dashboard's local images
were rebuilt 2026-08-25, the registry's `-latest` still resolved to the
2026-08-22 build the catalog had pinned, and nothing in either repo
distinguished those two facts from inside a checkout.

The version segment in every tag has the same problem one layer up. Nothing
determines the `v0.2.0` string stamped into a new tag; it is copied by hand.
`MnS-Integration-Platform/mns-product.yaml` said `version: 0.1.0` while every
published image tag said `v0.2.0`, and nothing reconciled the two files.
The gap was live, not academic: `mns-authoring-v0.3.0-review.1` and `.2`
exist on the registry (image config `created` 2026-08-17) and are referenced
by nothing in either repo — someone bumped the base version by hand for a
publish and left no trace of it anywhere git can show.

## Decision

**`traced` is the channel for images this repo builds.** A `traced` row's
tag is immutable and names its source:

```
<component>-v<product_version>-g<short-sha>[.N]
```

e.g. `tevv-web-dashboard-backend-v0.2.0-g6e6ae15`. The sha is the commit in
the publishing repo the image was built from, so a pin answers "what is in
this?" with `git show`, needing no maintained mapping between counters and
commits — the thing ADR-0001 decided and ADR-0002's `-review.N` tooling did
not deliver. `_validate_catalog` rejects a `traced` row whose tag does not
match this shape.

This is what ADR-0001 named as the cost of the alternative: `dashboard_*`
and `airsim_tools` sat on `-latest`, where staleness is undetectable by
construction, precisely because the tag carries no source and no ordering.

**`product_version` is the one source for the version segment**, mirrored
from `MnS-Integration-Platform/mns-product.yaml`. `images/catalog.yaml`
carries it as a top-level field. A `traced` tag's version must be
**≤ `product_version`**: a tag claiming a version ahead of the catalog is a
typo or a bad mint and `_validate_catalog` rejects it outright; a tag behind
`product_version` is not an error — images published before a bump
legitimately lag — but `status` reports those rows as published before the
current product version, so the lag stays visible instead of silent. When a
sibling `MnS-Integration-Platform` checkout is reachable, an additional check
asserts `product_version` equals that repo's `mns-product.yaml` version;
unreachable is skipped, not failed, so validation still works from a lone
clone and in CI. This is the fix for the drift above: `mns-product.yaml` at
`0.1.0` against tags already at `v0.2.0`, and a hand-bumped
`mns-authoring-v0.3.0-review.*` pair that no file recorded.

**Pre-1.0 version policy.** The product is pre-1.0, so the segments carry
different weight than mainline semver:

| segment | moves when |
|---|---|
| MAJOR (`1.x.x`) | reserved until the product is declared stable. Nothing bumps it yet. |
| MINOR (`0.N.0`) | a contract a consumer outside the repo depends on changes incompatibly, or a new capability lands: ScenarioSpec schema fields, generated stack layout, ROS topic/service names, dashboard API surface. |
| PATCH (`0.x.N`) | fixes and internal changes with no contract impact. |

A bump is a deliberate edit to `mns-product.yaml`, mirrored into
`product_version` in `images/catalog.yaml`, in one PR. Nothing is re-tagged
on a bump — old tags keep the version they were built under, which is the
entire point of a tag that names its source rather than counting.

**Newest means most recently pushed, for `traced` rows.** A `traced` row's
family is the component prefix (`tevv-web-dashboard-backend-`), not the
version, so a version bump does not orphan the row's history. Candidate tags
are those matching the `traced` shape; because sha tags do not sort
lexically or numerically the way `-review.N` does, ordering them by tag name
would be meaningless — the family walk keeps each candidate's registry
`last_updated` and picks the most recently pushed one as newest.

**`review` and `traced` coexist deliberately.** `mns_stack_generator`,
`mns_blocks`, and `mns_product_shell` stay on `-review.N`. `mns_authoring`
sits on `channel: pinned` at `mns-authoring-v0.2.0-zones-preview.4`,
deliberately off the review line pending TEVV-Authoring PR #8, with a
follow-up to move it back to `channel: review` once that work ships as a
`-review.N` tag. None of the four convert to `traced` by this decision.
They publish from different repos with their own release cadence, and
migrating them is separate work. Recording that here makes the two-scheme
catalog a stated decision instead of something that reads as unfinished
migration.

## Consequences

**Better.** A `traced` pin answers "what is in this?" with `git show` on the
named sha, with no counter-to-commit mapping to maintain — the thing
ADR-0001 asked for. Staleness on a row that publishes this way is
detectable by construction: the family walk has tags to compare, so `bump`
and `status` can say `STALE` instead of silently `OK` while a rebuilt image
sits unpinned. The version segment stops being a copied string: it has one
source (`mns-product.yaml`, mirrored into `product_version`), a stated policy
for when it moves, and a validator that catches both a tag minted ahead of
it and the two files drifting apart the way they already had once.

**Worse.** The catalog now carries two tag schemes a reader has to know —
`-review.N` and `-v<x.y.z>-g<sha>` mean different things and are validated
differently. A traced tag is longer and uglier than a counter. Walking a
traced family needs Docker Hub tag search the same as `review` does, so an
unauthenticated runner still cannot do it — `traced` does not lower that
bar, it only changes what a successful lookup can tell you. And
`product_version` now has to be bumped in two files, `mns-product.yaml` and
`images/catalog.yaml`, in one PR; `verify`'s cross-repo check catches the
two drifting apart only where both checkouts sit side by side, so a lone
clone can still land a bump that is wrong until CI (which does have both
checkouts) catches it.

**Unchanged.** The digest, not the tag, is still the contract per ADR-0001
and ADR-0002 — `traced` gives the tag more information, it does not replace
digest pinning. `images/catalog.yaml` stays the one authored file; `traced`
is a new value in an existing `channel` field, not a new mechanism.
Existing `-latest` tags already on the registry (including
`tevv-web-dashboard-backend-latest`, `tevv-web-dashboard-frontend-latest`,
and `airsim-tools-latest`) are untouched by this decision: nothing here
deletes or repoints them, it only stops new publishes from adding to their
history. `mns_stack_generator`, `mns_blocks`, and `mns_product_shell`
keep publishing to `-review.N` exactly as before; `mns_authoring` keeps
publishing to its off-line `channel: pinned` `-zones-preview.N` tag exactly
as before.

## Cross-references

- `MnS-Integration-Platform/docs/adr/0001-image-versioning-and-digest-pinning.md`
  — decided digest pinning and named source-tracing tags as the fix for an
  opaque counter; this ADR is that decision's implementation for the images
  this repo builds.
- `docs/adr/0002-one-image-catalog.md` — the catalog `traced` is a channel
  of; its `channel` taxonomy, generated-artifact model, and offline
  `verify`/`bump` split are unchanged by this decision.
- `docs/superpowers/specs/2026-08-26-immutable-tags-for-owned-images-design.md`
  — the design this ADR's decisions were drawn from, including the row-level
  rollout (`dashboard_backend`/`dashboard_frontend` to `traced`,
  `airsim_tools` to a one-off `pinned` re-tag) that has not landed yet.
- `images/catalog.yaml` — the catalog itself; `product_version` and any
  `channel: traced` row live there.
