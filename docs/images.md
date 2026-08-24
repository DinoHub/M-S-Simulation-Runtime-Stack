# Changing a container image

One rule: **every image reference in this repository is authored in
`images/catalog.yaml` and nowhere else.** If you are typing a `repo:tag`
anywhere but there, stop.

The catalog renders four files. All are committed, all carry a
`# GENERATED from images/catalog.yaml` header, none are hand-edited:

| Generated file | Who reads it |
| --- | --- |
| `product-images.env` | `product.sh`, `Makefile`, the full-product e2e test |
| `images/image-set.generated.yaml` | the stack generator, via `MNS_IMAGE_SET_FILE` |
| `images/platform-images.generated.env` | metrics / monitoring / logs / dashboard compose |
| `images/legacy-images.generated.env` | the legacy static scenario stacks |

Regenerate with `tools/images.sh sync`. `tools/images.sh verify` regenerates
into a temp dir and diffs against the committed copies, exiting nonzero on any
drift — that is the CI gate (`make verify-images`). Both are offline: no
registry calls, no network flakiness, runnable on every PR.

## Which command

| You want to | Do |
| --- | --- |
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

**1. `./.env` beats it.** Compose auto-loads `./.env`, and
`tools/load-images-env.sh` deliberately skips any key `.env` already sets, so
a local override wins. That is the intended escape hatch for running a local
build. It is also the first thing to check when the catalog looks right but
the wrong image runs — `tools/images.sh report` lists every var `./.env`
overrides, with both values.

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
