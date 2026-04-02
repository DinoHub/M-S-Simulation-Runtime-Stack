#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

export UID
export GID="$(id -g)"

STACK="${1:-all}"
SERVICE="${2:-}"
FILTER="${3:-}"

run_logs() {
    local compose_file="$1"

    if [ -n "$SERVICE" ]; then
        if [ -n "$FILTER" ]; then
            docker compose -f "$compose_file" logs -f "$SERVICE" | grep --line-buffered "$FILTER"
        else
            docker compose -f "$compose_file" logs -f "$SERVICE"
        fi
    else
        if [ -n "$FILTER" ]; then
            docker compose -f "$compose_file" logs -f | grep --line-buffered "$FILTER"
        else
            docker compose -f "$compose_file" logs -f
        fi
    fi
}

case "$STACK" in

all)

echo "Streaming logs from all stacks..."
echo "Press Ctrl+C to stop."
echo

docker compose -f docker-compose-sim.yml logs -f &
PID1=$!

docker compose -f docker-compose-monitoring.yml logs -f &
PID2=$!

docker compose -f docker-compose-metrics.yml logs -f &
PID3=$!

cleanup() {
    kill "$PID1" "$PID2" "$PID3" 2>/dev/null || true
}

trap cleanup EXIT INT TERM
wait
;;

sim)
run_logs docker-compose-sim.yml
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
exit 1
;;

esac