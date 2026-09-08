# packs/

The standalone-v2 content that `make dashboard` and `./product.sh` install, and
the one contract every pack is checked against.

| File | What it is | Consumers |
|---|---|---|
| `standalone-v2-review.1.lock.json` | The release's seven demo packs (four `.mnslevelpack`, three `.mnsassetpack`) with sizes, SHA-256s and artifact digests, plus the image pins the installer needs before the catalog is resolved. `images/catalog.yaml` `consumers.pack_locks` declares which catalog rows those pins mirror; `tools/images.sh verify` fails when they drift. | `tools/install-demo-packs.sh`, CI |
| `runtime-host-compatibility.json` | The frozen host capability contract baked into the pinned `tevv_runtime_host` image (`/app/TEVVRuntimeHost/TEVVRuntimeHost/Content/TEVVHost/host-compatibility.json`). Its `id` is the lock's `capability_id`. | the generator run started by the dashboard backend and by `./product.sh` (`MNS_RUNTIME_HOST_COMPATIBILITY_CONTRACT`) |

## Why the contract is checked in

The stack generator resolves a ScenarioSpec's `environment` (id, version,
artifact_digest) from the local pack store, selects the pack variant cooked for
the runtime host's capability id, and validates that variant against the host
contract document. The published generator image reads that document from
`MNS_RUNTIME_HOST_COMPATIBILITY_CONTRACT` and ships no default copy, so
without this file every v2 generate stopped with
`host compatibility document does not exist`. The runtime host image is the
authority; this file is a copy so the contract can be handed to a container
that never sees that image.

`tools/install-demo-packs.sh` refuses a lock whose `capability_id` differs from
this file's `id`: packs cooked for one host id never mount on another.

## Refreshing after a runtime-host bump

When `images/catalog.yaml`'s `tevv_runtime_host` row moves (a new engine build,
a new capability id), extract the contract from the new image and regenerate the
pack lock against packs cooked for that id:

```bash
. images/standalone-v2-images.generated.env
docker run --rm --entrypoint cat "$MNS_RUNTIME_HOST_IMAGE" \
  /app/TEVVRuntimeHost/TEVVRuntimeHost/Content/TEVVHost/host-compatibility.json \
  > packs/runtime-host-compatibility.json
python3 -c 'import json; print(json.load(open("packs/runtime-host-compatibility.json"))["id"])'
```

A pack version is immutable: packs built for the old id need new versions and a
new lock (see MnS-Integration-Platform `docs/scenario-platform/level-pack-v2-workflow.md`).
