#!/usr/bin/env bash
# tools/offline-verify.sh — check an export bundle WITHOUT loading anything.
#
# Answers the three questions that decide whether the offline host will start:
#   1. does the bundle carry every ref the catalog declares?
#   2. does each tar really contain the image id the manifest claims?
#   3. does every image ref the generated ./.env would use have a tag here?
#
#   tools/offline-verify.sh /mnt/usb/mns-bundle
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUNDLE="${1:?usage: $0 <bundle-dir>}"
MANIFEST="$BUNDLE/images/manifest.tsv"
[[ -f "$MANIFEST" ]] || { echo "ERROR: no manifest at $MANIFEST" >&2; exit 1; }

fail=0

echo "== 1. catalog coverage"
mapfile -t declared < <( { "$ROOT/tools/images.sh" refs
                           "$ROOT/tools/images.sh" refs --channel standalone_v2_ue582 --development; } | awk 'NF' | sort -u )
mapfile -t declared_tags < <(printf '%s\n' "${declared[@]}" | sed 's/@.*//' | sort -u)
for tag in "${declared_tags[@]}"; do
  if cut -f3 "$MANIFEST" | grep -qxF "$tag"; then
    printf '   ok      %s\n' "$tag"
  else
    printf '   MISSING %s\n' "$tag"; fail=1
  fi
done

echo
echo "== 2. tar contents match the manifest ids"
# Read the id out of the tar's own metadata rather than trusting the filename.
while IFS=$'\t' read -r file id tag ref; do
  [[ -n "$file" ]] || continue
  path="$BUNDLE/images/$file"
  [[ -f "$path" ]] || { printf '   MISSING %s (for %s)\n' "$file" "$tag"; fail=1; continue; }
  key="$(printf '%s' "$id" | sed 's/^sha256://')"
  # Capture the listing first. `tar -tf ... | grep -q` looks right but is not:
  # grep exits on the first match, tar takes SIGPIPE, and under `set -o
  # pipefail` the pipeline reports 141 — so a large tar whose config blob
  # sorts early fails a check it actually passes.
  listing="$(tar -tf "$path" 2>/dev/null || true)"
  if grep -qE "^(blobs/sha256/)?$key(\.json)?$" <<<"$listing"; then
    printf '   ok      %-12s %s\n' "${key:0:12}" "$tag"
  else
    printf '   SUSPECT %-12s %s (id not found inside the tar)\n' "${key:0:12}" "$tag"; fail=1
  fi
done < "$MANIFEST"

echo
echo "== 2b. tar bytes match their checksums"
if [[ -f "$BUNDLE/images/checksums.sha256" ]]; then
  if ( cd "$BUNDLE/images" && sha256sum --quiet -c checksums.sha256 ) ; then
    echo "   ok      all $(wc -l < "$BUNDLE/images/checksums.sha256") tar(s) intact"
  else
    echo "   MISMATCH — the lines above name the corrupted tar(s); recopy them"
    fail=1
  fi
else
  echo "   (no checksums.sha256 — bundle predates it; re-run tools/offline-export.sh to add one)"
fi

echo
echo "== 3. which image vars ./.env will carry (absent ones are commented out, not an error)"
# Same digest-stripping rule tools/offline-import.sh applies.
python3 - "$ROOT" "$MANIFEST" <<'PY'
import re, sys
from pathlib import Path
root, manifest = Path(sys.argv[1]), Path(sys.argv[2])
tags = {l.split("\t")[2] for l in manifest.read_text().splitlines() if l.strip()}
sources = ["images/standalone-v2-ue582.generated.env",
           "images/standalone-v2-development.generated.env",
           "product-images.env", "images/platform-images.generated.env",
           "images/legacy-images.generated.env"]
seen, bad = {}, 0
for name in sources:
    path = root / name
    if not path.is_file(): continue
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.+)$", line)
        if not m: continue
        k, v = m.group(1), m.group(2).strip()
        if k in seen or "@sha256:" not in v: continue
        seen[k] = v.split("@sha256:")[0]
for k, v in sorted(seen.items()):
    if v in tags:
        print(f"   ok      {k}")
    else:
        print(f"   skip    {k}={v}")
        bad += 1
print(f"\n   {len(seen)-bad} var(s) written to .env, {bad} commented out (re-export with --all-catalog to include them)")
PY

echo
echo "== 4. content packs"
if [[ -d "$BUNDLE/pack-cache" ]]; then
  if python3 - "$BUNDLE/pack-cache" "$ROOT/packs/standalone-v2-ue582.lock.json" <<'PY'
import hashlib, json, sys
from pathlib import Path
cache, lock = Path(sys.argv[1]), json.load(open(sys.argv[2], encoding="utf-8"))
ok = bad = 0
for pack in lock["packs"]:
    f = cache / pack["asset_name"]
    if not f.is_file():
        print(f"   absent  {pack['asset_name']}"); continue
    if f.stat().st_size != pack["size_bytes"]:
        print(f"   SIZE    {pack['asset_name']}"); bad += 1; continue
    h = hashlib.sha256()
    with open(f, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""): h.update(chunk)
    if h.hexdigest() == pack["sha256"]:
        print(f"   ok      {pack['asset_name']}"); ok += 1
    else:
        print(f"   SHA256  {pack['asset_name']}"); bad += 1
print(f"\n   {ok} pack(s) verified, {bad} corrupt")
sys.exit(1 if bad else 0)
PY
  then :; else fail=1; fi
else
  echo "   (no pack-cache in this bundle)"
fi

echo
[[ $fail -eq 0 ]] && echo "BUNDLE OK" || { echo "BUNDLE INCOMPLETE — see the lines above"; exit 1; }
