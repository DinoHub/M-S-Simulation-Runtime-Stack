# Changing a container image

One rule: **every image reference in this repository is authored in
`images/catalog.yaml` and nowhere else.** If you are typing a `repo:tag`
anywhere but there, stop.

The catalog renders seven files. All are committed, all carry a
`# GENERATED from images/catalog.yaml` header, none are hand-edited:

| Generated file | Who reads it |
| --- | --- |
| `product-images.env` | legacy review channel plus dashboard/tool pins |
| `images/standalone-v2-images.generated.env` | v2 product shell, dashboard overrides |
| `images/standalone-v2-development.generated.env` | dashboard development defaults using mutable tags |
| `images/image-set.generated.yaml` | production stack generator overlay with exact pins |
| `images/image-set.development.generated.yaml` | dashboard development overlay with tag-only refs |
| `images/platform-images.generated.env` | metrics / monitoring / logs / dashboard compose |
| `images/legacy-images.generated.env` | the legacy static scenario stacks |

Regenerate with `tools/images.sh sync`. `tools/images.sh verify` regenerates
into a temp dir and diffs against the committed copies, exiting nonzero on any
drift — that is the CI gate (`make verify-images`). Both are offline: no
registry calls, no network flakiness, runnable on every PR.

## Start here

```
tools/images.sh status
```

One prioritized list: **NEEDS YOU** (something is stale, drifted, invalid, or
has an open follow-up) and **FYI** (known and deliberate — unpublished images,
`./.env` overrides). Exits nonzero only when the first list is non-empty.
`--offline` skips every registry lookup and still checks catalog validity,
artifact drift, follow-ups and overrides.

It does not shell out to docker, so two things stay separate:
`tools/images.sh baked` (needs docker) and `tools/images.sh drift` (needs the
generator image). `status` says so in its last line rather than pretending to
have covered them.

CI runs it for you: `verify` gates every PR touching the catalog or its
tooling, and a Monday-morning scheduled job runs the online `status`. Both on
free public-repo runners.

## Follow-ups: reminders live in the catalog

Anything gated on something outside the catalog — a merge, a hardware test,
a decision — goes in the file, not in your head. Per row:

```yaml
  mns_authoring:
    channel: pinned
    follow_up: >-
      move back to channel review once TEVV-Authoring PR #8 ships a -review.N tag
```

Or, for a reminder belonging to no single image, the catalog-level list:

```yaml
follow_ups:
  - >-
    legacy stacks still carry ${VAR:-tag} fallbacks; strip them only after a
    real `make ardupilot-xfs` + `make px4-xfs` round-trip on hardware
```

Both print under NEEDS YOU on every `status` run, and both are reviewed in any
PR that touches the file. Delete the entry when it is done.

## Which command

| You want to | Do |
| --- | --- |
| Find out what needs attention at all | `tools/images.sh status` |
| Move to a newer `-review.N` build | `tools/images.sh bump` (all review rows) or `bump --only KEY` |
| Re-pin a `-latest` image that was republished | `tools/images.sh bump` — reports `TAG_MOVED`, rewrites the digest |
| Upgrade a third-party image (prom, grafana, nvcr…) | edit the `tag:` in the catalog, then `bump --only KEY` to resolve its digest. Never bulk-bumped: someone else's version bump is a deliberate upgrade |
| Pin a branch or preview build | edit `tag:` + `digest:` by hand, set `channel: pinned`, `sync` |
| Add an image the repo did not reference before | add an `images:` row **and** a `consumers:` binding, then `sync` |
| Check what is stale | `tools/images.sh report` (online) |
| Check nobody hand-edited a generated file | `tools/images.sh verify` (offline) |

After any catalog edit: `tools/images.sh sync && tools/images.sh verify`, and
commit the catalog together with the regenerated files. A PR that changes one
without the other fails CI.

## Digests, and getting them right

A pin is `repo:tag@sha256:…`. The **digest is the contract** — it is what
resolves, on every machine, forever. The tag rides along so the file is
readable; nothing stops a tag being repointed, so a tag alone is not a pin.

Always pin the **index** (manifest-list) digest. Read it off the `Digest:`
line of `docker buildx imagetools inspect <ref>`'s default report. Not
`--format '{{.Manifest.Digest}}'` (silently wrong for a single-arch image),
and not `docker manifest inspect -v` (gives the per-architecture entry, which
works on the machine you tested and fails everywhere else).

`tools/images.sh bump` does all of this for you. Resolve by hand only for
`channel: pinned` rows.

## Three things the catalog does not control

**1. Local development overrides.** `make dashboard` defaults to the generated tag-only development image set. A matching locally built tag wins, while an absent tag is pulled from the registry. Shell/.env image overrides still take precedence through `tools/load-images-env.sh`. `IMAGE_MODE=production make dashboard` selects the immutable digest-pinned artifacts instead.

**2. Baked backend defaults.** The dashboard-backend image carries
`MNS_AUTHORING_IMAGE_DEFAULT` and the generator equivalent *inside the built
image*. No file to render, so no `sync` can fix it: it needs a backend
rebuild, then `tools/images.sh bump --only dashboard_backend`. Compose
deployments pass the env through and are unaffected; an image-only deploy is
not. `tools/images.sh baked` reports the drift.

**3. A locally-present newer image.** Development mode intentionally uses a matching local tag and never pulls merely to check for a newer remote copy. If the tag is absent, `make dashboard` pulls it. Production mode remains digest-pinned and ignores a different local build. After publishing, other developers run `./product.sh setup` to refresh the approved remote set.

## Channels

`channel:` on each row drives `report` and `bump`:

- `review` — `repo:tag-review.N`; `bump` advances N. The tag **must** end in
  `-review.N`; the catalog rejects anything else, because `bump` derives the
  family from the tag and an off-line tag would report `NO_TAGS_FOUND`
  forever while quietly meaning "stale".
- `moving` — a mutable tag (typically `-latest`) republished in place. Tag
  never changes, digest does; reported as `TAG_MOVED`.
- `traced` — an image **we** publish, on a tag that names the source it was
  built from: `<component>-v<x.y.z>-g<sha>` (optionally `.N` for a rebuild of
  the same commit). Immutable by contract — nothing ever republishes one of
  these — so `TAG_MOVED` on a traced row means someone force-pushed over it.
  The catalog rejects a `traced` row whose tag is not that shape, and rejects
  a version component that runs ahead of `product_version:`. `report`/`bump`
  walk the family by registry push time, because a `g<sha>` suffix carries no
  ordering of its own; a newer sibling reports `STALE`. A row carrying
  `published_by:` may not sit on a mutable channel at all, and `verify` fails
  on any tracked file that still references one of these images by a bare
  `-latest` tag. See [ADR 0003](adr/0003-traced-tags-and-product-version.md).
- `upstream` — third-party image on a version tag. Never auto-bumped.
- `pinned` — published, deliberately off the release line (branch or preview
  build). Digest required, verified by `report`, refused by `bump`, moved by
  hand. The row should say in a comment why, and how to get back.
- `local` — locally built, `repo` starts with `local/`, `digest: null`,
  skipped by `verify` and `bump`.
- `unpublished` — referenced by this repo but absent from the registry.
  `digest: null`, no lookup attempted, `bump` refuses. Needs a push or a
  reference removal.

Why any of this exists, and what it costs:
[ADR 0002](adr/0002-one-image-catalog.md).

## MnS Docker image names and pulls

MnS-owned images use one Docker Hub repository and two tags per build:

- Mutable developer alias: `dhdevspace/auto_mns:<stem>-latest`
- Immutable release tag: `dhdevspace/auto_mns:<stem>-<date-or-version>`

For example, the ROS 2 bridge publishes
`tevv-airsim-ros2-bridge-humble-latest` and
`tevv-airsim-ros2-bridge-humble-20260826` to the same manifest. Production
catalog rows use the immutable tag plus its full manifest digest. The
`-latest` alias is for discovery and developer pulls; it is not a production
pin. Retagging the same manifest does not duplicate its layers in the registry.

The production M-S image set is remote-only and digest-pinned. The dashboard’s transitional development mode derives tag-only refs from that same catalog, allowing a local build to win without adding `local/...` repository names.

Use locally available development tags and pull only missing ones:

```bash
make ensure-images
# automatically performed by:
make dashboard
```

Explicitly refresh every approved production pin:

```bash
./tools/pull-all-images.sh
# or
./product.sh pull-images
./product.sh pull-images --development    # explicitly refresh dashboard development tags
# inspect without pulling
./tools/pull-all-images.sh --dry-run
# include every legacy and optional catalog image
./tools/pull-all-images.sh --all-catalog
```

To also advance all mutable catalog rows before pulling, use
`./tools/pull-all-images.sh --refresh-moving`. This updates the authored
catalog and generated pin files, so review and commit those changes. Immutable
standalone-v2 release rows only advance through an explicit coordinated release.
