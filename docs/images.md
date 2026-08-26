# Changing a container image

One rule: **every image reference in this repository is authored in
`images/catalog.yaml` and nowhere else.** If you are typing a `repo:tag`
anywhere but there, stop.

The catalog renders five files. All are committed, all carry a
`# GENERATED from images/catalog.yaml` header, none are hand-edited:

| Generated file | Who reads it |
| --- | --- |
| `product-images.env` | legacy review channel plus dashboard/tool pins |
| `images/standalone-v2-images.generated.env` | v2 product shell, dashboard overrides |
| `images/image-set.generated.yaml` | the stack generator, via `MNS_IMAGE_SET_FILE` |
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

**1. Legacy `./.env` overrides.** Retained static Compose workflows still auto-load `./.env`, and `tools/load-images-env.sh` reports those overrides with both values. The active standalone-v2 product sources its generated remote pins after legacy settings and exposes no local-image override. Use a service repository or integration checkout for local image development; do not use `.env` to replace an active M-S product image.

**2. Baked backend defaults.** The dashboard-backend image carries
`MNS_AUTHORING_IMAGE_DEFAULT` and the generator equivalent *inside the built
image*. No file to render, so no `sync` can fix it: it needs a backend
rebuild, then `tools/images.sh bump --only dashboard_backend`. Compose
deployments pass the env through and are unaffected; an image-only deploy is
not. `tools/images.sh baked` reports the drift.

**3. A locally-present newer image.** Because the digest is the contract, a
newer build sitting in `docker images` changes nothing — even with
`pull_policy: always`, Docker re-pulls the exact pinned digest. If you built
and pushed something new, the catalog is not updated until you update it.

## Channels

`channel:` on each row drives `report` and `bump`:

- `review` — `repo:tag-review.N`; `bump` advances N. The tag **must** end in
  `-review.N`; the catalog rejects anything else, because `bump` derives the
  family from the tag and an off-line tag would report `NO_TAGS_FOUND`
  forever while quietly meaning "stale".
- `moving` — a mutable tag (typically `-latest`) republished in place. Tag
  never changes, digest does; reported as `TAG_MOVED`.
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

The M-S product image set is remote-only. Local image development belongs in
the integration repositories and is not an M-S image-set option.

Pull every remote image in the active product set at its approved exact pin:

```bash
./tools/pull-all-images.sh
# or
./product.sh pull-images
# inspect without pulling
./tools/pull-all-images.sh --dry-run
# include every legacy and optional catalog image
./tools/pull-all-images.sh --all-catalog
```

To also advance all mutable catalog rows before pulling, use
`./tools/pull-all-images.sh --refresh-moving`. This updates the authored
catalog and generated pin files, so review and commit those changes. Immutable
standalone-v2 release rows only advance through an explicit coordinated release.
