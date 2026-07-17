#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

usage() {
  cat <<'EOF'
Usage:
  mns_runtime_tool.sh validate --scenario <ScenarioSpec> [--profile docker|editor]
  mns_runtime_tool.sh generate --scenario <ScenarioSpec> --out <stack-dir> [--profile docker|editor]
  mns_runtime_tool.sh run --stack <stack-dir> [docker compose up args...]
  mns_runtime_tool.sh stop --stack <stack-dir> [docker compose down args...]
  mns_runtime_tool.sh generate-run --scenario <ScenarioSpec> --out <stack-dir> [--profile docker|editor] [--run-arg <arg>...]
  mns_runtime_tool.sh version [--json]

This is the user-facing ScenarioSpec runtime entrypoint. It resolves the
generator and stack scripts relative to itself and normalizes generated stacks
to image-only Compose so callers do not need service source checkouts.
EOF
}

die() {
  echo "ERROR: $*" >&2
  exit 1
}

require_value() {
  local flag="$1"
  local value="${2:-}"
  [[ -n "$value" ]] || die "$flag requires a value"
}

run_generator() {
  "$REPO_ROOT/stacks/orchestrator/generate_stack.py" "$@"
}

sanitize_image_only_stack() {
  local stack="$1"
  "$REPO_ROOT/stacks/scripts/image_only_compose.py" "$stack"
}

prepend_no_build_for_image_only() {
  local stack="$1"
  shift || true
  local args=("$@")
  local has_build_flag=false
  local arg
  for arg in "${args[@]+"${args[@]}"}"; do
    case "$arg" in
      --build|--no-build)
        has_build_flag=true
        break
        ;;
    esac
  done
  if [[ -f "$stack/.mns-image-only" && "$has_build_flag" == false ]]; then
    printf '%s\0' --no-build
  fi
  printf '%s\0' "${args[@]+"${args[@]}"}"
}

cmd="${1:-}"
if [[ -z "$cmd" || "$cmd" == "-h" || "$cmd" == "--help" ]]; then
  usage
  exit 0
fi
shift || true

case "$cmd" in
  version)
    run_generator version "$@"
    ;;

  validate)
    scenario=""
    profile="docker"
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --scenario)
          require_value "$1" "${2:-}"
          scenario="$2"
          shift 2
          ;;
        --profile)
          require_value "$1" "${2:-}"
          profile="$2"
          shift 2
          ;;
        *)
          die "unknown validate argument: $1"
          ;;
      esac
    done
    [[ -n "$scenario" ]] || die "validate requires --scenario"
    run_generator validate "$scenario" --profile "$profile"
    ;;

  generate)
    scenario=""
    out=""
    profile="docker"
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --scenario)
          require_value "$1" "${2:-}"
          scenario="$2"
          shift 2
          ;;
        --out)
          require_value "$1" "${2:-}"
          out="$2"
          shift 2
          ;;
        --profile)
          require_value "$1" "${2:-}"
          profile="$2"
          shift 2
          ;;
        *)
          die "unknown generate argument: $1"
          ;;
      esac
    done
    [[ -n "$scenario" ]] || die "generate requires --scenario"
    [[ -n "$out" ]] || die "generate requires --out"
    run_generator generate "$scenario" --profile "$profile" --out "$out"
    sanitize_image_only_stack "$out"
    ;;

  run)
    stack=""
    run_args=()
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --stack)
          require_value "$1" "${2:-}"
          stack="$2"
          shift 2
          ;;
        *)
          run_args+=("$1")
          shift
          ;;
      esac
    done
    [[ -n "$stack" ]] || die "run requires --stack"
    mapfile -d '' -t run_args < <(prepend_no_build_for_image_only "$stack" "${run_args[@]+"${run_args[@]}"}")
    "$REPO_ROOT/stacks/scripts/run_stack.sh" "$stack" "${run_args[@]+"${run_args[@]}"}"
    ;;

  stop)
    stack=""
    stop_args=()
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --stack)
          require_value "$1" "${2:-}"
          stack="$2"
          shift 2
          ;;
        *)
          stop_args+=("$1")
          shift
          ;;
      esac
    done
    [[ -n "$stack" ]] || die "stop requires --stack"
    "$REPO_ROOT/stacks/scripts/stop_stack.sh" "$stack" "${stop_args[@]}"
    ;;

  generate-run)
    scenario=""
    out=""
    profile="docker"
    run_args=()
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --scenario)
          require_value "$1" "${2:-}"
          scenario="$2"
          shift 2
          ;;
        --out)
          require_value "$1" "${2:-}"
          out="$2"
          shift 2
          ;;
        --profile)
          require_value "$1" "${2:-}"
          profile="$2"
          shift 2
          ;;
        --run-arg)
          require_value "$1" "${2:-}"
          run_args+=("$2")
          shift 2
          ;;
        *)
          die "unknown generate-run argument: $1"
          ;;
      esac
    done
    [[ -n "$scenario" ]] || die "generate-run requires --scenario"
    [[ -n "$out" ]] || die "generate-run requires --out"
    run_generator generate "$scenario" --profile "$profile" --out "$out"
    sanitize_image_only_stack "$out"
    mapfile -d '' -t run_args < <(prepend_no_build_for_image_only "$out" "${run_args[@]+"${run_args[@]}"}")
    "$REPO_ROOT/stacks/scripts/run_stack.sh" "$out" "${run_args[@]+"${run_args[@]}"}"
    ;;

  *)
    die "unknown command: $cmd"
    ;;
esac
