#!/usr/bin/env bash
# Docker preflight, sourced by launch.sh (hard guard) and setup.sh (advisory).
#
# Every entrypoint in this repo is a thin wrapper around `docker compose`, so a
# daemon we cannot talk to surfaces as a confusing mid-run failure — the first
# thing launch.sh does is start the monitoring stack, so an unreachable daemon
# reads as "unable to get image 'sid220/lichtblick:latest'" rather than "you are
# not in the docker group". This turns that into a diagnosis up front.
#
# Sourced, not executed: it only defines a function and deliberately does not
# touch the caller's shell options.
#
# Usage:  . "$SCRIPT_DIR/tools/check_docker.sh"
#         check_docker || exit 1        # or: check_docker || true  (advisory)
#         check_nvidia_runtime || true  # advisory; see note on that function

# 0 = daemon reachable, 1 = not (reason printed to stderr).
check_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    echo "ERROR: docker not installed (see https://docs.docker.com/engine/install/)" >&2
    return 1
  fi

  local err
  if err=$(docker version --format '{{.Server.Version}}' 2>&1 >/dev/null); then
    return 0
  fi

  case "$err" in
    *"permission denied"*)
      # The socket is root:docker 0660. Distinguish "never added to the group"
      # from "added, but this login session predates it" — the fix differs, and
      # the second case is the one that bites right after the usermod.
      if getent group docker | grep -qw "$USER"; then
        cat >&2 <<EOF
ERROR: cannot reach the Docker daemon — permission denied on the socket.

  $USER IS in the 'docker' group, but this shell session started before that
  and still carries the old group set. Log out and back in, or run:

      newgrp docker        # then re-run this command in that subshell

EOF
      else
        cat >&2 <<EOF
ERROR: cannot reach the Docker daemon — permission denied on the socket.

  $USER is not in the 'docker' group. Fix with:

      sudo usermod -aG docker $USER
      newgrp docker        # or log out and back in

EOF
      fi
      ;;
    *)
      cat >&2 <<EOF
ERROR: cannot reach the Docker daemon.

  $err

  If the daemon is stopped:  sudo systemctl start docker

EOF
      ;;
  esac
  return 1
}

# GPU passthrough preflight. dcgm-exporter (docker-compose-monitoring.yml),
# airsim-xfs and the display container all carry a `driver: nvidia` device
# reservation; without the NVIDIA Container Toolkit those fail deep inside the
# `up` with the opaque "could not select device driver \"nvidia\" with
# capabilities: [[gpu]]".
#
# Advisory, not fatal: which services actually need a GPU depends on the flow
# (--editor skips the containerized sim, monitoring can be left off), so this
# warns and lets the caller proceed. Assumes check_docker already passed.
# 0 = GPU reservations will work, 1 = they will not (reason printed to stderr).
check_nvidia_runtime() {
  if ! command -v nvidia-smi >/dev/null 2>&1; then
    cat >&2 <<EOF
WARNING: no nvidia-smi on this host — no NVIDIA driver detected.

  Services with a 'driver: nvidia' reservation (airsim, dcgm-exporter) will
  fail to start. Install the driver for your GPU first.

EOF
    return 1
  fi

  if docker info --format '{{range $k, $v := .Runtimes}}{{$k}} {{end}}' 2>/dev/null \
      | grep -qw nvidia; then
    return 0
  fi

  cat >&2 <<'EOF'
WARNING: the NVIDIA driver is present but Docker has no 'nvidia' runtime.

  GPU services will fail with: could not select device driver "nvidia".
  Install the NVIDIA Container Toolkit and point Docker at it:

      curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
        | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
      curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
        | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
        | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
      sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
      sudo nvidia-ctk runtime configure --runtime=docker
      sudo systemctl restart docker

EOF
  return 1
}
