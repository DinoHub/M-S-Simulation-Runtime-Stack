#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <stack-dir> [docker compose logs args...]"
  exit 1
fi

STACK_DIR="$1"
shift || true

STACK_DIR="$(cd "$STACK_DIR" && pwd)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ ! -d "$STACK_DIR" ]]; then
  echo "ERROR: stack dir does not exist: $STACK_DIR"
  exit 1
fi

cd "$STACK_DIR"

if [[ -f ".env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source ".env"
  set +a
fi

# shellcheck disable=SC1091
source "$SCRIPT_DIR/source_strict_env.sh"

: "${CONFIG_ROOT:?CONFIG_ROOT is not set}"

COMPOSE_FILE="docker-compose.yml"
if [[ ! -f "$COMPOSE_FILE" ]]; then
  echo "ERROR: missing compose file: $STACK_DIR/$COMPOSE_FILE"
  exit 1
fi

docker compose -f "$COMPOSE_FILE" logs "$@"