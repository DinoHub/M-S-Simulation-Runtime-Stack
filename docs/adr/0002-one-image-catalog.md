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
| `images/legacy-images.generated.env` | ~53 hardcoded/`.env`-fed refs across the legacy static scenario stacks (R-6; see "Legacy scenario stacks" below) |

`tools/images.sh sync` renders all of them from the catalog; `tools/images.sh
verify` regenerates into a temp location and diffs against the committed copies,
exiting 1 on any drift — this is the CI gate (`make verify-images`,
`.github/workflows/images.yml`). Both are offline: no registry calls, so they
run on every PR with no network dependency and no flakiness.

**The digest is the contract; the tag is for humans — generalizing ADR 0001 to
every image, not just the product ones.** `repo:tag@sha256:...` is what
actually resolves; the tag next to it is a comment Docker happens to validate.
Every digest is the **index** (manifest-list) digest: the `Digest:` line of
`docker buildx imagetools inspect <ref>`'s default (non-`--format`) report
gives it — see below for why the tempting `--format '{{.Manifest.Digest}}'`
is the wrong tool for this; `docker manifest inspect -v` reports the
per-architecture manifest instead, which is the easy way to pin the wrong
thing and have it work only on the machine you tested on.

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
- `unpublished` — a real (non-`local/`) repo/tag that this repository
  references but that does not currently exist on the registry. `digest` is
  always `null`; `report` shows `UNPUBLISHED` without attempting a lookup (a
  lookup would only ever confirm what the row already says), `verify` does
  not fail the build over it, and `bump` refuses it outright. See "Unpublished
  images" below.

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
is still valid and the bumped row shows the new value. `upstream`, `local`,
and `unpublished` rows are refused outright, regardless of `--only`/`--channel`.

**A failed live lookup is `UNRESOLVABLE`, never `NO_DIGEST`.** The two look
similar in effect — neither has a digest to show — but they mean opposite
things: `NO_DIGEST` means the registry answered and the row simply has not
been pinned yet (routine, `bump` fixes it); `UNRESOLVABLE` means the lookup
itself failed (404, auth, network) and nothing was learned. Collapsing them,
which an early version of this tooling did, prints the row's own tag in the
"live" column on failure — indistinguishable from "not yet pinned" at a
glance, exactly the silent-OK failure mode `report` exists to prevent. Every
resolver path (`hub` and `imagetools`) returns `(value, error)` and a nonzero
`error` always produces `UNRESOLVABLE` with the reason in the detail column,
before any digest-comparison logic runs. `report` exits nonzero when any
non-`local`/non-`unpublished` row is `UNRESOLVABLE`, so it can gate a build.

### Unpublished images

Building this catalog surfaced four images that were referenced live but did
not exist on the registry at the time: `mock_data_generator`
(`docker-compose-monitoring.yml:69`), `exploration_mock_generator`
(`docker-compose-monitoring.yml:100`), `jsonl_ingestor`
(`docker-compose-monitoring.yml:196` — present only as a local image on one
machine), and `unreal_authored` (`image_sets.yaml`'s published
`simulators.unreal_authored` role). `docker manifest inspect` found none of
them. `jsonl_ingestor` was pushed to the registry during this work and is now
`channel: moving` with a verified digest (`images/catalog.yaml`); the other
three remain `channel: unpublished`. A fresh clone pulling the affected
compose profiles (`mock-testing`, `mock-data`, `exploration-mock`, or the
published `unreal_authored` simulator role) still fails today, registry-side,
regardless of this catalog.

These get `channel: unpublished` rather than `moving` (which would report
`NO_DIGEST` forever, indistinguishable from "just hasn't been pinned yet")
or `UNRESOLVABLE` (which would report a real infrastructure gap as if it were
a tooling failure, and — worse — would make `report` exit nonzero on every
run in a way no `bump` can fix). `unpublished` says plainly, in git: this
reference exists, it does not resolve, and here is why. Each row's `purpose:`
carries the "referenced from" pointer so the gap is discoverable without
re-deriving it from a failed `docker pull`.

Fixing the underlying gap — publish the remaining three images, or remove
the compose references that need them — is a follow-up decision for whoever
owns those
images' CI, not this catalog's job. The catalog's job is to make the gap
visible instead of a pull failure being the first anyone hears of it.

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

### Legacy scenario stacks

R-6 adds the ~53 image references across the five legacy static scenario
stacks — `compose/{ardupilot-condo,ardupilot-urbansim,ardupilot-xfs,px4-condo,
px4-xfs}/docker-compose.yml`, the autonomy team's daily `make ardupilot-xfs` /
`make px4-xfs` path — to the catalog, generating `images/legacy-images.
generated.env`. **The `${VAR:-default}` fallback already in every one of
these compose files is UNCHANGED by this commit** — the eight legacy vars
(`ARDUPILOT_IMAGE`, `AIRSIM_CONDO_IMAGE`, `AIRSIM_BRIDGE_IMAGE`, `QGC_IMAGE`,
`ZENOH_BRIDGE_IMAGE`, `AIRSIM_IMAGE`, `PIXEL_STREAMING_SIGNALLING_IMAGE`,
`PX4_IMAGE`) are stripped to `${VAR:?...}` only after a real `make
ardupilot-xfs` + `make px4-xfs` round-trip on hardware, matching R-5's
two-step precedent for the platform vars.

**Reused rows** (default tag matches an existing catalog row exactly, so
the legacy var is simply bound to it rather than duplicated): `QGC_IMAGE` →
`qgroundcontrol`, `PX4_IMAGE` → `px4`, ardupilot-condo's `AIRSIM_CONDO_IMAGE`
→ `condo`, px4-xfs's `AIRSIM_IMAGE` → `xfs`.

**New rows**: `ardupilot_slim`, `airsim_ros2_bridge_legacy`,
`condo_latest_legacy`, `airsim_xfs_legacy`, `airsim_urbansimdemo`,
`pixel_streaming_signalling`, `zenoh_bridge_ros2dds` (`channel: upstream`,
`resolver: imagetools` — `eclipse/` is a third-party Hub org this repo does
not control, same treatment as `prom/`/`grafana/`/`sid220/`/`timescale/`).

#### Conflicting defaults

Building `consumers.legacy_env` surfaced two var names whose compose-file
default is **not the same image** depending on which scenario references
it — exactly the kind of drift ADR-era tooling (a single `product-
images.env`-style flat var list) cannot represent honestly:

- **`AIRSIM_IMAGE`** — three distinct defaults: ardupilot-xfs's
  `xfs-latest`, px4-xfs's `tevv-airsim-xfs-latest`, ardupilot-urbansim's
  `urbansimdemo-latest`. This one is **documented and intentional** — the
  px4-xfs template itself says "Default tag differs from ardupilot-xfs's
  xfs-latest on purpose (PX4-flavoured XFS build); root .env's AIRSIM_IMAGE
  overrides both scenarios identically" (`compose/px4-xfs/templates/
  docker-compose.yml.j2`).
- **`AIRSIM_CONDO_IMAGE`** — two distinct defaults: ardupilot-condo's
  `tevv-airsim-condo-latest-ceilingless` vs px4-condo's `condo-latest`, with
  **no comment anywhere explaining the split** — looks like ordinary drift
  rather than a deliberate choice, though nothing depends on unifying it
  today (root `.env` already pins `AIRSIM_CONDO_IMAGE=...condo-latest`, so
  ardupilot-condo runs px4-condo's image today too — shell/.env always
  outranks a compose default).

Because `consumers.legacy_env` is grouped by scenario (not by var name),
the catalog records both values, correctly attributed to the file that uses
each. `tools/images.py`'s `render_legacy_env` renders one `VAR=ref` line
into `images/legacy-images.generated.env` only when every scenario group
that binds a var agrees on the same resolved ref; a var where they disagree
gets **no line at all**, plus a comment block naming every value and the
scenario that uses it. This is deliberate, not an oversight: any single
value chosen for the flat env file would, once a developer removes that var
from `.env`, silently pin ardupilot-urbansim/ardupilot-xfs/px4-xfs (or
ardupilot-condo/px4-condo) to each other's image — invisibly changing what
one of those scenarios runs. Unifying it is a scenario-design decision for
whoever owns those compose files, not this catalog's job; the catalog's job
is to make the disagreement visible in git instead of two developers
discovering it by diffing `docker ps` output.

#### Precedence chain

`load_images_env` (unchanged from R-4) exports a key from a given generated
file only when it is **both** unset in the shell **and** absent from
`./.env`. Stacking both generated files (`platform-images.generated.env`,
then `legacy-images.generated.env`) in `launch.sh`/`stop.sh`/`logs.sh` gives:

```
shell env  >  ./.env  >  images/legacy-images.generated.env  >  compose ${VAR:-default}
```

Root `.env` today sets `ARDUPILOT_IMAGE`, `AIRSIM_IMAGE`, `PX4_IMAGE`,
`AIRSIM_BRIDGE_IMAGE`, `ZENOH_BRIDGE_IMAGE`, and `AIRSIM_CONDO_IMAGE` —
**for every one of those, the catalog's value does not take effect until a
developer removes the key from `.env`**, by design, for this commit.
`QGC_IMAGE` and `PIXEL_STREAMING_SIGNALLING_IMAGE` are commented out in
`.env` today, so those two DO take effect immediately once this commit
lands: the compose-resolved image gains an `@sha256:...` digest suffix
pinning the exact same tag it already defaulted to (verified live against
the registry at pin time) — same effective image, now reproducible, which
is the entire point of R-4 through R-6. `AIRSIM_IMAGE` and
`AIRSIM_CONDO_IMAGE` are never emitted at all (see "Conflicting defaults"
above), so they are completely unaffected regardless of `.env` contents.

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
