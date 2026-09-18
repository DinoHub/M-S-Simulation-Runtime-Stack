#!/usr/bin/env bash
# tools/offline-import.sh — run on the OFFLINE Ubuntu 24.04 host, inside the
# same repo checkout, against the bundle produced by mns-offline-export.sh.
#
#   tools/offline-import.sh /mnt/usb/mns-bundle
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[[ -f "$ROOT/images/catalog.yaml" ]] || { echo "ERROR: tools/offline-*.sh must stay inside the repo checkout." >&2; exit 1; }
BUNDLE="${1:?usage: $0 <bundle-dir>}"
[[ -d "$BUNDLE/images" ]] || { echo "ERROR: $BUNDLE does not look like an export bundle." >&2; exit 1; }

# ---------------------------------------------------------------- images ----
echo "==> loading images"
# The bundle stores images by ID (see the note in tools/offline-export.sh), so
# the tars carry no tags. Load each distinct tar once, then apply the tags the
# manifest records — that is what ./.env and tools/ensure-images.sh look up.
MANIFEST="$BUNDLE/images/manifest.tsv"
[[ -f "$MANIFEST" ]] || { echo "ERROR: $MANIFEST is missing — re-export the bundle." >&2; exit 1; }

# Tag what `docker load` says it loaded, rather than the id the manifest
# records. The two are not always the same string: with the containerd image
# store a daemon reports an image's MANIFEST digest as its id, while the classic
# overlay2 store reports the CONFIG digest. The bytes are identical either way,
# but `docker image inspect <config-digest>` finds nothing on a containerd-store
# host, which is what "did not yield sha256:..." used to mean.
declare -A loaded=()
while IFS=$'\t' read -r file id tag ref; do
  [[ -n "$file" ]] || continue
  if [[ -z "${loaded[$file]:-}" ]]; then
    [[ -f "$BUNDLE/images/$file" ]] || { echo "ERROR: $file listed in the manifest but absent from the bundle." >&2; exit 1; }
    if ! out="$(docker load -i "$BUNDLE/images/$file" 2>&1)"; then
      echo "ERROR: docker load failed for $file" >&2
      printf '%s\n' "$out" | sed 's/^/    /' >&2
      exit 1
    fi
    # "Loaded image ID: sha256:..." (no tags in the archive) or
    # "Loaded image: repo:tag" (archive carried RepoTags).
    handle="$(printf '%s\n' "$out" \
      | sed -n 's/^Loaded image ID: //p; s/^Loaded image: //p' | head -1)"
    if [[ -z "$handle" ]]; then
      # Fall back to the manifest id; some daemons print nothing useful.
      docker image inspect "$id" >/dev/null 2>&1 && handle="$id"
    fi
    if [[ -z "$handle" ]]; then
      echo "ERROR: $file loaded but the daemon named no image. Its output was:" >&2
      printf '%s\n' "$out" | sed 's/^/    /' >&2
      exit 1
    fi
    loaded["$file"]="$handle"
  fi
  docker tag "${loaded[$file]}" "$tag"
  docker image inspect "$tag" >/dev/null 2>&1 \
    || { echo "ERROR: tagged $tag from ${loaded[$file]} but it does not resolve" >&2; exit 1; }
  echo "    $tag"
done < "$MANIFEST"
echo "    ${#loaded[@]} tar(s) loaded"

# ------------------------------------------------------------------ .env ----
# docker load restores tags but NOT RepoDigests, so every `repo:tag@sha256:...`
# ref in the generated env files is unresolvable here. ./.env outranks all of
# them (tools/load-images-env.sh: shell > ./.env > generated file), so write
# the same refs with the digest stripped. Integrity was established on the
# online host, which pulled each of these by digest.
echo "==> writing ./.env image overrides (digest-stripped, ue582 channel)"

python3 - "$ROOT" "$MANIFEST" <<'PY'
import re, sys, time
from pathlib import Path

root, manifest = Path(sys.argv[1]), Path(sys.argv[2])
# Only name images this bundle actually carries. Writing a key for an image
# that was never exported (the monitoring/metrics/logs/legacy stacks ship only
# with --all-catalog) would turn a clear "no such image" into a confusing
# pull attempt on a host with no network.
bundled = {l.split("\t")[2] for l in manifest.read_text().splitlines() if l.strip()}
# First file to define a key wins: the ue582 channel is the default, so its
# refs must outrank the v1 product-images.env values for the same key names.
sources = [
    "images/standalone-v2-ue582.generated.env",
    "images/standalone-v2-development.generated.env",
    "product-images.env",
    "images/platform-images.generated.env",
    "images/legacy-images.generated.env",
]
seen, lines = {}, []
for name in sources:
    path = root / name
    if not path.is_file():
        continue
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.+)$", line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        if key in seen or "@sha256:" not in val:
            continue
        seen[key] = val.split("@sha256:")[0]

skipped = {k: v for k, v in seen.items() if v not in bundled}
seen = {k: v for k, v in seen.items() if v in bundled}

BEGIN = "# --- offline image overrides (tools/offline-import.sh) --- BEGIN"
END = "# --- end offline image overrides ---"

existing = (root / ".env").read_text(encoding="utf-8") if (root / ".env").is_file() else ""
# Drop any block a previous import wrote, so re-running is idempotent rather
# than appending a second copy of the header and the two policy keys.
kept, skipping = [], False
for line in existing.splitlines():
    if line.strip() == BEGIN:
        skipping = True
        continue
    if line.strip() == END:
        skipping = False
        continue
    if skipping:
        continue
    m = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=", line)
    if m and m.group(1) in seen:
        continue   # a hand-set value is re-stated inside the block below
    kept.append(line)
while kept and not kept[-1].strip():
    kept.pop()

if kept and (root / ".env").is_file():
    backup = root / f".env.bak.{int(time.time())}"
    backup.write_text(existing, encoding="utf-8")
    print(f"    backed up existing .env to {backup.name}")

out = kept + [
    "",
    BEGIN,
    "# docker load does not restore RepoDigests, so the digest-pinned refs in",
    "# images/*.generated.env cannot resolve on this host. These are the same",
    "# refs with the digest stripped; the online host pulled each by digest.",
    "# Regenerate if you switch CHANNEL away from the default ue582.",
    "MNS_IMAGE_PULL_POLICY=missing",
    "DASHBOARD_PULL_POLICY=missing",
] + [f"{k}={v}" for k, v in sorted(seen.items())] + (
    ["",
     "# Not in this bundle (re-export with --all-catalog if you need them);",
     "# left unset so compose fails with a clear error instead of trying to pull:"]
    + [f"#   {k}={v}" for k, v in sorted(skipped.items())] if skipped else []) + [END]
(root / ".env").write_text("\n".join(out) + "\n", encoding="utf-8")
print(f"    {len(seen)} image vars written to .env"
      + (f"; {len(skipped)} left commented out (not in this bundle)" if skipped else ""))
PY

# ------------------------------------------------- ScenarioLab seed packs ----
# tools/stage-authoring-packs.sh seeds the PackLibrary with mns_vehicle_models
# and scenario_runtime_basic from the v1 authoring image, and resolves that
# image by the digest-pinned ref it greps out of product-images.env. A digest
# never resolves after `docker load`, and ./.env cannot override a value the
# script reads from a file, so on an offline host it goes to the registry and
# `make dashboard` dies. Do the seeding here with the tag instead: the script
# returns early when mns_vehicle_models is already present, so it then never
# needs the image at all.
echo "==> seeding ScenarioLab default asset packs"
SEED_TAG="$(grep -E '^MNS_AUTHORING_IMAGE=' "$ROOT/product-images.env" | tail -1 | cut -d= -f2- | sed 's/@sha256:.*//')"
DATA_ROOT="${MNS_AUTHORING_DATA_ROOT:-$ROOT/.mns/ue582/authoring-data}"
PACK_LIBRARY="$DATA_ROOT/PackLibrary"
MARKER="$PACK_LIBRARY/asset_packs/mns_vehicle_models.mnsassetpack/mns_asset_pack.json"

if [[ -f "$MARKER" ]]; then
  echo "    already seeded"
elif [[ -z "$SEED_TAG" ]]; then
  echo "    WARNING: product-images.env has no MNS_AUTHORING_IMAGE; ScenarioLab will have no vehicle models." >&2
elif ! docker image inspect "$SEED_TAG" >/dev/null 2>&1; then
  echo "    WARNING: $SEED_TAG is not in this bundle." >&2
  echo "             Re-export it, or make dashboard will try to pull it and fail offline." >&2
else
  mkdir -p "$PACK_LIBRARY/asset_packs" "$PACK_LIBRARY/level_packs"
  docker run --rm --user "$(id -u):$(id -g)" --entrypoint sh \
    -v "$PACK_LIBRARY/asset_packs:/out:rw" \
    "$SEED_TAG" -c 'cp -a -n /opt/mns/default-packs/asset_packs/. /out/'
  if [[ -f "$MARKER" ]]; then
    echo "    seeded from $SEED_TAG: $(ls -1 "$PACK_LIBRARY/asset_packs" | tr '\n' ' ')"
  else
    echo "    WARNING: seeding ran but mns_vehicle_models is still absent." >&2
  fi
fi

# ----------------------------------------------------------------- packs ----
echo "==> installing content pack cache"
CACHE="$ROOT/.mns/downloads/pack-cache"
mkdir -p "$CACHE"
cp -n "$BUNDLE"/pack-cache/*.mns*pack "$CACHE"/ 2>/dev/null || true
echo "    $(ls -1 "$CACHE" | wc -l) archive(s) in $CACHE"

# ---------------------------------------------------------------- python ----
# Only PyYAML is actually required here: tools/images.sh and
# tools/install_demo_packs.py need it, and Ubuntu ships it as python3-yaml.
# jinja2 and python-dotenv are used by tools/generate_scenario.py alone, which
# belongs to the legacy compose/<scenario> path, not `make dashboard` — so a
# missing pip is a warning, not a failure.
if [[ -d "$BUNDLE/wheels" && "${MNS_SKIP_WHEELS:-0}" != 1 ]]; then
  echo "==> installing python deps"
  if python3 -c "import yaml, jinja2, dotenv" 2>/dev/null; then
    echo "    already satisfied — nothing to install"
  else
    PIP=""
    for candidate in "python3 -m pip" pip3 pip; do
      # shellcheck disable=SC2086
      if $candidate --version >/dev/null 2>&1; then PIP="$candidate"; break; fi
    done
    if [[ -z "$PIP" ]]; then
      echo "    WARNING: no pip on this host (install python3-pip to get one)." >&2
    else
      # shellcheck disable=SC2086
      $PIP install --no-index --find-links "$BUNDLE/wheels" -r "$ROOT/tools/requirements.txt" 2>/dev/null \
        || { echo "    system python is externally managed — retrying with --break-system-packages"
             $PIP install --break-system-packages --no-index --find-links "$BUNDLE/wheels" \
               -r "$ROOT/tools/requirements.txt" || true; }
    fi
    python3 -c "import yaml" 2>/dev/null \
      || { echo "ERROR: PyYAML is missing and could not be installed. Install python3-yaml." >&2; exit 1; }
    python3 -c "import jinja2, dotenv" 2>/dev/null \
      || echo "    note: jinja2/python-dotenv absent — only tools/generate_scenario.py (legacy ./launch.sh) needs them; make dashboard does not."
  fi
fi

echo
echo "Done. Verify without touching the network:"
echo "  tools/ensure-images.sh --development --channel standalone_v2_ue582 --dry-run   # expect 0 would pull"
echo "  tools/install-demo-packs.sh --check --all                                      # expect all installed"
echo "  tools/images.sh verify                                                         # catalog vs generated files"
echo
echo "(tools/images.sh status --offline also works, but it exits 1 on the catalog's"
echo " pre-existing follow-up notes, so it is not a pass/fail signal here.)"
echo
echo "Then start it — sourcing .env FIRST is required:"
echo "  set -a; . ./.env; set +a"
echo "  make dashboard"
echo
echo "Why: ./.env feeds docker compose, but tools/load-images-env.sh deliberately"
echo "skips exporting any key that ./.env already defines. Python helpers such as"
echo "install_demo_packs.py read the environment, find nothing, and fall back to"
echo "the lock's digest-pinned image — which cannot resolve after docker load and"
echo "sends them to the registry. Sourcing .env puts the tag-only refs in the"
echo "environment, where they outrank everything else."
