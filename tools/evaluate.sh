#!/usr/bin/env bash
# Evaluate one scenario end to end and print its verdict:
#
#   generate it (with COMPONENTS) -> bring it up -> wait for the components
#   -> record what the scorers read (@scoring) -> fly MISSION -> stop (the
#   metrics are finalized and every component is scored) -> wait for the
#   metrics service -> print the mission checks and each component's score.
#
#   tools/evaluate.sh SCENARIO [--mission "<command>"] [-- <extra mns-stacks run flags>]
#
# MISSION is a command that flies the mission and returns when it is over;
# {stack} in it is the generated stack's folder. Without one nobody flies and
# the run is a fixed FLY_SECONDS (300) recording. COMPONENTS="components/x ..."
# attaches component packages, as for `make fly`. The verdict is also in the
# dashboard's Analysis (Run metrics); `mns-stacks report --stack ...` prints it
# again later.
#
#   tools/evaluate.sh casio-siyi \
#     --mission "STACK={stack} components/casio-edge/bench/square-flight.sh eval \
#                --center-n 15.5 --center-e 17 --side 36 --alt 12 --hover-at-end"
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
usage() { sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; }

scenario="" mission=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --mission) mission="${2:?--mission needs a command}"; shift ;;
    --) shift; break ;;
    -h|--help) usage; exit 0 ;;
    -*) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
    *) [[ -z "$scenario" ]] || { usage >&2; exit 2; }; scenario="$1" ;;
  esac
  shift
done
[[ -n "$scenario" ]] || { usage >&2; exit 2; }

run_args=(--topics @scoring)
if [[ -n "$mission" ]]; then
  # The mission command runs in the mns-stacks container, from the repository
  # root (the workspace is mounted at the same path), after the components are
  # ready and the recording has started; the run ends EVAL_TAIL_S after it.
  run_args+=(--mission external --start-cmd "cd $(printf '%q' "$ROOT") && $mission"
             --done timeout --timeout "${EVAL_TAIL_S:-10}")
fi

status=0
"$ROOT/tools/fly.sh" "$scenario" --record -- "${run_args[@]}" "$@" || status=$?
stack="$(cut -f1 "$ROOT/.mns/last-stack")"
echo "== verdict (metrics service, up to ${REPORT_WAIT_S:-300} s)"
# The table goes to the terminal, the whole report to the stack's outputs.
"$ROOT/tools/mns-stacks.sh" report --stack "$stack" --wait "${REPORT_WAIT_S:-300}" \
  > "$stack/outputs/last-report.json" || status=$?
echo "   full report: ${stack#"$ROOT"/}/outputs/last-report.json"
exit "$status"
