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

# Host-port preflight.
#
# This repo has lost hours to port collisions twice, and they present very
# differently depending on how the service reaches the host:
#
#   - Published (`ports:`) — compose refuses to start: "Bind for 0.0.0.0:8082
#     failed: port is already allocated". Loud, but only names the port, not
#     what is holding it.
#   - Host-net (`network_mode: host`) — nothing fails. The second listener logs
#     "address already in use" inside the container and serves nothing, so the
#     stack looks healthy and the visualizer is just dead. This is what the
#     dashboard ros2-node vs monitoring foxglove-bridge clash on 8765 did.
#
# So: check before `up`, and name the occupant. Ports already held by the
# container we are about to (re)create are not conflicts — compose replaces
# those — which keeps `make dashboard` idempotent.
#
# Usage:  check_ports 3001:airsim-dashboard-frontend:frontend \
#                     8765:ros2-node:"Foxglove websocket"
#         check_ports ... || true        # advisory
# 0 = all clear, 1 = at least one real conflict (details printed to stderr).
check_ports() {
  local spec port owner label rest holder conflicts=0

  for spec in "$@"; do
    port="${spec%%:*}"; rest="${spec#*:}"
    owner="${rest%%:*}"; label="${rest#*:}"

    _port_listener "$port" || continue          # free — nothing to report
    holder="$(_port_holder "$port")"

    # Ours already: `up` recreates it in place.
    [ "$holder" = "$owner" ] && continue

    if [ "$conflicts" -eq 0 ]; then
      echo "ERROR: host ports needed by this stack are already in use:" >&2
      conflicts=1
    fi
    printf '  %-6s %-28s wanted by %s (%s)\n' \
      "$port" "held by ${holder:-an unidentified process}" "$owner" "$label" >&2
  done

  [ "$conflicts" -eq 0 ] && return 0

  cat >&2 <<'EOF'

  Stop the holder, or point this stack elsewhere with the matching *_PORT
  variable (see the comments beside each `ports:` entry). Note that host-net
  services do NOT fail loudly on a clash — they start and serve nothing.

EOF
  return 1
}

# 0 = something is listening on $1. `ss` covers host-net containers, which
# publish nothing and so are invisible to `docker ps`.
_port_listener() {
  if command -v ss >/dev/null 2>&1; then
    ss -ltn "sport = :$1" 2>/dev/null | grep -q LISTEN
  else
    # bash /dev/tcp: connect succeeds only if something accepts.
    (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null && exec 3>&-
  fi
}

# Best-effort name for whoever holds $1, preferring a container name so it can
# be compared against the service that wants the port.
#
# Three attempts, because the easy one covers only half the cases:
#   1. `docker ps` published ports — misses host-net containers entirely, since
#      they publish nothing. That is exactly the 8765 case.
#   2. the listener's cgroup — /proc/<pid>/cgroup carries the container id for
#      host-net containers, which is what closes that gap.
#   3. the bare process name from ss.
# Empty when none work: ss hides other users' pids unless we are root, so an
# unattributed port is normal, not an error.
_port_holder() {
  local name pid cid

  name="$(docker ps --format '{{.Names}} {{.Ports}}' 2>/dev/null \
          | awk -v p=":$1->" 'index($0, p) { print $1; exit }')"
  [ -n "$name" ] && { echo "$name"; return; }

  command -v ss >/dev/null 2>&1 || return

  pid="$(ss -ltnp "sport = :$1" 2>/dev/null \
         | sed -n 's/.*pid=\([0-9]\{1,\}\).*/\1/p' | head -1)"
  if [ -n "$pid" ] && [ -r "/proc/$pid/cgroup" ]; then
    cid="$(sed -n 's/.*docker-\([0-9a-f]\{64\}\)\.scope.*/\1/p' "/proc/$pid/cgroup" | head -1)"
    if [ -n "$cid" ]; then
      name="$(docker inspect -f '{{.Name}}' "$cid" 2>/dev/null | sed 's|^/||')"
      [ -n "$name" ] && { echo "$name"; return; }
    fi
  fi

  ss -ltnp "sport = :$1" 2>/dev/null \
    | sed -n 's/.*users:((\"\([^"]*\)\".*/\1/p' | head -1
}

# Registry-reachability preflight.
#
# Every mutable-tag service in this repo carries `pull_policy: always`, which
# makes each `up` contact the registry — and a pull failure aborts the whole
# `up` even when the image is already on disk. A Docker Hub blip therefore
# reads as a hard startup failure:
#
#   failed to resolve reference "docker.io/dhdevspace/auto_mns:...": failed to
#   do request: Head "https://registry-1.docker.io/v2/...": net/http: TLS
#   handshake timeout
#
# Nothing is wrong with the machine in that case; the fix is to retry, or to
# start from the local cache. Naming that up front beats decoding the message.
#
# Advisory by design (callers use `|| true`): the probe is a plain HTTPS GET
# and can fail where the daemon itself would succeed — a proxy configured only
# in /etc/systemd/system/docker.service.d, say. A false warning must not block
# a stack that would have started.
#
# Usage:  check_registry [OVERRIDE_VAR]     # e.g. check_registry MNS_IMAGE_PULL_POLICY
# 0 = registry answered, 1 = it did not (guidance printed to stderr).
check_registry() {
  local var="${1:-MNS_IMAGE_PULL_POLICY}"
  local url="${MSRS_REGISTRY_PROBE_URL:-https://registry-1.docker.io/v2/}"

  command -v curl >/dev/null 2>&1 || return 0   # cannot probe; do not guess

  # No -f: /v2/ answers 401 to an anonymous client, and 401 means reachable.
  # Only a transport-level failure (DNS, TCP, TLS, timeout) is a real miss.
  curl -sS -o /dev/null --max-time "${MSRS_REGISTRY_PROBE_TIMEOUT:-10}" "$url" 2>/dev/null \
    && return 0

  cat >&2 <<EOF
WARNING: cannot reach the container registry ($url).

  Services on mutable tags pull on every start, so this \`up\` will likely fail
  with "TLS handshake timeout" or "failed to resolve reference" — even for
  images already cached locally. These outages are usually brief; retrying is
  the first thing to try.

  To start from the local cache instead:

      $var=missing <the same command>       # or set it in the stack's .env

  Check what is cached first: docker images | grep auto_mns

EOF
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
