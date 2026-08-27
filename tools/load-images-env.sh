#!/usr/bin/env sh
# `local` is not in POSIX but is implemented by every shell that runs this
# (dash, bash, ash, ksh). Keeping it is deliberate; SC3043 would otherwise
# fire on every helper below.
# shellcheck disable=SC3043
# tools/load-images-env.sh — sourced helper, not executed directly.
#
# POSIX sh, not bash: the Makefile's dashboard targets source this under
# /bin/sh (no `[[`, no `${!name}` indirection — see below for the workarounds).
#
# Exports each KEY=VAL from a given generated env file (e.g.
# images/platform-images.generated.env), but ONLY when the key is both:
#   1. unset in the current shell environment, and
#   2. absent from ./.env
#
# Why this two-part guard instead of just `--env-file <generated>`:
#   - `docker compose` already auto-loads ./.env for variable interpolation
#     (it sets AIRSIM_IMAGE and friends there). A shell environment variable
#     always outranks a .env value in compose's precedence order, so a caller
#     who exported an override (or set one directly in .env) must keep
#     winning — this helper must never clobber that.
#   - `--env-file <path>` REPLACES compose's .env lookup outright rather than
#     adding to it, which would silently drop every other variable ./.env
#     was supplying. Exporting into the shell instead layers on top without
#     disturbing that lookup.
#   - ./.env is not guaranteed to exist at all (fresh clone, CI checkout).
#     This file must be safe to source in that case too.
#
# Usage:
#   . "$SCRIPT_DIR/tools/load-images-env.sh"
#   load_images_env "$SCRIPT_DIR/images/platform-images.generated.env"

# True (0) when $1 is already assigned in env file $2 (plain `KEY=` or
# `export KEY=` at the start of a line, arbitrary leading whitespace).
_load_images_env_key_in_file() {
  local key="$1" file="$2"
  [ -f "$file" ] || return 1
  grep -qE "^[[:space:]]*(export[[:space:]]+)?${key}=" "$file"
}

load_images_env() {
  local env_file="$1"
  [ -f "$env_file" ] || return 0

  local line key val
  while IFS= read -r line || [ -n "$line" ]; do
    # Skip blank lines and comments.
    case "$line" in
      ''|'#'*) continue ;;
    esac
    # Not a KEY=VAL line (no '=' anywhere) — ignore. Checked on the RAW line,
    # before the `export ` prefix is stripped below: stripping first would
    # change $key without touching a no-'=' $line (e.g. a hand-written
    # `export FOO` with no value), so the old "did stripping do anything"
    # check below would wrongly say yes and fall through to exporting FOO
    # with the whole original line as its garbage value.
    case "$line" in
      *=*) ;;
      *) continue ;;
    esac
    key="${line%%=*}"
    val="${line#*=}"
    key="${key#export }"
    # Not a valid identifier (must start with a letter/underscore, and
    # contain only letters/digits/underscores after that) — ignore. POSIX
    # case globs, no regex: reject on a bad first character, then reject if
    # anything outside [A-Za-z0-9_] remains anywhere in the string.
    case "$key" in
      [A-Za-z_]*) : ;;
      *) continue ;;
    esac
    case "$key" in
      *[!A-Za-z0-9_]*) continue ;;
    esac

    # Shell wins over everything. No `${!key}` indirection in POSIX sh, so
    # use eval to ask "is $key set" by name.
    eval "_load_images_env_is_set=\${${key}+x}"
    if [ -n "$_load_images_env_is_set" ]; then
      continue
    fi
    # A value already declared in ./.env wins too — don't fight a local override.
    if _load_images_env_key_in_file "$key" "./.env"; then
      continue
    fi

    export "$key=$val"
  done < "$env_file"
}
