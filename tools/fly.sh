#!/usr/bin/env bash
# Fly one scenario headless: generate its stack, run it until the mission is
# done, and stop it (finalize_metrics included). `make fly` runs this after
# bringing the packs to the lock's versions, the check `make dashboard` does.
#
#   tools/fly.sh SCENARIO [--record] [-- <extra mns-stacks run flags>]
#
# SCENARIO is a folder under scenarios/, a folder, or a ScenarioSpec.yaml.
# The stack goes to generated/<name>/ (the dashboard's layout), a bag to
# <runs dir>/<run id>/bag, and the stack is remembered for `make stop`.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
usage() { sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; }

scenario="" record=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --record) record=(--record) ;;
    --) shift; break ;;
    -h|--help) usage; exit 0 ;;
    -*) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
    *) [[ -z "$scenario" ]] || { usage >&2; exit 2; }; scenario="$1" ;;
  esac
  shift
done
[[ -n "$scenario" ]] || { usage >&2; exit 2; }

spec=""
for candidate in "$scenario" "$ROOT/scenarios/$scenario"; do
  if [[ -f "$candidate" ]]; then spec="$candidate"; break; fi
  if [[ -f "$candidate/ScenarioSpec.yaml" ]]; then spec="$candidate/ScenarioSpec.yaml"; break; fi
done
if [[ -z "$spec" ]]; then
  echo "ERROR: no ScenarioSpec for '$scenario' (looked for it as a file, a folder, and" >&2
  echo "       scenarios/$scenario/ScenarioSpec.yaml). Export one from ScenarioLab: make author" >&2
  exit 2
fi
spec="$(cd "$(dirname "$spec")" && pwd)/$(basename "$spec")"
name="$(basename "$(dirname "$spec")")"
stack="$ROOT/generated/$name"

# Created by the host user first, as `make dashboard` does, so a root-owned
# directory never blocks the generator (which runs as you).
mkdir -p "$ROOT/generated"
echo "== generate $name -> generated/$name"
# The folder, not the file: a split spec keeps its asset packs beside it.
"$ROOT/tools/mns-stacks.sh" generate "$(dirname "$spec")" --profile docker --out "$stack"
mkdir -p "$ROOT/.mns"
printf '%s\n' "$stack" >"$ROOT/.mns/last-stack"

echo "== run generated/$name${record[*]:+ (recording)} until the mission is done"
echo "   (if it is interrupted: make stop STACK=generated/$name)"
"$ROOT/tools/mns-stacks.sh" run --stack "$stack" "${record[@]}" --until-done "$@"
