#!/usr/bin/env bash
# mns-offline-export.sh — run on an ONLINE machine, inside a clone of
# M-S-Simulation-Runtime-Stack, to build a transferable bundle for an
# air-gapped host.
#
# Needs: docker login (private dhdevspace/auto_mns), GH_TOKEN with read access
# to DinoHub/TEVV-Airsim (the pack releases are private), python3 + pyyaml.
#
#   tools/offline-export.sh /mnt/usb/mns-bundle
#   tools/offline-export.sh /mnt/usb/mns-bundle --all-catalog
#
# The default set is the 15 images the product path needs (dashboard, product
# shell, ScenarioLab, generator, runtime host, bridge, SITL, QGC). Add
# --all-catalog for the other 42: monitoring, metrics, logs, and the legacy
# per-world simulators behind compose/<scenario>/.
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[[ -f "$ROOT/images/catalog.yaml" ]] || { echo "ERROR: tools/offline-*.sh must stay inside the repo checkout." >&2; exit 1; }
usage() {
  echo "Usage: tools/offline-export.sh <output-dir> [--all-catalog]"
  echo
  echo "  <output-dir>    where the bundle is written (created if absent, reused if present)"
  echo "  --all-catalog   also export monitoring, metrics, logs and legacy scenario images"
  echo
  echo "Env: GH_TOKEN (pack releases), MNS_TARGET_PY (default 3.12), MNS_DEMO_PACKS,"
  echo "     MNS_DEMO_PACK_LOCK"
}

# Parse flags in any position: an unrecognised argument is an error rather than
# something silently ignored, so a typo cannot start a 30 GB pull by surprise.
OUT=""
ALL_CATALOG=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --all-catalog) ALL_CATALOG=true ;;
    -h|--help) usage; exit 0 ;;
    -*) echo "ERROR: unknown option: $1" >&2; usage >&2; exit 2 ;;
    *) [[ -z "$OUT" ]] || { echo "ERROR: more than one output directory given: $OUT, $1" >&2; exit 2; }
       OUT="$1" ;;
  esac
  shift
done
[[ -n "$OUT" ]] || { usage >&2; exit 2; }
PACK_SELECTIONS="${MNS_DEMO_PACKS:---all}"

mkdir -p "$OUT/images" "$OUT/pack-cache" "$OUT/wheels"

# ---------------------------------------------------------------- images ----
# Pull by exact digest (integrity is established HERE, on the online side),
# then save by tag: `docker load` restores tags but never RepoDigests, so the
# tag is the only handle the offline host will have.
echo "==> enumerating catalog refs"
refs_args=()
$ALL_CATALOG && refs_args+=(--all-catalog)
mapfile -t prod_refs < <("$ROOT/tools/images.sh" refs "${refs_args[@]}")
mapfile -t dev_refs  < <("$ROOT/tools/images.sh" refs --channel standalone_v2_ue582 --development)

declare -A want=()
for ref in "${prod_refs[@]}" "${dev_refs[@]}"; do
  [[ -n "$ref" ]] && want["$ref"]=1
done

# tools/stage-authoring-packs.sh seeds ScenarioLab's PackLibrary with
# mns_vehicle_models and scenario_runtime_basic from the *v1* authoring image,
# because neither standalone-v2 authoring image ships default asset packs and
# the editor cannot add a drone without the vehicle models. It reads that image
# straight out of product-images.env, so it is in no channel's ref list and was
# missing from this bundle until now — `make dashboard` then tried to pull it.
seed_ref="$(grep -E '^MNS_AUTHORING_IMAGE=' "$ROOT/product-images.env" | tail -1 | cut -d= -f2-)"
if [[ -n "$seed_ref" ]]; then
  want["$seed_ref"]=1
  echo "    + ${seed_ref%%@*} (ScenarioLab default asset packs)"
fi
echo "    ${#want[@]} distinct refs (production pins + ue582 development tags)"

echo "==> pulling"
for ref in "${!want[@]}"; do
  ok=false
  for attempt in 1 2 3; do
    docker pull "$ref" && { ok=true; break; }
    [[ $attempt == 3 ]] || sleep $((attempt * 5))
  done
  $ok || { echo "ERROR: could not pull $ref" >&2; exit 1; }
done

echo "==> resolving refs to image IDs"
# Save by image ID, never by tag. Two reasons, both verified on this host:
#   1. `docker pull repo:tag@sha256:...` does NOT create the local tag — the
#      image carries only a RepoDigest — so `docker save repo:tag` fails with
#      "reference does not exist" for every pinned-only ref.
#   2. Where a pinned ref and a development ref share one tag string (the
#      mutable -latest dashboard images), the unpinned pull moves that tag onto
#      a newer build, so saving by tag would ship an image the catalog never
#      approved.
# The intended tags travel in the manifest and are applied on import instead.
declare -A id_of=()          # ref  -> image id
declare -A tag_owner=()      # tag  -> ref that should own it (pinned wins)
for ref in "${!want[@]}"; do
  id="$(docker image inspect "$ref" --format '{{.Id}}')" \
    || { echo "ERROR: $ref did not resolve after pull" >&2; exit 1; }
  id_of["$ref"]="$id"
  tag="${ref%%@*}"
  if [[ -z "${tag_owner[$tag]:-}" || "$ref" == *@sha256:* ]]; then
    tag_owner["$tag"]="$ref"
  fi
done

echo "==> saving (one tar per distinct image, so a partial transfer is resumable)"
: > "$OUT/images/manifest.tsv"
declare -A saved=()
for tag in "${!tag_owner[@]}"; do
  ref="${tag_owner[$tag]}"
  id="${id_of[$ref]}"
  short="${id#sha256:}"; short="${short:0:12}"
  file="$short.tar"
  if [[ -z "${saved[$id]:-}" ]]; then
    echo "    $tag  ($short)"
    [[ -f "$OUT/images/$file" ]] || docker save -o "$OUT/images/$file" "$id"
    saved["$id"]=1
  else
    echo "    $tag  ($short, shares a tar)"
  fi
  printf '%s\t%s\t%s\t%s\n' "$file" "$id" "$tag" "$ref" >> "$OUT/images/manifest.tsv"
done
echo "    ${#saved[@]} tar(s) for ${#tag_owner[@]} tag(s)"

# Checksum every tar. `tar -tf` proves an archive is listable, not that its
# bytes survived the trip: a tar truncated or flipped inside a layer blob still
# lists cleanly and then fails at `docker load` on the target. The packs have
# carried checksums from the start (they come from the lock); the images had
# none until this.
echo "==> checksumming tars"
( cd "$OUT/images" && sha256sum *.tar > checksums.sha256 )
echo "    $(wc -l < "$OUT/images/checksums.sha256") checksum(s)"

# ----------------------------------------------------------------- packs ----
# The installer reuses any archive in its cache whose size and SHA-256 match
# the lock, with no token and no network. It never writes that cache itself,
# so fetch the release assets here.
echo "==> downloading content packs"
LOCK="${MNS_DEMO_PACK_LOCK:-$ROOT/packs/standalone-v2-ue582.lock.json}"
GH_TOKEN="${GH_TOKEN:-${GITHUB_TOKEN:-$(command -v gh >/dev/null && gh auth token 2>/dev/null || true)}}"
[[ -n "$GH_TOKEN" ]] || { echo "ERROR: set GH_TOKEN — the pack releases are private and answer 404 anonymously." >&2; exit 1; }

GH_TOKEN="$GH_TOKEN" LOCK="$LOCK" OUT="$OUT" SELECTIONS="$PACK_SELECTIONS" python3 - <<'PY'
import json, os, subprocess, hashlib, sys
from pathlib import Path
from urllib.parse import quote

lock = json.load(open(os.environ["LOCK"], encoding="utf-8"))
cache = Path(os.environ["OUT"]) / "pack-cache"
sel = os.environ["SELECTIONS"].split()
token = os.environ["GH_TOKEN"]

def wanted(p):
    if "--all" in sel: return True
    if "--objects" in sel and p["kind"] == "asset": return True
    return f"--{p['selection']}" in sel

def curl(url, accept, out=None):
    cmd = ["curl", "--fail", "--silent", "--show-error", "--location", "--retry", "3", "--config", "-"]
    if out: cmd += ["--output", str(out)]
    r = subprocess.run(cmd + [url], input=f'header = "Authorization: Bearer {token}"\nheader = "Accept: {accept}"\n',
                       text=True, capture_output=not out)
    if r.returncode: sys.exit(f"curl failed for {url}: {r.stderr if r.stderr else ''}")
    return r.stdout

def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""): h.update(chunk)
    return h.hexdigest()

total = 0
for pack in lock["packs"]:
    if not wanted(pack): continue
    target = cache / pack["asset_name"]
    if target.is_file() and target.stat().st_size == pack["size_bytes"] and sha256(target) == pack["sha256"]:
        print(f"    cached  {pack['asset_name']}"); total += pack["size_bytes"]; continue
    rel = pack["release"]
    meta = json.loads(curl(f"https://api.github.com/repos/{rel['repository']}/releases/tags/{quote(rel['tag'], safe='')}",
                           "application/vnd.github+json"))
    asset = next((a for a in meta.get("assets") or [] if a["name"] == pack["asset_name"]), None)
    if not asset: sys.exit(f"{rel['repository']}@{rel['tag']} has no asset {pack['asset_name']}")
    print(f"    fetch   {pack['asset_name']} ({pack['size_bytes']/1e9:.2f} GB)")
    curl(asset["url"], "application/octet-stream", out=target)
    if target.stat().st_size != pack["size_bytes"] or sha256(target) != pack["sha256"]:
        sys.exit(f"checksum mismatch for {pack['asset_name']}")
    total += pack["size_bytes"]
print(f"    {total/1e9:.2f} GB of packs staged")
PY

# ------------------------------------------------------- python + tarball ----
# `pip download` with no target flags resolves wheels for the interpreter it is
# running under, which is this host's, not the offline machine's. Ubuntu 24.04
# LTS ships Python 3.12; a bundle built on a 3.13/3.14 host would carry cp313/
# cp314 binary wheels (pyyaml, markupsafe) that cannot install there. Pin the
# target explicitly instead.
MNS_TARGET_PY="${MNS_TARGET_PY:-3.12}"
echo "==> downloading python wheels for Python $MNS_TARGET_PY (MNS_TARGET_PY to change)"
pip download -d "$OUT/wheels" -r "$ROOT/tools/requirements.txt" \
  --python-version "$MNS_TARGET_PY" --only-binary=:all: \
  --platform manylinux2014_x86_64 >/dev/null
echo "    $(ls -1 "$OUT/wheels" | wc -l) wheel(s)"

cp "$LOCK" "$OUT/pack-cache/.lock.json"
du -sh "$OUT"
echo
echo "Bundle ready: $OUT"
echo "Copy it plus a git clone of this repo to the offline host, then run tools/offline-import.sh."
