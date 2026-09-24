#!/usr/bin/env bash
# One-time setup for the MnS product: take a fresh clone to the point where
# `make dashboard` starts in seconds.
#
#   ./setup.sh                    check the machine, log in, pull images, install packs
#   ./setup.sh --check            check only: change nothing, download nothing
#   ./setup.sh --no-packs         everything except the pack download (do it later)
#   ./setup.sh --packs "--blocks --condo"   install only these packs
#
# Safe to re-run: every step skips what is already done. It never needs sudo;
# where a host package is missing it prints the command to install it.
#
# Steps 4 and 5 are the same make targets `make dashboard` runs first
# (ensure-images, then ensure-demo-packs + stage-authoring-packs), so what
# setup installs is exactly what the dashboard expects.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

CHECK_ONLY=false
INSTALL_PACKS=true
PACKS="${MNS_DEMO_PACKS:---all}"

usage() { sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; }
while [[ $# -gt 0 ]]; do
  case "$1" in
    --check) CHECK_ONLY=true ;;
    --no-packs) INSTALL_PACKS=false ;;
    --packs) PACKS="${2:?--packs needs a selection, e.g. \"--blocks --condo\"}"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

# Interactive logins only when a person is at the terminal.
INTERACTIVE=false
[[ -t 0 && -t 1 ]] && INTERACTIVE=true

PROBLEMS=()
problem() { PROBLEMS+=("$1"); echo "  ✗ $1"; }
ok()      { echo "  ✓ $1"; }
note()    { echo "  • $1"; }
step()    { echo; echo "[$1] $2"; }

echo "========================================"
echo " MnS product setup"
echo "========================================"

# ---------------------------------------------------------------------------
step "1/5" "Checking this machine"

. "$SCRIPT_DIR/tools/check_docker.sh"
if check_docker; then
  ok "Docker is reachable"
  if docker compose version >/dev/null 2>&1; then
    ok "Docker Compose v2 ($(docker compose version --short 2>/dev/null))"
  else
    problem "Docker Compose v2 plugin missing  →  sudo apt-get install -y docker-compose-plugin"
  fi
  if check_nvidia_runtime; then
    ok "NVIDIA GPU and container runtime"
  else
    problem "NVIDIA container runtime not usable (see the message above)"
  fi
else
  problem "Docker is not reachable (see the message above)"
fi

for tool in make curl git python3; do
  if command -v "$tool" >/dev/null 2>&1; then
    ok "$tool"
  else
    problem "$tool missing  →  sudo apt-get install -y $tool"
  fi
done

if command -v python3 >/dev/null 2>&1; then
  if python3 -c 'import yaml' >/dev/null 2>&1; then
    ok "Python PyYAML"
  else
    problem "Python PyYAML missing  →  sudo apt-get install -y python3-yaml"
  fi
  # Only the legacy ./launch.sh stacks render templates with these.
  for mod_pkg in "jinja2:python3-jinja2" "dotenv:python3-dotenv"; do
    mod="${mod_pkg%%:*}"; pkg="${mod_pkg##*:}"
    python3 -c "import $mod" >/dev/null 2>&1 \
      || note "optional: Python $mod (legacy ./launch.sh stacks only)  →  sudo apt-get install -y $pkg"
  done
fi

free_gb=$(df -Pk "$SCRIPT_DIR" | awk 'NR==2 {printf "%d", $4/1024/1024}')
if [[ "$INSTALL_PACKS" == true && "$PACKS" == "--all" && "$free_gb" -lt 50 ]]; then
  problem "only ${free_gb} GB free here; the full pack set needs about 50 GB  →  free space, or use --packs \"--blocks --condo --xfs\""
else
  ok "${free_gb} GB free disk"
fi

if [[ -n "${DISPLAY:-}" ]]; then
  ok "desktop display ($DISPLAY)"
else
  note "no DISPLAY in this shell. Run 'make dashboard' from a terminal on the desktop so ScenarioLab and the simulator can open windows"
fi

# ---------------------------------------------------------------------------
step "2/5" "Local configuration"

if [[ "$CHECK_ONLY" == false ]]; then
  chmod +x launch.sh stop.sh logs.sh setup.sh product.sh tools.sh 2>/dev/null || true
  mkdir -p metrics_outputs logs tmp generated scenarios
fi
if [[ -f .env ]]; then
  ok ".env exists (kept as is)"
elif [[ "$CHECK_ONLY" == true ]]; then
  note ".env will be created from .env.example"
else
  cp .env.example .env
  ok ".env created from .env.example (defaults; nothing to edit)"
fi

# ---------------------------------------------------------------------------
step "3/5" "Accounts"
# Both registries are private: images come from Docker Hub
# (dhdevspace/auto_mns), packs from GitHub releases (DinoHub/TEVV-Airsim,
# read by tools/install_demo_packs.py via GH_TOKEN, GITHUB_TOKEN or gh).

docker_logged_in() {
  local cfg="${DOCKER_CONFIG:-$HOME/.docker}/config.json"
  [[ -f "$cfg" ]] && python3 - "$cfg" <<'PY' 2>/dev/null
import json, sys
cfg = json.load(open(sys.argv[1]))
hub = any("docker.io" in k for k in cfg.get("auths", {}))
sys.exit(0 if hub or cfg.get("credsStore") or cfg.get("credHelpers") else 1)
PY
}
github_logged_in() {
  [[ -n "${GH_TOKEN:-}${GITHUB_TOKEN:-}" ]] && return 0
  command -v gh >/dev/null 2>&1 && gh auth token >/dev/null 2>&1
}

if docker_logged_in; then
  ok "Docker Hub login"
elif [[ "$CHECK_ONLY" == false && "$INTERACTIVE" == true ]]; then
  echo "  Docker Hub login needed for the MnS images. Enter your Docker Hub account:"
  if docker login; then ok "Docker Hub login"; else problem "Docker Hub login failed  →  docker login"; fi
else
  problem "not logged in to Docker Hub  →  docker login"
fi

if github_logged_in; then
  ok "GitHub credentials"
elif [[ "$CHECK_ONLY" == false && "$INTERACTIVE" == true ]] && command -v gh >/dev/null 2>&1; then
  echo "  GitHub login needed for the MnS content packs:"
  if gh auth login --hostname github.com --git-protocol https --web; then
    ok "GitHub credentials"
  else
    problem "GitHub login failed  →  gh auth login"
  fi
elif command -v gh >/dev/null 2>&1; then
  problem "no GitHub credentials  →  gh auth login   (or: export GH_TOKEN=<token>)"
else
  problem "no GitHub credentials  →  install the GitHub CLI (sudo apt-get install -y gh) then gh auth login, or export GH_TOKEN=<token>"
fi

# ---------------------------------------------------------------------------
if [[ "$CHECK_ONLY" == true ]]; then
  echo
  if [[ ${#PROBLEMS[@]} -eq 0 ]]; then
    echo "Check passed. Run ./setup.sh to download the images and packs."
    exit 0
  fi
  echo "Check found ${#PROBLEMS[@]} problem(s); fix them, then run ./setup.sh."
  exit 1
fi

if [[ ${#PROBLEMS[@]} -gt 0 ]]; then
  echo
  echo "========================================"
  echo " Setup stopped: fix these, then run ./setup.sh again"
  echo "========================================"
  for p in "${PROBLEMS[@]}"; do echo "  ✗ $p"; done
  exit 1
fi

# ---------------------------------------------------------------------------
step "4/5" "Container images (several GB the first time)"
if ! make --no-print-directory ensure-images; then
  echo
  echo "Image download failed (message above). Usually: Docker Hub login lacks access to"
  echo "dhdevspace/auto_mns, or the network dropped. Fix it and run ./setup.sh again;"
  echo "images already downloaded are kept."
  exit 1
fi

# ---------------------------------------------------------------------------
if [[ "$INSTALL_PACKS" == true ]]; then
  step "5/5" "Content packs: levels and objects ($PACKS; about 15 GB for --all the first time)"
  if ! make --no-print-directory stage-authoring-packs MNS_DEMO_PACKS="$PACKS"; then
    echo
    echo "Pack install failed (message above). A 404 means your GitHub account cannot"
    echo "read the pack releases: ask your MnS contact for access. Fix it and run"
    echo "./setup.sh again (installed packs are kept), or run ./setup.sh --no-packs to"
    echo "finish without them."
    exit 1
  fi
else
  step "5/5" "Content packs: skipped (--no-packs); 'make dashboard' installs them"
fi

echo
echo "========================================"
echo " Setup complete"
echo "========================================"
echo
echo "Start the product from a terminal on this machine's desktop:"
echo
echo "    make dashboard"
echo
echo "then open http://localhost:3001. Walkthrough: docs/USER_GUIDE.md"
