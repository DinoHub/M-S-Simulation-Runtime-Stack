# 0002. One canonical image catalog; everything else generated

- **Status:** Accepted
- **Date:** 2026-08-22

## Context

Container image references lived in five disconnected places: `product-images.env`
(7 product images, properly digest-pinned per ADR 0001), the stack generator's baked
`image_sets.yaml` (all runtime sim images, bare mutable tags, no digest), ~19
hardcoded tags inline across `docker-compose-monitoring.yml`,
`docker-compose-metrics.yml`, `docker-compose-logs.yml` and
`docker-compose-dashboard.yml`, ~50 refs across the legacy static scenario stacks
(fed by a gitignored `.env`), and two refs baked into the released dashboard
backend image.

Consequence: a scenario run was not reproducible even though the product tooling
was pinned. `xfs-latest` could move between two runs of the same spec with
nothing in git to show it, and two developers running `make ardupilot-xfs` could
be running different sims with no record of the difference. Bumping pins was
~25 hand-run commits over three weeks, with no CI check that anything was even
consistent — `product-images.env` could drift from `image_sets.yaml` (or either
from the inline compose tags) and nothing would notice.

## Decision

**One authored file, `images/catalog.yaml`.** Every distinct image is a row —
`repo`, `tag`, `digest`, `channel`, `purpose`, plus an optional `resolver` and
`bakes` — listed exactly once regardless of how many places reference it. A
`consumers:` section binds catalog keys to their actual uses (env var names,
`image_sets` roles), so an image used in two places is still one row.

**Everything else is generated, committed, and CI-verified — never hand-edited:**

| Generated artifact | Replaces |
| --- | --- |
| `product-images.env` | itself (unchanged path/format; only new line is the GENERATED marker) |
| `images/image-set.generated.yaml` | overlays `MnS-Integration-Platform`'s `image_sets.yaml` via the generator's existing `MNS_IMAGE_SET_FILE` hook |
| `images/platform-images.generated.env` | the 19 hardcoded tags in the metrics/monitoring/logs/dashboard compose files |
| `images/legacy-images.generated.env` | the legacy static-stack `.env` (added in a later commit, R-6) |

`tools/images.sh sync` renders all of them from the catalog; `tools/images.sh
verify` regenerates into a temp location and diffs against the committed copies,
exiting 1 on any drift — this is the CI gate (`make verify-images`,
`.github/workflows/images.yml`). Both are offline: no registry calls, so they
run on every PR with no network dependency and no flakiness.

**The digest is the contract; the tag is for humans — generalizing ADR 0001 to
every image, not just the product ones.** `repo:tag@sha256:...` is what
actually resolves; the tag next to it is a comment Docker happens to validate.
Every digest is the **index** (manifest-list) digest: `docker buildx imagetools
inspect <ref> --format '{{.Manifest.Digest}}'` reports it; `docker manifest
inspect -v` reports the per-architecture manifest instead, which is the easy
way to pin the wrong thing and have it work only on the machine you tested on.

**`channel` replaces the old regex-based inference in `check-image-pins.sh`:**

- `review` — `repo:tag-review.N`; `bump` advances N via Docker Hub tag search
  (same as before).
- `moving` — a mutable tag (typically `-latest`) that is republished in place;
  the tag never changes but the digest does, reported as `TAG_MOVED`.
- `upstream` — a third-party image on a version tag (`gcr.io`, `nvcr.io`,
  `ghcr.io`, `docker.elastic.co`, and third-party Hub orgs this repo does not
  control — `prom/`, `grafana/`, `sid220/`, `timescale/`). Never auto-bumped:
  a version bump for someone else's image is a deliberate upgrade, not routine
  maintenance.
- `local` — locally built (`repo` starts with `local/`); `digest` is always
  `null`, and the row is skipped by both `verify` and `bump`. A `local/…` ref
  with `pull_policy: always` fails at registry lookup, so `sync` asserts no
  image set combines the two.

**`resolver` says how `report`/`bump` find a live digest:** `hub` (default,
Docker Hub v2 API — needed to enumerate `-review.N` siblings) or `imagetools`
(`docker buildx imagetools inspect` + its default report's `Digest:` line —
**not** `--format '{{.Manifest.Digest}}'`, which silently prints nothing useful
against a single-architecture image; see
`MnS-Integration-Platform/services/tevv-web-dashboard/tools/pin-dashboard-images.sh:52-58`,
which got this wrong once and documents why). Every `upstream` row uses
`imagetools`, since none of those registries are Docker Hub.

**Bump is line-targeted, never `yaml.dump`.** A YAML round-trip through
`yaml.dump` would re-serialize the whole file and destroy every `purpose:`
comment and section-header comment — which is the entire point of hand-authoring
this file instead of a flat env list. `tools/images.sh bump` finds the specific
`tag:`/`digest:` lines for the row being bumped with a targeted regex, rewrites
only those two lines, then re-parses the whole file and asserts the structure
is still valid and the bumped row shows the new value. `upstream` and `local`
rows are refused outright, regardless of `--only`/`--channel`.

**Baked backend refs are a generated, CI-verified cache, not a second source of
truth.** The dashboard backend's Dockerfile bakes `MNS_AUTHORING_IMAGE_DEFAULT`
and `MNS_STACK_GENERATOR_IMAGE_DEFAULT` so an image-only deploy needs no env
wiring. That makes the backend a *consumer* of two other catalog rows — declared
via `bakes: [mns_authoring, mns_stack_generator]` on the `dashboard_backend` row.
`tools/images.sh baked` walks that edge (instead of a hardcoded var-name pair)
and now exits nonzero on drift, turning what used to be advisory output into a
CI assert.

**`check-image-pins.sh` becomes a thin, deprecated shim** onto `tools/images.sh`,
mapping the old flags (`--bump`, `--drift`) through with a one-line deprecation
notice on stderr and the same exit codes — three weeks and ~25 commits of muscle
memory and PR descriptions reference it by name.

## Consequences

**Better.** One place to look at what a scenario run actually pulls, whether it
is the product shell, the sim it launches, or the metrics stack watching it.
Every generated artifact is committed, so any PR reverts byte-exact, and
`verify` catches hand-edits to a generated file or a catalog edit nobody
re-synced. `report`/`baked` distinguish "behind" from "retagged under you" from
"cannot be resolved at all" — the last of which used to be silently indistinguishable
from OK.

**Worse.** One more layer of indirection between "the tag I see in a compose
file" and "the image that actually runs" — for anything under
`images/*.generated.*` or `product-images.env`, the truth is now
`images/catalog.yaml`, and editing the generated file directly is a caught
mistake (`verify` fails) rather than a silent one. Long digest lines are still
ugly, same as ADR 0001. `bump`'s line-targeted rewrite is more code than a
`yaml.dump` would have been, in exchange for keeping every hand-written comment.

**Unchanged.** `buildx` still cannot build against a digest reference —
`unpin_for_build` still strips the `@sha256:…` suffix at build sites, only
consumption uses the full reference. Existing unlaunchable pins (ADR 0001's
`review.17`–`.19`) are unaffected; this decision does not rewrite history.

## Cross-references

- `MnS-Integration-Platform/docs/adr/0001-image-versioning-and-digest-pinning.md`
  — the original decision to pin product images by digest; this ADR generalizes
  it to every image the runtime stack touches and adds the generated-artifact +
  CI-verification layer on top.
- `images/catalog.yaml` — the catalog itself, header comment restates this
  contract for anyone editing it without having read this file first.
