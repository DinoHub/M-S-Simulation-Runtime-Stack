#!/usr/bin/env bash
# check-image-pins.sh — report/bump stale image pins in product-images.env,
# and (for the stack generator) show what a bump would change in generated/.
#
#   tools/check-image-pins.sh                 # report: pinned vs latest tag on Hub
#   tools/check-image-pins.sh --bump          # rewrite product-images.env to latest
#   tools/check-image-pins.sh --drift         # regenerate committed specs with the
#                                             # LATEST generator into a tmp dir and
#                                             # diff against generated/ (no writes)
#
# Auth: reads Docker Hub credentials from ~/.docker/config.json (plain "auths"
# entry; docker login first if missing). Only queries the repos already pinned.
# Review tags are ordered by their numeric suffix (-review.N), not push date.
#
# Pins are repo:tag@sha256 (see ADR 0001). Two things can be stale, and the
# report separates them: a NEWER tag exists (STALE), or the tag you pinned has
# been retagged onto a different image (TAG_MOVED). The second was invisible
# while pins were bare tags — the pin would quietly start meaning something
# else with no change to this repository.

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/product-images.env"
MODE="${1:-report}"

latest_tags() {
python3 - "$ENV_FILE" <<'PY'
import json, sys, re, base64, urllib.request

env_file = sys.argv[1]
pins = {}  # var -> (repo, tag, pinned digest or '')
for line in open(env_file):
    m = re.match(r'^([A-Z0-9_]+_IMAGE)=([^:\s]+):([^@\s]+)(?:@(sha256:[0-9a-f]+))?', line)
    if m:
        pins[m.group(1)] = (m.group(2), m.group(3), m.group(4) or '')

cfg = json.load(open(__import__('os').path.expanduser('~/.docker/config.json')))
auth = next((v['auth'] for k, v in cfg.get('auths', {}).items() if 'docker.io' in k), None)
if not auth:
    sys.exit("no docker.io credentials in ~/.docker/config.json — run docker login")
user, pw = base64.b64decode(auth).decode().split(':', 1)
req = urllib.request.Request('https://hub.docker.com/v2/users/login',
    data=json.dumps({'username': user, 'password': pw}).encode(),
    headers={'Content-Type': 'application/json'})
token = json.load(urllib.request.urlopen(req))['token']

def digest_for(repo, tag):
    """Digest the registry currently serves for repo:tag ('' if unknown).

    Hub returns the manifest-list digest, which is what a repo:tag@sha256
    reference resolves against and what `docker manifest inspect -v` reports."""
    try:
        r = urllib.request.Request(
            f'https://hub.docker.com/v2/repositories/{repo}/tags/{tag}',
            headers={'Authorization': f'JWT {token}'})
        return json.load(urllib.request.urlopen(r)).get('digest') or ''
    except Exception:
        return ''


def tags(repo, family):
    out, url = [], f'https://hub.docker.com/v2/repositories/{repo}/tags/?page_size=100&name={family}'
    while url:
        r = urllib.request.Request(url, headers={'Authorization': f'JWT {token}'})
        d = json.load(urllib.request.urlopen(r))
        out += [t['name'] for t in d['results']]
        url = d.get('next')
    return out

def review_num(tag):
    m = re.search(r'-review\.(\d+)$', tag)
    return int(m.group(1)) if m else None

for var, (repo, tag, pinned_digest) in sorted(pins.items()):
    live = digest_for(repo, tag)
    m = re.match(r'^(.*-review)\.\d+$', tag)
    family = m.group(1) if m else None
    candidates = []
    if family:
        candidates = [(review_num(t), t) for t in tags(repo, family)
                      if review_num(t) is not None and t.startswith(family + '.')]
    latest = max(candidates)[1] if candidates else tag

    if not family:
        # A mutable tag (…-latest) never gets a newer *tag* — it is republished
        # in place. So "stale" for these means the digest moved, which is only
        # answerable because the pin carries one. Reporting UNVERSIONED and
        # stopping, as this did, left exactly the images that change most often
        # as the ones nothing could check.
        if not pinned_digest:
            status = 'NO_DIGEST'
        elif live and live != pinned_digest:
            status = 'TAG_MOVED'
        elif not live:
            status = 'NO_TAGS_FOUND'
        else:
            status = 'OK'
    elif not candidates:
        status = 'NO_TAGS_FOUND'
    elif not pinned_digest:
        # A pin without a digest can change meaning without this file changing:
        # exactly what ADR 0001 exists to prevent. Louder than being behind.
        status = 'NO_DIGEST'
    elif live and live != pinned_digest:
        status = 'TAG_MOVED'
    elif latest != tag:
        status = 'STALE'
    else:
        status = 'OK'

    target = digest_for(repo, latest) if latest != tag else live
    print(f'{var}\t{repo}\t{tag}\t{latest}\t{status}\t{target}')
PY
}

report="$(latest_tags)"
printf '%-28s %-38s %-38s %-12s %s\n' 'VAR' 'PINNED' 'LATEST' 'STATUS' 'DIGEST'
while IFS=$'\t' read -r var repo pinned latest status digest; do
    printf '%-28s %-38s %-38s %-12s %s\n' "$var" "$pinned" "$latest" "$status" "${digest:0:19}"
done <<<"$report"

case "$report" in
  *TAG_MOVED*) echo
    echo "TAG_MOVED: a pinned tag now serves a different image. The pin still" >&2
    echo "resolves to the digest you committed, so nothing you run has changed —" >&2
    echo "but the tag beside it is now misleading. Re-pin with --bump." >&2 ;;
esac
case "$report" in
  *NO_DIGEST*) echo
    echo "NO_DIGEST: pinned by tag alone, so the image can change under you" >&2
    echo "without this repository changing. --bump rewrites it with a digest." >&2 ;;
esac

case "$MODE" in
report) exit 0 ;;

--bump)
    changed=0
    while IFS=$'\t' read -r var repo pinned latest status digest; do
        # Rewrite for anything that leaves the pin weaker than "one exact
        # image": behind (STALE), retagged (TAG_MOVED), or tag-only (NO_DIGEST).
        # For a mutable tag, TAG_MOVED is the normal path — same tag, new digest.
        case "$status" in STALE|TAG_MOVED|NO_DIGEST) ;; *) continue ;; esac
        if [[ -z "$digest" ]]; then
            echo "skipped: $var — could not resolve a digest for ${repo}:${latest}" >&2
            continue
        fi
        # The old value may or may not carry a digest; match the whole line.
        sed -i "s|^${var}=.*\$|${var}=${repo}:${latest}@${digest}|" "$ENV_FILE"
        echo "bumped: $var ${pinned} -> ${latest}@${digest:0:19}… ($status)"
        changed=1
    done <<<"$report"
    [[ $changed == 0 ]] && echo "nothing to bump"
    ;;

--drift)
    # Pull the LATEST tag by digest when we have one: --drift must compare
    # against a specific image, not whatever the tag points at this minute.
    gen_latest=$(awk -F'\t' '$1=="MNS_STACK_GENERATOR_IMAGE"{print $2":"$4 ($6=="" ? "" : "@" $6)}' <<<"$report")
    [[ -z "$gen_latest" ]] && { echo "no generator pin found"; exit 1; }
    docker pull -q "$gen_latest" >/dev/null
    tmp=$(mktemp -d)
    # generator output is root-owned; clean up with the same privileges
    trap 'docker run --rm -v "$tmp:/t" alpine sh -c "rm -rf /t/*" >/dev/null 2>&1; rmdir "$tmp" 2>/dev/null' EXIT
    any=0
    for spec in "$ROOT"/scenarios/*/ScenarioSpec.yaml; do
        s=$(basename "$(dirname "$spec")")
        [[ -d "$ROOT/generated/$s" ]] || continue   # only diff stacks that exist
        docker run --rm -v "$ROOT:$ROOT" -v "$tmp:$tmp" -w "$ROOT" -e MNS_IMAGE_SET=published \
            "$gen_latest" generate "scenarios/$s/ScenarioSpec.yaml" --profile docker \
            --out "$tmp/$s" >/dev/null 2>&1 || { echo "$s: GENERATION FAILED with $gen_latest"; any=1; continue; }
        # the generator bakes the --out path into manifests — normalize it so
        # only real content differences survive the diff
        docker run --rm -v "$tmp:$tmp" alpine sh -c \
            "grep -rl '$tmp/$s' '$tmp/$s' 2>/dev/null | while read -r f; do sed -i 's|$tmp/$s|$ROOT/generated/$s|g' \"\$f\"; done"
        # outputs/ is runtime state; .env carries local overrides (pull policy)
        if d=$(diff -r -q -x outputs -x .env "$ROOT/generated/$s" "$tmp/$s" 2>/dev/null); [[ -n "$d" ]]; then
            echo "== $s drifts against $gen_latest:"
            echo "$d" | sed 's/^/   /'
            any=1
        else
            echo "== $s: no drift"
        fi
    done
    [[ $any == 1 ]] && echo "drift found — review, then: tools/check-image-pins.sh --bump && regenerate"
    ;;

*) echo "usage: $0 [--bump|--drift]" >&2; exit 2 ;;
esac
