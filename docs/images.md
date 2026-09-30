# Changing a container image

One rule: **every image reference in this repository is authored in
`images/catalog.yaml` and nowhere else.** If you are typing a `repo:tag`
anywhere but there, stop.

The catalog renders six files, and `tools/images.sh verify` also checks the
pins `packs/v1.0.0.lock.json` restates. All are committed, all carry a
`# GENERATED from images/catalog.yaml` header, none are hand-edited:

| Generated file | Who reads it |
| --- | --- |
| `images/v1.0.0.generated.env` | the v1 channel: `mns-packs`, `mns-stacks`, ScenarioLab, the runtime host and its kit, the bridge, the dashboard |
| `images/development.generated.env` | `make dashboard`'s development defaults (tag-only refs) |
| `images/image-set.generated.yaml` | what generated stacks run (`MNS_IMAGE_SET_FILE` for `mns-stacks generate`), exact pins |
| `images/image-set.development.generated.yaml` | the same, tag-only refs, for development mode |
| `images/platform-images.generated.env` | the dashboard compose file's inline images |
| `product-images.env` | no pins in v1.0.0; kept because the pinned dashboard backend recognises this checkout by it |

Regenerate with `tools/images.sh sync`. `tools/images.sh verify` regenerates
into a temp dir and diffs against the committed copies, exiting nonzero on any
drift — that is the CI gate (`make verify-images`). Both run without a
network: no registry calls, no network flakiness, runnable on every PR.

`verify` also reports two things as a **WARNING** that never fails it (a pin
being filled in, or labels that disagree, is recoverable):

- **Pending digests.** A `channel: pinned` row may carry `pending: "<why>"`
  and `digest: null`: it names its tag (an rc image the release owner has not
  pushed or pinned yet) and renders tag-only until the digest is known. v1.0.0
  starts with `v1_packs`, `v1_stacks` and `v1_runtime_host_kit` pending (their
  `-v1.0.0-rc` images are built in phase 4). Once an image is pushed,
  `tools/images.sh bump --only KEY` resolves the pinned tag's digest, writes
  it, and deletes the `pending:` line; `status` lists pending rows under
  NEEDS YOU until then.
- **One host pin.** The runtime host image is the source of truth, so
  `verify` compares the labels of the images `consumers.release_channels.v1.host_pin`
  names: the host's `tevv.host.kit_image` must name the pinned kit digest, the
  authoring image's `tevv.authoring.host_image` must name the pinned host
  digest, and its `tevv.authoring.shared_set_id` must equal the host's
  `tevv.host.shared_set_id`. `verify` reads images already in the local Docker
  store only (it says NOTE when one is not there); `status` also asks the
  registry.

## Releasing: what `verify --release` requires

On `release/v1.0.0-next` a pin may be *pending* (an rc tag, no digest yet).
What merges into `release/v1.0.0` or `main` may not: `tools/images.sh verify
--release` fails on any `pending:` row, any `-rc` tag in a release channel,
and any tag-only ref in `images/v1.0.0.generated.env` or
`packs/v1.0.0.lock.json`. CI turns it on for every pull request into those two
branches (`GITHUB_BASE_REF`), and runs it as its own step so a red run names
the rule.

Phase 4 (rc images pushed): pin each rc's digest; the tag stays the rc tag.

```bash
tools/images.sh bump --only v1_stacks            # resolves mns-stacks-v1.0.0-rc, drops pending:
tools/images.sh bump --only v1_packs
tools/images.sh bump --only v1_runtime_host_kit
tools/images.sh sync && make pack-lock           # the lock restates packs/stacks
tools/images.sh verify
```

Phase 6 (the accepted rcs retagged `-v1.0.0` in the registry): move each row
to its release tag and that tag's digest. `bump` refuses an ordinary pinned
row, so this is the one explicit retag it does, one row at a time:

```bash
tools/images.sh bump --only v1_stacks --tag mns-stacks-v1.0.0
tools/images.sh bump --only v1_packs  --tag mns-packs-v1.0.0
tools/images.sh bump --only v1_runtime_host_kit --tag tevv-runtime-host-kit-v1.0.0
# rows still on a moving -latest tag: set channel: pinned by hand first, then
tools/images.sh bump --only sim_real_eval  --tag sim-real-eval-worker-v1.0.0
tools/images.sh bump --only qgroundcontrol --tag airsim-qgc-x11-v1.0.0
tools/images.sh sync && make pack-lock
tools/images.sh verify --release                 # must pass before the release PR
```

`--tag` resolves the new tag's index digest (a tag that does not resolve is
skipped, nothing is written), rewrites `tag:` and `digest:`, and drops a
`pending:` note. Delete the rows' `follow_up:` notes in the same commit.

## Start here

```
tools/images.sh status
```

One prioritized list: **NEEDS YOU** (something is stale, drifted, invalid, or
has an open follow-up) and **FYI** (known and deliberate — unpublished images,
`./.env` overrides). Exits nonzero only when the first list is non-empty.
`--offline` skips every registry lookup and still checks catalog validity,
artifact drift, pending digests, follow-ups and overrides.

Two things stay separate: `tools/images.sh baked` (needs docker) and
`tools/images.sh drift` (regenerates the committed stacks with the pinned
`mns-stacks`). `status` says so in its last line rather than pretending to
have covered them.

CI runs it for you: `verify` gates every PR touching the catalog or its
tooling, and a Monday-morning scheduled job runs the online `status`. Both on
free public-repo runners.

## Follow-ups: reminders live in the catalog

Anything gated on something outside the catalog — a merge, a hardware test,
a decision — goes in the file, not in your head. Per row:

```yaml
  v1_authoring:
    channel: pinned
    follow_up: >-
      repin once TEVV-Authoring publishes the fix for <issue>
```

Or, for a reminder belonging to no single image, the catalog-level list:

```yaml
follow_ups:
  - >-
    add a v1.1 channel once its packs are published for the new host id
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
| Pin an rc image that is not pushed yet | `channel: pinned`, its `tag:`, `digest: null`, `pending: "<who pins it, when>"`, `sync`; later `bump --only KEY` |
| Move a pinned row to its release tag | `bump --only KEY --tag NEWTAG` (see [Releasing](#releasing-what-verify---release-requires)) |
| Check a catalog can ship | `tools/images.sh verify --release` |
| Add an image the repo did not reference before | add an `images:` row **and** a `consumers:` binding, then `sync` |
| Check what is stale | `tools/images.sh report` (online) |
| Check nobody hand-edited a generated file | `tools/images.sh verify` (no network) |

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

All three meet in the dashboard. How `make dashboard` layers the generated
files, which images the backend hands to generated stacks, and why the
backend needs a credentials mount to pull anything at all is in
[How the dashboard gets its images](dashboard-images.md).

**1. Local development overrides.** `make dashboard` defaults to the generated tag-only development image set. A matching locally built tag wins, while an absent tag is pulled from the registry. Shell/.env image overrides still take precedence through `tools/load-images-env.sh` — that is how a dashboard backend/frontend built locally from a TEVV-Web-Dashboard branch runs before it is published: `DASHBOARD_BACKEND_IMAGE=local/tevv-web-dashboard-backend:v2-dev` in `./.env`, and `tools/images.sh status` lists it under FYI. `IMAGE_MODE=production make dashboard` selects the immutable digest-pinned artifacts instead.

**2. Baked defaults (none now).** A pin baked into another image cannot be
fixed by `sync`: it needs a rebuild of the image that bakes it. The dashboard
backend used to bake the mns-stacks and authoring refs, so every rebuild of
either forced a backend rebuild. Since TEVV-Web-Dashboard#122 it bakes
nothing and takes `MNS_STACKS_IMAGE`, `MNS_PACKS_IMAGE` and
`MNS_AUTHORING_IMAGE` from the channel env file at run time. No row declares
`bakes:`, and `tools/images.sh baked` would report drift if one ever did.

**3. A locally-present newer image.** Development mode intentionally uses a matching local tag and never pulls merely to check for a newer remote copy. If the tag is absent, `make dashboard` pulls it. Production mode remains digest-pinned and ignores a different local build. After publishing, other developers run `./setup.sh` (or `make pull-images`) to refresh the approved remote set.

## Channels

`channel:` on each row drives `report` and `bump`:

- `review` — `repo:tag-review.N`; `bump` advances N. The tag **must** end in
  `-review.N`; the catalog rejects anything else, because `bump` derives the
  family from the tag and an off-line tag would report `NO_TAGS_FOUND`
  forever while quietly meaning "stale".
- `moving` — a mutable tag (typically `-latest`) republished in place. Tag
  never changes, digest does; reported as `TAG_MOVED`.
- `upstream` — third-party image on a version tag. Never auto-bumped.
- `pinned` — an exact release image (every v1 row), or a branch or preview
  build. Digest required, verified by `report`, refused by `bump`, moved by
  hand; the one exception is a `pending:` row, whose digest `bump --only KEY`
  fills in. The row's purpose says what it is.
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

The production M-S image set is remote-only and digest-pinned. The dashboard’s development mode derives tag-only refs from that same catalog, allowing a local build to win without adding `local/...` repository names.

Use locally available development tags and pull only missing ones:

```bash
make ensure-images
# automatically performed by:
make dashboard
```

Explicitly refresh every approved production pin:

```bash
./tools/pull-all-images.sh                  # or: make pull-images
./tools/pull-all-images.sh --development    # explicitly refresh dashboard development tags
# inspect without pulling
./tools/pull-all-images.sh --dry-run
# include every optional catalog image
./tools/pull-all-images.sh --all-catalog
```

To also advance all mutable catalog rows before pulling, use
`./tools/pull-all-images.sh --refresh-moving`. This updates the authored
catalog and generated pin files, so review and commit those changes. Immutable
v1 release rows only advance through an explicit coordinated release.
