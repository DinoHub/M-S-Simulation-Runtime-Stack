#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STACKS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

usage() {
  cat <<'EOF'
Usage:
  scripts/stop_stack.sh [stack-dir-or-name] [docker compose down args...]
  scripts/stop_stack.sh --all [docker compose down args...]

Examples:
  scripts/stop_stack.sh
  scripts/stop_stack.sh px4-xfs-multi
  scripts/stop_stack.sh stacks/ardupilot-xfs-single --volumes
  scripts/stop_stack.sh --all --volumes

Behavior:
  With a stack argument, stops that one Compose stack and its matching external
  MAVROS sidecars.
  With no arguments, stops all known stacks in this repo plus active generated
  stacks discovered from Docker Compose labels, including /tmp-generated stacks.
  Docker Compose shutdown removes orphaned containers by default.

  External MAVROS sidecars created by tests/test_external_mavros.sh are removed
  before Compose shutdown so agent_internal-* Docker networks can be removed
  cleanly when no other stack is still attached.
EOF
}

die() {
  echo "ERROR: $*" >&2
  exit 1
}

resolve_stack_dir() {
  local stack_arg="$1"

  if [[ -d "$stack_arg" ]]; then
    cd "$stack_arg" && pwd
  elif [[ -d "$STACKS_DIR/$stack_arg" ]]; then
    cd "$STACKS_DIR/$stack_arg" && pwd
  else
    return 1
  fi
}

known_stack_dirs() {
  find "$STACKS_DIR" -mindepth 2 -maxdepth 3 -name docker-compose.yml -printf '%h\n' | sort
}

active_stack_dirs() {
  local dir

  while IFS= read -r dir; do
    [[ -n "$dir" ]] || continue
    [[ -f "$dir/docker-compose.yml" ]] || continue

    # Limit label-based discovery to MnS stack folders, so a no-arg stop does
    # not tear down unrelated Compose projects on the same machine.
    if [[ -f "$dir/generated-manifest.json" || -f "$dir/scenario-artifacts-manifest.json" ]]; then
      printf '%s\n' "$dir"
    elif [[ -f "$dir/.env" ]] && grep -q '^CONFIG_ROOT=' "$dir/.env"; then
      printf '%s\n' "$dir"
    fi
  done < <(docker ps -a --filter label=com.docker.compose.project.working_dir \
    --format '{{.Label "com.docker.compose.project.working_dir"}}')
}

all_stack_dirs() {
  {
    known_stack_dirs
    active_stack_dirs
  } | awk 'NF && !seen[$0]++' | sort
}

remove_external_mavros_sidecars() {
  local stack_dir
  local stack_name
  local stack_names=("$@")
  local names=()
  local name

  if [[ "${#stack_names[@]}" -eq 0 ]]; then
    while IFS= read -r stack_dir; do
      stack_names+=("$(basename "$stack_dir")")
    done < <(all_stack_dirs)
  fi

  for stack_name in "${stack_names[@]}"; do
    while IFS= read -r name; do
      [[ -n "$name" ]] || continue
      names+=("$name")
    done < <(docker ps -a --format '{{.Names}}' | grep -E "^mavros-${stack_name}-" || true)
  done

  if [[ "${#names[@]}" -eq 0 ]]; then
    echo "No external MAVROS sidecars found."
    return 0
  fi

  echo "Stopping external MAVROS sidecars:"
  printf '  %s\n' "${names[@]}"
  docker rm -f "${names[@]}" >/dev/null
}

compose_down_args() {
  local has_remove_orphans=false
  local arg

  for arg in "$@"; do
    if [[ "$arg" == "--remove-orphans" ]]; then
      has_remove_orphans=true
      break
    fi
  done

  if [[ "$has_remove_orphans" == false ]]; then
    printf '%s\n' "--remove-orphans"
  fi
  if [[ "$#" -gt 0 ]]; then
    printf '%s\n' "$@"
  fi
}

stop_one_stack() {
  local stack_dir="$1"
  local stack_name
  local down_args=()
  shift || true

  stack_name="$(basename "$stack_dir")"
  mapfile -t down_args < <(compose_down_args "$@")

  remove_external_mavros_sidecars "$stack_name"
  echo

  (
    cd "$stack_dir"

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
      echo "ERROR: missing compose file: $stack_dir/$COMPOSE_FILE"
      exit 1
    fi

    echo "========================================"
    echo " Stopping stack"
    echo "========================================"
    echo "STACK_DIR=$stack_dir"
    echo "COMPOSE_FILE=$COMPOSE_FILE"
    echo "UID=$UID"
    echo "GID=$GID"
    echo "DISPLAY=$DISPLAY"
    echo "XAUTHORITY=$XAUTHORITY"
    echo "CONFIG_ROOT=$CONFIG_ROOT"
    echo

    docker compose -f "$COMPOSE_FILE" down "${down_args[@]}"
  )

  if [[ "${MNS_FINALIZE_METRICS_ON_STOP:-1}" != "0" && -f "$SCRIPT_DIR/finalize_metrics.sh" ]]; then
    "$SCRIPT_DIR/finalize_metrics.sh" "$stack_dir" || \
      echo "WARNING: metrics finalization failed for $stack_dir; events.jsonl files remain in the metrics output directory." >&2
  fi
}

stop_all_stacks() {
  local stack_dir

  echo "========================================"
  echo " Stopping all stacks"
  echo "========================================"
  echo "STACKS_DIR=$STACKS_DIR"
  echo

  remove_external_mavros_sidecars
  echo

  while IFS= read -r stack_dir; do
    stop_one_stack "$stack_dir" "$@"
  done < <(all_stack_dirs)
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ $# -eq 0 ]]; then
  stop_all_stacks
  exit 0
fi

if [[ "$1" == "--all" ]]; then
  shift
  stop_all_stacks "$@"
  exit 0
fi

STACK_DIR="$(resolve_stack_dir "$1")" || die "stack dir does not exist: $1"
shift || true

stop_one_stack "$STACK_DIR" "$@"
