#!/usr/bin/env bash

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo "ERROR: source_strict_env.sh must be sourced, not executed."
  echo "Use: source scripts/source_strict_env.sh"
  exit 1
fi

set -euo pipefail

# UID already exists in Bash as a readonly shell variable.
: "${UID:?Unable to determine UID}"
export UID

# GID is not automatically available the same way, so derive it if missing.
export GID="${GID:-$(id -g 2>/dev/null || true)}"
: "${GID:?Unable to determine GID}"

if [[ -z "${DISPLAY:-}" ]]; then
  if [[ -S /tmp/.X11-unix/X0 ]]; then
    export DISPLAY=":0"
  else
    echo "ERROR: DISPLAY is not set and could not infer a usable X11 display."
    return 1
  fi
fi

if [[ -z "${XAUTHORITY:-}" ]]; then
  candidates=(
    "$HOME/.Xauthority"
    "/run/user/$(id -u)/gdm/Xauthority"
    "/run/user/$(id -u)/.mutter-Xwaylandauth."*
  )

  found_xauth=""
  for c in "${candidates[@]}"; do
    for expanded in $c; do
      if [[ -e "$expanded" ]]; then
        found_xauth="$expanded"
        break 2
      fi
    done
  done

  if [[ -n "$found_xauth" ]]; then
    export XAUTHORITY="$found_xauth"
  else
    echo "ERROR: XAUTHORITY is not set and no common Xauthority file was found."
    return 1
  fi
fi

if [[ ! -e "$XAUTHORITY" ]]; then
  echo "ERROR: XAUTHORITY does not exist: $XAUTHORITY"
  return 1
fi

export SDL_VIDEODRIVER="${SDL_VIDEODRIVER:-x11}"
export QT_X11_NO_MITSHM="${QT_X11_NO_MITSHM:-1}"

if command -v xhost >/dev/null 2>&1; then
  xhost +local:docker >/dev/null 2>&1 || true
fi
