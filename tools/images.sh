#!/usr/bin/env bash
# tools/images.sh — one canonical image catalog: images/catalog.yaml is the
# single authored source; everything else (product-images.env,
# images/image-set.generated.yaml, images/platform-images.generated.env,
# images/standalone-v2-images.generated.env,
# images/legacy-images.generated.env) is generated and committed. See
# docs/adr/0002-one-image-catalog.md.
#
#   tools/images.sh status          # START HERE: one prioritized "what needs you"
#                                    # list, merging verify + report + baked +
#                                    # .env overrides + per-row follow_up notes.
#                                    # --offline skips every registry lookup.
#   tools/images.sh sync            # regenerate all artifacts (offline)
#   tools/images.sh verify          # CI gate: selftest + regenerate + diff, exit 1 on
#                                    # drift or a selftest failure (offline)
#   tools/images.sh refs            # exact active-product refs; --all-catalog for every row
#   tools/pull-all-images.sh       # pull exact active refs with retries; --all-catalog expands scope
#   tools/images.sh report          # pinned vs latest on Hub / upstream registries (online)
#   tools/images.sh bump [--only KEY] [--channel review|moving]
#   tools/images.sh drift           # regenerate committed ScenarioSpecs with the
#                                    # LATEST generator into a tmp dir, diff vs generated/
#   tools/images.sh baked           # assert the released dashboard-backend image's
#                                    # baked authoring/generator refs match the catalog
#   tools/images.sh selftest        # regression guard on synthetic fixtures (offline,
#                                    # no real catalog/network involved); `verify` always
#                                    # runs this first, so it rarely needs invoking directly
#
# sync/verify/report/bump are implemented in tools/images.py (YAML-heavy,
# needs pyyaml). drift/baked stay here — they were already bash+docker in
# tools/check-image-pins.sh (now a thin deprecated shim onto this script) and
# porting the docker-run/diff plumbing to Python bought nothing.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PYTHON:-python3}"
MODE="${1:-}"
[[ $# -gt 0 ]] && shift || true

case "$MODE" in
  sync|verify|report|bump|selftest|status|refs)
    exec "$PY" "$ROOT/tools/images.py" "$MODE" "$@"
    ;;
  drift)
    ;;
  baked)
    ;;
  *)
    echo "usage: $0 <status|sync|verify|report|bump|drift|baked|selftest|refs> [args...]" >&2
    exit 2
    ;;
esac

# --- drift: regenerate committed ScenarioSpecs with the LATEST generator ---
# image into a tmp dir and diff against generated/. Ported from
# check-image-pins.sh --drift, sourcing the generator pin from the (generated)
# product-images.env instead of grepping it directly — same file, same format.
if [[ "$MODE" == "drift" ]]; then
    set -a
    # shellcheck disable=SC1091
    . "$ROOT/product-images.env"
    set +a
    gen_ref="${MNS_STACK_GENERATOR_IMAGE:?MNS_STACK_GENERATOR_IMAGE not set — run tools/images.sh sync}"
    docker pull -q "$gen_ref" >/dev/null
    tmp=$(mktemp -d)
    # generator output is root-owned; clean up with the same privileges
    trap 'docker run --rm -v "$tmp:/t" alpine sh -c "rm -rf /t/*" >/dev/null 2>&1; rmdir "$tmp" 2>/dev/null' EXIT
    any=0
    for spec in "$ROOT"/scenarios/*/ScenarioSpec.yaml; do
        s=$(basename "$(dirname "$spec")")
        [[ -d "$ROOT/generated/$s" ]] || continue   # only diff stacks that exist
        # MNS_IMAGE_SET_FILE is the HOST path here (not /workspace/... as in
        # product.sh): this runs the generator directly from the host with an
        # identical-path mount (-v "$ROOT:$ROOT" above), so the same string
        # that's valid on the host is also what the generator container sees.
        # Contrast product.sh's run_shell, which mounts the workspace at a
        # fixed container path and needs launcher.py's HOST_WORKSPACE_ROOT
        # translation instead — see the comment there.
        docker run --rm -v "$ROOT:$ROOT" -v "$tmp:$tmp" -w "$ROOT" -e MNS_IMAGE_SET=published \
            -e "MNS_IMAGE_SET_FILE=$ROOT/images/image-set.generated.yaml" \
            "$gen_ref" generate "scenarios/$s/ScenarioSpec.yaml" --profile docker \
            --out "$tmp/$s" >/dev/null 2>&1 || { echo "$s: GENERATION FAILED with $gen_ref"; any=1; continue; }
        # the generator bakes the --out path into manifests — normalize it so
        # only real content differences survive the diff
        docker run --rm -v "$tmp:$tmp" alpine sh -c \
            "grep -rl '$tmp/$s' '$tmp/$s' 2>/dev/null | while read -r f; do sed -i 's|$tmp/$s|$ROOT/generated/$s|g' \"\$f\"; done"
        # outputs/ is runtime state; .env carries local overrides (pull policy)
        if d=$(diff -r -q -x outputs -x .env "$ROOT/generated/$s" "$tmp/$s" 2>/dev/null); [[ -n "$d" ]]; then
            echo "== $s drifts against $gen_ref:"
            echo "$d" | sed 's/^/   /'
            any=1
        else
            echo "== $s: no drift"
        fi
    done
    [[ $any == 1 ]] && { echo "drift found — review, then: tools/images.sh bump --only mns_stack_generator && tools/images.sh sync && regenerate"; exit 1; }
    exit 0
fi

# --- baked: assert the released dashboard-backend's baked authoring/ ---
# generator refs match the pins in images/catalog.yaml. Driven by the
# catalog's `bakes:` edge on dashboard_backend (tools/images.py baked-pins),
# not a hardcoded var-name pair — this is the CI-assert form of the old
# script's advisory baked_pins_check().
if [[ "$MODE" == "baked" ]]; then
    backend_ref=$("$PY" "$ROOT/tools/images.py" resolve-var DASHBOARD_BACKEND_IMAGE)
    pins="$("$PY" "$ROOT/tools/images.py" baked-pins dashboard_backend)"
    if [[ -z "$pins" ]]; then
        echo "no bakes: declared on dashboard_backend in images/catalog.yaml — nothing to check"
        exit 0
    fi

    if ! cfg=$(docker buildx imagetools inspect "$backend_ref" --format '{{json .Image}}' 2>&1); then
        # Not silently OK: an unreadable image is an unanswered question.
        printf '%-28s %s\n' 'BAKED_UNREADABLE' "cannot inspect $backend_ref"
        printf '%s\n' "$cfg" | sed 's/^/    /' >&2
        exit 1
    fi

    bad=0
    while IFS=$'\t' read -r var want; do
        [[ -z "$var" ]] && continue
        got=$(printf '%s' "$cfg" | var="$var" python3 -c \
            'import json,os,sys;e=json.load(sys.stdin)["config"].get("Env") or [];k=os.environ["var"]+"=";print(next((v[len(k):] for v in e if v.startswith(k)),""))')
        if [[ -z "$got" ]]; then
            printf '%-36s %s\n' "$var" 'BAKED_EMPTY'
            bad=1
        elif [[ "$got" != "$want" ]]; then
            printf '%-36s %s\n' "$var" 'BAKED_STALE'
            printf '    baked:  %s\n' "$got"
            printf '    pinned: %s\n' "$want"
            bad=1
        else
            printf '%-36s %s\n' "$var" 'ok'
        fi
    done <<<"$pins"

    if [[ $bad == 1 ]]; then
        echo
        echo "BAKED_*: the released dashboard backend carries authoring/generator" >&2
        echo "references that are empty or no longer match images/catalog.yaml. Compose" >&2
        echo "deployments override them at runtime and are unaffected; an image-only" >&2
        echo "deploy is not. tools/images.sh bump cannot fix this — it needs a backend" >&2
        echo "rebuild, then tools/images.sh bump --only dashboard_backend to pick up" >&2
        echo "the new backend digest." >&2
        exit 1
    fi
    exit 0
fi
