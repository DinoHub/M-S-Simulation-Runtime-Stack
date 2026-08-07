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

# Pinned-image presence preflight.
#
# product-images.env moves in git; the images do not move with it. A pin bump
# leaves the newly pinned tag absent locally, and nothing notices until the
# thing that needs it runs — for the stack generator that is
# /api/scenario/generate, which reports a bare 422 "generation failed" with no
# hint that the cause is a pin from a commit you pulled an hour ago.
# `./product.sh doctor` catches this, but nothing forces you to run it.
#
# Advisory: these images are needed at generate/authoring time, not at `up`
# time, so a miss must not block a dashboard that is otherwise fine.
#
# Usage:  check_images "$MNS_STACK_GENERATOR_IMAGE" "$MNS_AUTHORING_IMAGE"
# 0 = all present (or none named), 1 = at least one missing.
#
# Note: no arrays anywhere in this file — `make` runs recipes under /bin/sh
# (dash on Debian/Ubuntu), which parses `missing=()` as a syntax error and
# aborts the whole recipe before any check runs.
check_images() {
  local image missing=""

  for image in "$@"; do
    [ -n "$image" ] || continue
    docker image inspect "$image" >/dev/null 2>&1 || missing="$missing  $image
"
  done

  [ -z "$missing" ] && return 0

  {
    echo "WARNING: images pinned in product-images.env are not on this machine:"
    printf '%s' "$missing"
    cat <<'EOF'

  Pulled a commit that bumped the pins? The images do not come with it. Until
  they are here, scenario generation fails with a bare 422 "generation failed".

      ./product.sh setup        # pulls the pinned set (retries on registry blips)
      ./product.sh doctor       # lists exactly what is still missing

EOF
  } >&2
  return 1
}

# Print the best X11 cookie path on this host, or nothing if there is none.
#
# The historical fallbacks — $HOME/.Xauthority (launch.sh) and
# /run/user/<uid>/gdm/Xauthority (the generated stacks) — are both wrong on a
# GNOME/Wayland desktop, where the cookie is a per-session
# /run/user/<uid>/.mutter-Xwaylandauth.XXXXXX. Naming a path that does not
# exist is worse than naming none: Docker creates the missing bind source as an
# empty DIRECTORY, and the container gets a directory where a cookie belongs.
#
# Only ever returns a readable regular file, so callers can export the result
# without recreating that failure. Newest mutter cookie wins — the name changes
# per session and stale ones linger.
resolve_xauthority() {
  local candidate

  [ -n "${XAUTHORITY:-}" ] && [ -f "$XAUTHORITY" ] && [ -r "$XAUTHORITY" ] && {
    echo "$XAUTHORITY"; return 0; }

  candidate="$(ls -t /run/user/"$(id -u)"/.mutter-Xwaylandauth.* 2>/dev/null | head -1)"
  [ -n "$candidate" ] && [ -r "$candidate" ] && { echo "$candidate"; return 0; }

  for candidate in "$HOME/.Xauthority" "/run/user/$(id -u)/gdm/Xauthority"; do
    [ -f "$candidate" ] && [ -r "$candidate" ] && { echo "$candidate"; return 0; }
  done

  return 1
}

# X11 preflight for the GPU/Unreal containers.
#
# The sim mounts the host's X cookie and talks to /tmp/.X11-unix. Two ways that
# breaks, both of which surface far from the cause:
#
#   - XAUTHORITY unset. Generated stacks bind
#     `${XAUTHORITY:-/run/user/1000/gdm/Xauthority}`, and that gdm path does not
#     exist on a GNOME/Wayland host (the cookie is
#     /run/user/<uid>/.mutter-Xwaylandauth.XXXXXX). Docker then CREATES the
#     missing source as an empty directory and bind-mounts it over
#     /tmp/.Xauthority.
#   - XAUTHORITY set to something that is not a readable regular file — same
#     end state.
#
# Either way Unreal logs "Authorization required, but no authorization protocol
# specified", then "Could not initialize SDL: x11 not available", exits 0, and
# `restart: unless-stopped` loops it. The healthcheck never passes, so what the
# user sees is "dependency failed to start: container ...-unreal-airsim is
# unhealthy" — nothing about X11 at all.
#
# Advisory: headless flows (--headless, -RenderOffScreen) need none of this.
# 0 = X11 pass-through looks sound, 1 = it does not.
check_x11() {
  local fallback="/run/user/$(id -u)/gdm/Xauthority"
  local problem=""

  if [ -z "${DISPLAY:-}" ]; then
    problem="DISPLAY is not set"
  elif [ -z "${XAUTHORITY:-}" ]; then
    problem="XAUTHORITY is not set, so stacks fall back to $fallback"
  elif [ ! -f "$XAUTHORITY" ]; then
    problem="XAUTHORITY=$XAUTHORITY is not a regular file"
  elif [ ! -r "$XAUTHORITY" ]; then
    problem="XAUTHORITY=$XAUTHORITY is not readable"
  else
    # Sound, but clean up after an earlier bad run if we still can: a leftover
    # empty directory at the fallback path is what a previous launch created,
    # and it will be reused the moment XAUTHORITY goes missing again.
    [ -d "$fallback" ] && rmdir "$fallback" 2>/dev/null
    return 0
  fi

  {
    echo "WARNING: X11 pass-through looks broken — $problem."
    echo
    echo "  GPU/Unreal containers will start, fail to open a display, exit 0, and"
    echo "  restart in a loop. The visible error is \"dependency failed to start:"
    echo "  container ...-unreal-airsim is unhealthy\", which says nothing about X11."
    echo
    if [ -d "$fallback" ]; then
      echo "  A previous run already left an empty DIRECTORY at the fallback path:"
      echo "      $fallback"
      echo "  Docker created it when the bind source was missing. Remove it:"
      echo "      rmdir $fallback      # sudo if it is root-owned"
      echo
    fi
    cat <<'EOF'
  Point XAUTHORITY at the real cookie before launching (GNOME/Wayland hosts):

      export XAUTHORITY=$(ls -t /run/user/$(id -u)/.mutter-Xwaylandauth.* 2>/dev/null | head -1)

  Or skip the display entirely: ./launch.sh <scenario> --headless

EOF
  } >&2
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
