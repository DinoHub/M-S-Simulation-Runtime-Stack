#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <stack-dir> [docker compose args...]"
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

echo "========================================"
echo " Running stack"
echo "========================================"
echo "STACK_DIR=$STACK_DIR"
echo "COMPOSE_FILE=$COMPOSE_FILE"
echo "UID=$UID"
echo "GID=$GID"
echo "DISPLAY=$DISPLAY"
echo "XAUTHORITY=$XAUTHORITY"
echo "CONFIG_ROOT=$CONFIG_ROOT"
echo "COMPOSE_PARALLEL_LIMIT=${COMPOSE_PARALLEL_LIMIT:-1}"
echo

export COMPOSE_PARALLEL_LIMIT="${COMPOSE_PARALLEL_LIMIT:-1}"

cleanup_iceoryx_runtime_state() {
  local iceoryx_root="${MNS_ICEORYX_ROOT:-/tmp/iceoryx2}"
  if [[ "${MNS_CLEAN_ICEORYX:-1}" == "0" ]]; then
    return
  fi
  if ! grep -q "${iceoryx_root}:/tmp/iceoryx2" "$COMPOSE_FILE"; then
    return
  fi

  local active_containers=()
  local container_id
  while IFS= read -r container_id; do
    [[ -n "$container_id" ]] || continue
    if docker inspect --format '{{range .Mounts}}{{println .Source}}{{end}}' "$container_id" 2>/dev/null | grep -Fxq "$iceoryx_root"; then
      active_containers+=("$(docker inspect --format '{{.Name}}' "$container_id" 2>/dev/null | sed 's#^/##')")
    fi
  done < <(docker ps -q)

  if [[ "${#active_containers[@]}" -gt 0 ]]; then
    echo "WARNING: skipping iceoryx cleanup because running containers use $iceoryx_root:"
    printf '  %s\n' "${active_containers[@]}"
    echo "Stop those stacks first, or set MNS_CLEAN_ICEORYX=0 to suppress this check."
    echo
    return
  fi

  mkdir -p "$iceoryx_root"
  find "$iceoryx_root" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
}

cleanup_iceoryx_runtime_state

UP_ARGS=("$@")
has_build_flag=false
for arg in "${UP_ARGS[@]}"; do
  case "$arg" in
    --build|--no-build)
      has_build_flag=true
      break
      ;;
  esac
done

if [[ "$has_build_flag" == false && -f ".mns-image-only" ]]; then
  UP_ARGS=(--no-build "${UP_ARGS[@]+"${UP_ARGS[@]}"}")
elif [[ "$has_build_flag" == false ]]; then
  missing_build_contexts=()
  while IFS= read -r context; do
    [[ -n "$context" ]] || continue
    [[ "$context" == *'$'* ]] && continue

    if [[ "$context" = /* ]]; then
      context_path="$context"
    else
      context_path="$STACK_DIR/$context"
    fi

    if [[ ! -d "$context_path" ]]; then
      missing_build_contexts+=("$context")
    fi
  done < <(
    awk '
      /^[[:space:]]+context:[[:space:]]*/ {
        sub(/^[^:]*:[[:space:]]*/, "")
        gsub(/^["'\'']|["'\'']$/, "")
        print
      }
    ' "$COMPOSE_FILE"
  )

  if [[ "${#missing_build_contexts[@]}" -gt 0 ]]; then
    echo "WARNING: missing Compose build contexts; using --no-build:"
    printf '  %s\n' "${missing_build_contexts[@]}"
    echo
    UP_ARGS=(--no-build "${UP_ARGS[@]}")
  else
    UP_ARGS=(--build "${UP_ARGS[@]}")
  fi
fi

docker compose -f "$COMPOSE_FILE" up "${UP_ARGS[@]}"
