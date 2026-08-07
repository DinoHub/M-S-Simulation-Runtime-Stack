#!/usr/bin/env sh
# Retry wrapper around `docker compose`, sourced by launch.sh and the Makefile.
#
# Every mutable-tag service here carries `pull_policy: always`, so each `up`
# contacts the registry — and a single failed manifest HEAD aborts the whole
# `up`, cached image or not:
#
#   Error response from daemon: failed to resolve reference
#   "docker.io/dhdevspace/auto_mns:tevv-web-dashboard-backend-latest": failed to
#   do request: Head "https://registry-1.docker.io/v2/...": net/http: TLS
#   handshake timeout
#
# Docker Hub hands these out in bursts: the same command a minute later pulls
# fine, and a run can fail on service 4 of 5 after the first three pulled
# clean. A preflight probe cannot catch it — the registry answers when asked
# and times out on the next request. Retrying the `up` is what actually works.
#
# Retries ONLY transport-level registry failures. A missing tag, an auth
# rejection, a port clash, a bad compose file — all fail on the first attempt,
# because retrying those just wastes the user's time three times over.
#
# Sourced, not executed: defines one function and leaves the caller's shell
# options alone. POSIX sh — `make` runs recipes under /bin/sh (dash), so no
# arrays and no PIPESTATUS.
#
# Usage:  . "$SCRIPT_DIR/tools/compose_retry.sh"
#         compose_retry -f docker-compose-dashboard.yml up -d
#         MSRS_COMPOSE_RETRIES=1 ...     # opt out of retrying

compose_retry() {
  _cr_max="${MSRS_COMPOSE_RETRIES:-3}"
  _cr_attempt=1
  _cr_log="$(mktemp)" || { docker compose "$@"; return $?; }
  _cr_rcfile="$(mktemp)" || { rm -f "$_cr_log"; docker compose "$@"; return $?; }

  while :; do
    # tee so a multi-GB pull still streams its progress, and a temp file for the
    # status because the pipeline's left side runs in a subshell (no PIPESTATUS
    # under dash).
    { docker compose "$@" 2>&1; echo "$?" >"$_cr_rcfile"; } | tee "$_cr_log"
    _cr_rc="$(cat "$_cr_rcfile" 2>/dev/null || echo 1)"

    [ "$_cr_rc" = "0" ] && { rm -f "$_cr_log" "$_cr_rcfile"; return 0; }

    if ! _compose_transient "$_cr_log"; then
      rm -f "$_cr_log" "$_cr_rcfile"
      return "$_cr_rc"                      # a real failure — surface it now
    fi

    if [ "$_cr_attempt" -ge "$_cr_max" ]; then
      break
    fi

    echo >&2
    echo "Registry error looks transient — retrying in $((_cr_attempt * 5))s (attempt $_cr_attempt of $_cr_max)." >&2
    sleep "$((_cr_attempt * 5))"
    _cr_attempt=$((_cr_attempt + 1))
  done

  rm -f "$_cr_log" "$_cr_rcfile"
  cat >&2 <<'EOF'

  Still failing after every retry. The registry is more than briefly unhappy.
  Start from the images already on this machine instead:

      MNS_IMAGE_PULL_POLICY=missing <the same command>     # scenario/generated/metrics stacks
      DASHBOARD_PULL_POLICY=missing make dashboard         # TEVV dashboard

  Check what is cached first: docker images | grep auto_mns

EOF
  return "$_cr_rc"
}

# 0 = the failure in $1 is a transport-level registry problem worth retrying.
# Deliberately narrow: "manifest unknown", "unauthorized", "port is already
# allocated" and friends are permanent for this invocation and must fail fast.
_compose_transient() {
  grep -qE 'TLS handshake timeout|failed to do request|i/o timeout|connection reset by peer|net/http: request canceled|temporary failure in name resolution|no such host|500 Internal Server Error|502 Bad Gateway|503 Service Unavailable|toomanyrequests' "$1"
}
