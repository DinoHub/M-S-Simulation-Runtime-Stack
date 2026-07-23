#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
IMAGE="${MNS_RUNTIME_TOOL_IMAGE:-dhdevspace/auto_mns:mns-runtime-tool-latest}"
PULL_POLICY="${MNS_RUNTIME_TOOL_PULL_POLICY:-always}"
IMAGE_SET="${MNS_IMAGE_SET:-published}"

usage() {
  cat <<'EOF'
Usage:
  tools/mns-runtime-tool/run_image.sh <mns-runtime-tool args...>

Runs the published MnS runtime tool image with path-preserving mounts and the
host Docker socket. This is an internal helper used by launch.sh, logs.sh, and
stop.sh; users should prefer those top-level wrappers.
EOF
}

if [[ $# -eq 0 || "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ "$IMAGE_SET" != "published" ]]; then
  echo "ERROR: Runtime Stack ScenarioSpec mode is pull-only and only supports MNS_IMAGE_SET=published; got MNS_IMAGE_SET=$IMAGE_SET" >&2
  echo "Use the Integration Platform dev flow when testing local/* images." >&2
  exit 1
fi

if [[ -n "${MNS_IMAGE_SET_FILE:-}" ]]; then
  echo "ERROR: Runtime Stack ScenarioSpec mode does not support MNS_IMAGE_SET_FILE overrides." >&2
  echo "Edit the generated stack .env only for explicit dhdevspace tag pins." >&2
  exit 1
fi

validate_runtime_image_set_args() {
  local idx=0
  local args=("$@")
  while [[ $idx -lt ${#args[@]} ]]; do
    case "${args[$idx]}" in
      --image-set)
        local next=$((idx + 1))
        if [[ $next -ge ${#args[@]} ]]; then
          echo "ERROR: --image-set requires a value" >&2
          exit 1
        fi
        if [[ "${args[$next]}" != "published" ]]; then
          echo "ERROR: Runtime Stack ScenarioSpec mode only supports --image-set published; got ${args[$next]}" >&2
          exit 1
        fi
        idx=$((idx + 2))
        ;;
      --image-set=*)
        local value="${args[$idx]#*=}"
        if [[ "$value" != "published" ]]; then
          echo "ERROR: Runtime Stack ScenarioSpec mode only supports --image-set published; got $value" >&2
          exit 1
        fi
        idx=$((idx + 1))
        ;;
      --image-set-file|--image-set-file=*)
        echo "ERROR: Runtime Stack ScenarioSpec mode does not support --image-set-file overrides." >&2
        echo "Edit the generated stack .env only for explicit dhdevspace tag pins." >&2
        exit 1
        ;;
      *)
        idx=$((idx + 1))
        ;;
    esac
  done
}

validate_runtime_image_set_args "$@"

abs_path() {
  local value="$1"
  if [[ "$value" = /* ]]; then
    printf '%s\n' "$value"
  else
    printf '%s/%s\n' "$PWD" "$value"
  fi
}

mount_source_for_path() {
  local value="$1"
  local abs
  abs="$(abs_path "$value")"

  case "$abs" in
    "$REPO_ROOT"|"$REPO_ROOT"/*)
      return 0
      ;;
  esac

  if [[ -d "$abs" ]]; then
    printf '%s\n' "$abs"
  elif [[ -f "$abs" ]]; then
    dirname "$abs"
  else
    mkdir -p "$(dirname "$abs")"
    dirname "$abs"
  fi
}

mounts=(
  -v "$REPO_ROOT:$REPO_ROOT:rw"
  -w "$REPO_ROOT"
  -v /var/run/docker.sock:/var/run/docker.sock
  -e "MNS_WORKSPACE_ROOT=$REPO_ROOT"
  -e "MNS_HOST_WORKSPACE_ROOT=$REPO_ROOT"
  -e "MNS_IMAGE_SET=published"
  -e "MNS_FORCE_NO_BUILD=1"
  -e "MNS_HOST_UID=$(id -u)"
  -e "MNS_HOST_GID=$(id -g)"
)

if [[ -n "${DISPLAY:-}" ]]; then
  mounts+=(-e "DISPLAY=$DISPLAY")
fi
if [[ -n "${XAUTHORITY:-}" && -e "${XAUTHORITY:-}" ]]; then
  mounts+=(-e "XAUTHORITY=$XAUTHORITY" -v "$XAUTHORITY:$XAUTHORITY:ro")
fi
if [[ -d /tmp/.X11-unix ]]; then
  mounts+=(-v /tmp/.X11-unix:/tmp/.X11-unix:rw)
fi

extra_mounts=()
args=("$@")
idx=0
while [[ $idx -lt ${#args[@]} ]]; do
  case "${args[$idx]}" in
    --scenario|--out|--stack)
      next=$((idx + 1))
      if [[ $next -lt ${#args[@]} ]]; then
        source_path="$(mount_source_for_path "${args[$next]}")" || true
        if [[ -n "${source_path:-}" ]]; then
          extra_mounts+=("$source_path")
        fi
      fi
      idx=$((idx + 2))
      ;;
    *)
      idx=$((idx + 1))
      ;;
  esac
done

# Deduplicate path-preserving mounts.
seen="|$REPO_ROOT|"
for source_path in "${extra_mounts[@]}"; do
  [[ -n "$source_path" ]] || continue
  case "$seen" in
    *"|$source_path|"*) continue ;;
  esac
  seen+="$source_path|"
  mounts+=(-v "$source_path:$source_path:rw")
done

exec docker run --rm --pull="$PULL_POLICY" "${mounts[@]}" "$IMAGE" "$@"
