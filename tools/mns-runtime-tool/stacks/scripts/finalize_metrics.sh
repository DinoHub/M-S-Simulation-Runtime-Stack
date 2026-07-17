#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
STACKS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

usage() {
  cat <<'EOF'
Usage:
  scripts/finalize_metrics.sh <stack-dir-or-name>

Stages ScenarioSpec/generated-stack context into each MetricsEmitter run folder
and finalizes local run manifests. Upload and ClickHouse load are disabled by
default unless MNS_METRICS_ARCHIVE_UPLOAD or MNS_METRICS_ARCHIVE_LOAD_CLICKHOUSE
are true in the stack .env/environment.
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

is_true() {
  case "${1:-}" in
    1|true|TRUE|yes|YES|on|ON) return 0 ;;
    *) return 1 ;;
  esac
}

copy_if_exists() {
  local src="$1"
  local dst="$2"
  if [[ -f "$src" ]]; then
    mkdir -p "$(dirname "$dst")"
    cp -f "$src" "$dst"
  fi
}

stage_scenario_sources() {
  local stack_dir="$1"
  local run_dir="$2"
  local source_dir="$stack_dir/source"
  local file rel dst

  [[ -d "$source_dir" ]] || return 0
  while IFS= read -r -d '' file; do
    rel="${file#"$source_dir/"}"
    case "$rel" in
      ScenarioBundle/artifacts/*) continue ;;
    esac
    dst="$run_dir/scenario/$rel"
    mkdir -p "$(dirname "$dst")"
    cp -f "$file" "$dst"
  done < <(find "$source_dir" -type f \( -name '*.yaml' -o -name '*.yml' -o -name '*.json' \) -print0)
}

stage_stack_context() {
  local stack_dir="$1"
  local run_dir="$2"

  stage_scenario_sources "$stack_dir" "$run_dir"
  copy_if_exists "$stack_dir/docker-compose.yml" "$run_dir/stack/docker-compose.yml"
  copy_if_exists "$stack_dir/generated-manifest.json" "$run_dir/stack/generated-manifest.json"
  copy_if_exists "$stack_dir/execution-context.json" "$run_dir/stack/execution-context.json"
  copy_if_exists "$stack_dir/scenario-artifacts-manifest.json" "$run_dir/stack/scenario-artifacts-manifest.json"
  copy_if_exists "$stack_dir/scenario-docker-args.txt" "$run_dir/stack/scenario-docker-args.txt"
  copy_if_exists "$stack_dir/config/metrics/metrics_runtime.json" "$run_dir/stack/config/metrics/metrics_runtime.json"
  copy_if_exists "$stack_dir/config/scenario/scenario_runtime.json" "$run_dir/stack/config/scenario/scenario_runtime.json"
  copy_if_exists "$stack_dir/config/scenario/scenario_conditions.json" "$run_dir/stack/config/scenario/scenario_conditions.json"
  copy_if_exists "$stack_dir/config/scenario/object_clutter.json" "$run_dir/stack/config/scenario/object_clutter.json"
  copy_if_exists "$stack_dir/config/scenario-plugin/scenario_plugin.json" "$run_dir/stack/config/scenario-plugin/scenario_plugin.json"
}

finalize_with_airsim_tools() {
  local metrics_dir="$1"
  local airsim_root="${TEVV_AIRSIM_ROOT:-$REPO_ROOT/services/tevv-airsim}"
  local run_bundle_dir="$airsim_root/tooling/run_bundle"
  local finalize_py="$run_bundle_dir/finalize_run.py"
  local make_manifest_py="$run_bundle_dir/make_manifest.py"
  local args=()

  if [[ ! -f "$make_manifest_py" ]]; then
    echo "WARNING: AirSim run-bundle tools not found at $run_bundle_dir; leaving events.jsonl files unfinalized." >&2
    return 0
  fi

  if [[ -f "$finalize_py" ]] && python3 -c 'import mcap.writer' >/dev/null 2>&1; then
    args=("$metrics_dir" --repo "$REPO_ROOT")
    if ! is_true "${MNS_METRICS_ARCHIVE_UPLOAD:-}"; then
      args+=(--no-upload)
    fi
    if ! is_true "${MNS_METRICS_ARCHIVE_LOAD_CLICKHOUSE:-}"; then
      args+=(--no-load)
    fi
    python3 "$finalize_py" "${args[@]}"
  else
    echo "WARNING: optional run-bundle dependencies are unavailable; writing manifest.json without MCAP/upload/load." >&2
    python3 "$make_manifest_py" "$metrics_dir" --repo "$REPO_ROOT"
  fi
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

[[ $# -eq 1 ]] || { usage >&2; exit 1; }

STACK_DIR="$(resolve_stack_dir "$1")" || die "stack dir does not exist: $1"

if [[ -f "$STACK_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$STACK_DIR/.env"
  set +a
fi

if ! is_true "${MNS_METRICS_ENABLED:-true}"; then
  echo "Metrics logging disabled for $STACK_DIR."
  exit 0
fi

METRICS_OUTPUT="${MNS_METRICS_OUTPUT:-outputs/metrics}"
if [[ "$METRICS_OUTPUT" = /* ]]; then
  METRICS_DIR="$METRICS_OUTPUT"
else
  METRICS_DIR="$STACK_DIR/$METRICS_OUTPUT"
fi
mkdir -p "$METRICS_DIR"

run_dirs=()
while IFS= read -r -d '' events_file; do
  run_dirs+=("$(dirname "$events_file")")
done < <(find "$METRICS_DIR" -type f -path '*/run_*/events.jsonl' -print0 2>/dev/null)

if [[ "${#run_dirs[@]}" -eq 0 ]]; then
  echo "No MetricsEmitter events found under $METRICS_DIR yet."
  exit 0
fi

for run_dir in "${run_dirs[@]}"; do
  stage_stack_context "$STACK_DIR" "$run_dir"
done

finalize_with_airsim_tools "$METRICS_DIR"

echo "Metrics logs are under: $METRICS_DIR"
