#!/usr/bin/env bash
# tools/images.sh — one canonical image catalog: images/catalog.yaml is the
# single authored source; everything else (product-images.env,
# images/image-set.generated.yaml, images/image-set.development.generated.yaml,
# images/platform-images.generated.env, images/development.generated.env,
# images/v1.0.0.generated.env) is generated and committed. See
# docs/adr/0002-one-image-catalog.md.
#
#   tools/images.sh status          # START HERE: one prioritized "what needs you"
#                                    # list, merging verify + report + baked +
#                                    # .env overrides + per-row follow_up notes,
#                                    # plus SOURCE_AHEAD for rows that name a
#                                    # `source:` repo — merged code no image
#                                    # carries, which no registry check can see.
#                                    # --offline skips every registry lookup.
#   tools/images.sh sync            # regenerate all artifacts (offline)
#   tools/images.sh verify          # CI gate: selftest + regenerate + diff, exit 1 on
#                                    # drift or a selftest failure (offline)
#   tools/images.sh refs            # exact refs; add --development for local-first tags
#   tools/pull-all-images.sh       # pull exact active refs with retries; --all-catalog expands scope
#   tools/images.sh report          # pinned vs latest on Hub / upstream registries (online)
#   tools/images.sh bump [--only KEY] [--channel review|moving]
#   tools/images.sh drift           # regenerate committed ScenarioSpecs with the
#                                    # pinned mns-stacks into a tmp dir, diff vs generated/
#   tools/images.sh baked           # assert the released dashboard-backend image's
#                                    # baked mns-stacks/authoring refs match the catalog
#   tools/images.sh selftest        # regression guard on synthetic fixtures (offline,
#                                    # no real catalog/network involved); `verify` always
#                                    # runs this first, so it rarely needs invoking directly
#
# sync/verify/report/bump are implemented in tools/images.py (YAML-heavy,
# needs pyyaml). drift/baked stay here: they are docker plumbing (drift runs
# mns-stacks through tools/mns-stacks.sh), and porting that to Python buys
# nothing.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PYTHON:-python3}"
MODE="${1:-}"
[[ $# -gt 0 ]] && shift || true

case "$MODE" in
  sync|verify|report|bump|selftest|status|refs|local-refs)
    # setup.sh, make doctor and pull-all-images.sh reach these subcommands, so
    # this is on the customer path: fail with the fix rather than "python3:
    # command not found". images.py carries the matching pyyaml guard.
    if ! command -v "$PY" >/dev/null 2>&1; then
      echo "ERROR: tools/images.sh $MODE needs Python 3 ('$PY' not found; set \$PYTHON to override)." >&2
      echo "       Install python3, then: pip install -r tools/requirements.txt" >&2
      exit 1
    fi
    exec "$PY" "$ROOT/tools/images.py" "$MODE" "$@"
    ;;
  drift)
    ;;
  baked)
    ;;
  *)
    echo "usage: $0 <status|sync|verify|report|bump|drift|baked|selftest|refs|local-refs> [args...]" >&2
    exit 2
    ;;
esac

# --- drift: regenerate committed ScenarioSpecs with the pinned mns-stacks ---
# into a scratch dir and diff against generated/. Runs `mns-stacks generate`
# through tools/mns-stacks.sh, exactly as make fly and the dashboard do: the
# channel's pack store and runtime host contract, host paths, no socket.
if [[ "$MODE" == "drift" ]]; then
    # The product's precedence, without clobbering the shell: the shell
    # environment, then ./.env (a local override, said out loud), then the
    # channel's generated pin.
    # shellcheck source=tools/load-images-env.sh
    . "$ROOT/tools/load-images-env.sh"
    stacks_ref="${MNS_STACKS_IMAGE:-}"
    if [[ -z "$stacks_ref" ]]; then
        stacks_ref="$(dotenv_value MNS_STACKS_IMAGE "$ROOT/.env")"
        [[ -n "$stacks_ref" ]] && echo "NOTE: MNS_STACKS_IMAGE from ./.env overrides the catalog pin: $stacks_ref" >&2
    fi
    [[ -n "$stacks_ref" ]] || stacks_ref="$(dotenv_value MNS_STACKS_IMAGE "$ROOT/images/v1.0.0.generated.env")"
    [[ -n "$stacks_ref" ]] || { echo "MNS_STACKS_IMAGE not set — run tools/images.sh sync" >&2; exit 1; }
    export MNS_STACKS_IMAGE="$stacks_ref"
    export MNS_IMAGE_SET_FILE="${MNS_IMAGE_SET_FILE:-$ROOT/images/image-set.generated.yaml}"
    # Inside the checkout, so it is already mounted; generation runs as you,
    # so plain rm cleans it up.
    tmp="$ROOT/.mns/drift"
    rm -rf "$tmp"; mkdir -p "$tmp"
    trap 'rm -rf "$tmp"' EXIT
    any=0
    for spec in "$ROOT"/scenarios/*/ScenarioSpec.yaml; do
        s=$(basename "$(dirname "$spec")")
        [[ -d "$ROOT/generated/$s" ]] || continue   # only diff stacks that exist
        # The v1 image set ships ONE generic simulator (tevv_runtime_host) and
        # resolves the world from a level pack named by environment.version +
        # environment.artifact_digest. A spec without the digest names a
        # per-world simulator that no longer exists, so it cannot generate.
        # Say that, rather than a bare "GENERATION FAILED" that reads like a
        # broken mns-stacks pin.
        if ! "$PY" -c '
import sys, yaml
spec = yaml.safe_load(open(sys.argv[1], encoding="utf-8")) or {}
env = spec.get("environment") or {}
sys.exit(0 if env.get("artifact_digest") else 1)
' "$spec"; then
            echo "== $s: SKIPPED (environment has no artifact_digest, so it cannot"
            echo "   select a level pack. Re-author it in ScenarioLab.)"
            continue
        fi
        "$ROOT/tools/mns-stacks.sh" generate "$ROOT/scenarios/$s" --profile docker \
            --out "$tmp/$s" >/dev/null 2>&1 || { echo "$s: GENERATION FAILED with $stacks_ref"; any=1; continue; }
        # mns-stacks writes the --out path into manifests: normalize it so
        # only real content differences survive the diff
        grep -rl "$tmp/$s" "$tmp/$s" 2>/dev/null | while read -r f; do
            sed -i "s|$tmp/$s|$ROOT/generated/$s|g" "$f"
        done
        # outputs/ is runtime state; .env carries local overrides (pull policy)
        if d=$(diff -r -q -x outputs -x .env "$ROOT/generated/$s" "$tmp/$s" 2>/dev/null); [[ -n "$d" ]]; then
            echo "== $s drifts against $stacks_ref:"
            echo "$d" | sed 's/^/   /'
            any=1
        else
            echo "== $s: no drift"
        fi
    done
    [[ $any == 1 ]] && { echo "drift found — review, then regenerate the stack (make fly SCENARIO=<name>, or the dashboard's Generate)"; exit 1; }
    exit 0
fi

# --- baked: assert the released dashboard-backend's baked mns-stacks/ ---
# authoring refs match the pins in images/catalog.yaml. Driven by the
# catalog's `bakes:` edge on v1_dashboard_backend (tools/images.py baked-pins),
# not a hardcoded var-name pair — this is the CI-assert form of the old
# script's advisory baked_pins_check().
if [[ "$MODE" == "baked" ]]; then
    pins="$("$PY" "$ROOT/tools/images.py" baked-pins v1_dashboard_backend)"
    if [[ -z "$pins" ]]; then
        echo "no bakes: declared on v1_dashboard_backend in images/catalog.yaml — nothing to check"
        exit 0
    fi
    backend_ref=$(sed -n 's/^DASHBOARD_BACKEND_IMAGE=//p' "$ROOT/images/v1.0.0.generated.env")

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
        echo "BAKED_*: the released dashboard backend carries mns-stacks/authoring" >&2
        echo "references that are empty or no longer match images/catalog.yaml. Compose" >&2
        echo "deployments override them at runtime and are unaffected; an image-only" >&2
        echo "deploy is not. tools/images.sh bump cannot fix this — it needs a backend" >&2
        echo "rebuild, then pin the new backend (v1_dashboard_backend) in" >&2
        echo "images/catalog.yaml and run tools/images.sh sync." >&2
        exit 1
    fi
    exit 0
fi
