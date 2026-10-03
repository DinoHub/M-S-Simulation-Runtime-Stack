#!/usr/bin/env bash
# Fly one scenario headless: generate its stack, run it until the mission is
# done, and stop it (finalize_metrics included). `make fly` runs this after
# bringing the packs to the lock's versions, the check `make dashboard` does.
#
#   tools/fly.sh SCENARIO [--record] [--keep] [-- <extra mns-stacks run flags>]
#
# --keep brings the stack up and leaves it up (no --until-done): fly it from
# the dashboard, QGroundControl or your own stack, then `make stop`.
#
# Without a --done in the extra flags the run is a fixed-length one
# (--done timeout, FLY_SECONDS, default 300 s): the runtime host has no
# mission-complete marker, and a ScenarioLab export names no mission.
#
# SCENARIO is a folder under scenarios/, a folder, or a ScenarioSpec.yaml.
# The stack goes to generated/<name>/ (the dashboard's layout), a bag to
# <runs dir>/<run id>/bag, and the stack is remembered for `make stop`.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
usage() { sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; }

scenario="" record=() keep=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --record) record=(--record) ;;
    --keep) keep=true ;;
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
# Advisory: a vehicle at the world origin of a level with no floor there (XFS)
# falls through it, and the autopilot never arms. Warns with the measured
# start (packs/level-spawn-hints.json); never stops the run.
if command -v python3 >/dev/null 2>&1; then python3 "$ROOT/tools/check_spawn.py" "$spec" || true; fi
echo "== generate $name -> generated/$name"
# The folder, not the file: a split spec keeps its asset packs beside it.
kit=()
[[ -n "${MNS_CAPABILITY_KIT:-}" ]] && kit=(--kit "$(cd "$MNS_CAPABILITY_KIT" && pwd)")
# --no-topics: generate runs without the Docker socket, so its topic preview
# (which needs the bridge image) could only report "unavailable".
# COMPONENTS: component packages to attach (space-separated folders, e.g.
# components/mac-vo); the ScenarioSpec itself never names them.
components=()
for c in ${COMPONENTS:-}; do components+=(--component "$(cd "$c" && pwd)"); done
"$ROOT/tools/mns-stacks.sh" generate "$(dirname "$spec")" --profile docker --no-topics \
  --out "$stack" "${kit[@]}" "${components[@]}"
# Remembered for `make stop`: the stack, and its Compose project when the run
# was given one (--project NAME in the extra flags), tab-separated.
project=""
prev=""
for arg in "$@"; do
  [[ "$prev" == --project ]] && project="$arg"
  case "$arg" in --project=*) project="${arg#--project=}" ;; esac
  prev="$arg"
done
mkdir -p "$ROOT/.mns"
printf '%s\t%s\n' "$stack" "$project" >"$ROOT/.mns/last-stack"

if [[ "$keep" == true ]]; then
  echo "== run generated/$name${record[*]:+ (recording)}, left up (--keep)"
  "$ROOT/tools/mns-stacks.sh" run --stack "$stack" "${record[@]}" "$@"
  echo "stack left up: make stop   (or: make stop STACK=generated/$name)"
  exit 0
fi
# `run --until-done` needs a completion signal, and the runtime host emits no
# mission-complete marker of its own. A ScenarioLab export carries no mission,
# so unless the caller names one (ARGS="--done landed --mav ..." or
# "--done topic:/x"), fly a fixed-length run: --done timeout, FLY_SECONDS long.
done_args=()
has_done=false has_timeout=false
for arg in "$@"; do
  case "$arg" in
    --done|--done=*) has_done=true ;;
    --timeout|--timeout=*) has_timeout=true ;;
  esac
done
if [[ "$has_done" == false ]]; then
  done_args=(--done timeout)
  if [[ "$has_timeout" == false ]]; then
    done_args+=(--timeout "${FLY_SECONDS:-300}")
    echo "NOTE: no --done in ARGS: flying a fixed ${FLY_SECONDS:-300} s run (--done timeout)." >&2
    echo "      FLY_SECONDS=<s> changes the length; ARGS=\"--done ...\" names a real completion signal." >&2
  else
    echo "NOTE: no --done in ARGS: the run ends when --timeout elapses (--done timeout)." >&2
  fi
fi
echo "== run generated/$name${record[*]:+ (recording)} until done"
echo "   (if it is interrupted: make stop STACK=generated/$name)"
"$ROOT/tools/mns-stacks.sh" run --stack "$stack" "${record[@]}" --until-done "${done_args[@]}" "$@"
