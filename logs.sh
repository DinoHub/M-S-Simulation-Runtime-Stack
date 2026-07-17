#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ -f ".env" ]; then
  set -a
  source .env
  set +a
fi

export UID
export GID="$(id -g)"

CONFIG_ROOT="${CONFIG_ROOT:-./config}"
export CONFIG_ROOT="$(cd "$CONFIG_ROOT" && pwd)"

SCENARIO="${SCENARIO:-px4-condo}"
SIM_FILE="compose/${SCENARIO}/docker-compose.yml"

# Probe every scenario — the user may have launched a different one than the
# .env default. Pick the first scenario with running containers; fall back to
# SCENARIO from .env if none are running.
for candidate in compose/*/docker-compose.yml; do
    if docker compose --project-directory "$SCRIPT_DIR" -f "$candidate" ps -q 2>/dev/null | grep -q .; then
        SIM_FILE="$candidate"
        SCENARIO="$(basename "$(dirname "$candidate")")"
        break
    fi
done

STACK="${1:-all}"
SERVICE="${2:-}"
FILTER="${3:-}"

# Returns 0 if the given compose file has at least one running container.
has_running() {
    local compose_file="$1"
    local ids
    ids="$(docker compose --project-directory "$SCRIPT_DIR" -f "$compose_file" ps -q 2>/dev/null)"
    [ -n "$ids" ]
}

run_logs() {
    local compose_file="$1"

    if [ -n "$SERVICE" ]; then
        if [ -n "$FILTER" ]; then
            docker compose --project-directory "$SCRIPT_DIR" -f "$compose_file" logs -f "$SERVICE" | grep --line-buffered "$FILTER"
        else
            docker compose --project-directory "$SCRIPT_DIR" -f "$compose_file" logs -f "$SERVICE"
        fi
    else
        if [ -n "$FILTER" ]; then
            docker compose --project-directory "$SCRIPT_DIR" -f "$compose_file" logs -f | grep --line-buffered "$FILTER"
        else
            docker compose --project-directory "$SCRIPT_DIR" -f "$compose_file" logs -f
        fi
    fi
}

case "$STACK" in

stack)
if [ -z "$SERVICE" ]; then
    echo "ERROR: stack logs require a generated stack directory"
    echo "Usage: ./logs.sh stack <generated-stack-dir> [docker compose logs args...]"
    exit 1
fi
shift 2 || true
exec "$SCRIPT_DIR/tools/mns-runtime-tool/stacks/scripts/log_stack.sh" "$SERVICE" "$@"
;;

all)

ACTIVE_STACKS=()
for entry in "sim:$SIM_FILE" "monitoring:docker-compose-monitoring.yml" "metrics:docker-compose-metrics.yml"; do
    label="${entry%%:*}"
    file="${entry#*:}"
    if has_running "$file"; then
        ACTIVE_STACKS+=("$label:$file")
    fi
done

if [ ${#ACTIVE_STACKS[@]} -eq 0 ]; then
    echo "No stacks are currently running. Start one with ./launch.sh."
    exit 0
fi

echo "Streaming logs from running stacks: $(printf '%s ' "${ACTIVE_STACKS[@]%%:*}")"
echo "Press Ctrl+C to stop."
echo

PIDS=()
for entry in "${ACTIVE_STACKS[@]+"${ACTIVE_STACKS[@]}"}"; do
    file="${entry#*:}"
    docker compose --project-directory "$SCRIPT_DIR" -f "$file" logs -f &
    PIDS+=($!)
done

cleanup() {
    for pid in "${PIDS[@]+"${PIDS[@]}"}"; do
        kill "$pid" 2>/dev/null || true
    done
}

trap cleanup EXIT INT TERM
wait
;;

sim)
run_logs "$SIM_FILE"
;;

monitoring)
run_logs docker-compose-monitoring.yml
;;

metrics)
run_logs docker-compose-metrics.yml
;;

*)
echo "Unknown stack: $STACK"
echo
echo "Usage:"
echo "./logs.sh [all|sim|monitoring|metrics] [service] [filter]"
echo "./logs.sh stack <generated-stack-dir> [docker compose logs args...]"
echo
echo "The 'sim' stack uses SCENARIO from .env (currently: $SCENARIO)."
echo "'all' tails only stacks that currently have running containers."
exit 1
;;

esac
