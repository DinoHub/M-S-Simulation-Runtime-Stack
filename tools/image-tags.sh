# shellcheck shell=bash disable=SC2034  # TAG_STATE and TAG_DIGESTS are read by the sourcing script
# Sourced, not run: does a local image tag point at the digest the catalog
# pins for it? Used by tools/ensure-images.sh, tools/doctor.sh and
# tools/pull-all-images.sh.
#
# A tag is only a name. When a registry tag is republished in place (the
# -v1.0.0 tags were, on 2026-10-07), every machine that pulled it before keeps
# the old image under that name, and `docker pull repo:tag@digest` fetches the
# pinned image without moving the tag. Development mode runs the bare tags, so
# without these checks it runs the old image while every digest check passes.
#
#   tag_state REF DIGEST   sets TAG_STATE to one of
#                            absent      no local image under REF
#                            at-pin      REF's image has DIGEST
#                            built-here  REF's image has no registry digest at
#                                        all (built on this machine, classic
#                                        image store)
#                            stale       REF's image has other digests only
#                          and TAG_DIGESTS to the image's digests (sha256:...,
#                          space separated)
#   pin_present REF DIGEST the pinned image is in the local store
#   point_tag_at_pin REF DIGEST
#                          docker tag repo@DIGEST REF (no network; the pinned
#                          image must be present)
#
# MNS_KEEP_LOCAL_TAGS=1 keeps a stale tag where it is: for an image built here
# under the release tag on a containerd image store, which gives local builds
# a digest too, so they cannot be told apart from an old pull.

tag_repo() {
  local name="${1%@*}"
  # The tag is whatever follows the last ':' after the last '/' (a registry
  # host may carry a port).
  if [[ "${name##*/}" == *:* ]]; then
    name="${name%:*}"
  fi
  printf '%s' "$name"
}

tag_state() {
  local ref="$1" digest="$2" out
  TAG_STATE=absent
  TAG_DIGESTS=""
  if ! out="$(docker image inspect -f '{{range .RepoDigests}}{{println .}}{{end}}' "$ref" 2>/dev/null)"; then
    return 0
  fi
  local line digests=()
  while IFS= read -r line; do
    [[ -n "$line" ]] && digests+=("${line##*@}")
  done <<<"$out"
  TAG_DIGESTS="${digests[*]}"
  if [[ "${#digests[@]}" -eq 0 ]]; then
    TAG_STATE=built-here
  elif [[ " $TAG_DIGESTS " == *" $digest "* ]]; then
    TAG_STATE=at-pin
  else
    TAG_STATE=stale
  fi
}

pin_present() {
  docker image inspect "$(tag_repo "$1")@$2" >/dev/null 2>&1
}

point_tag_at_pin() {
  docker tag "$(tag_repo "$1")@$2" "$1"
}

keep_local_tags() {
  [[ "${MNS_KEEP_LOCAL_TAGS:-0}" == 1 ]]
}
