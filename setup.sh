#!/usr/bin/env bash
# One-time setup for the MnS product: check the machine, create .env, log in
# to Docker Hub and pull the images. Content packs are a separate step:
# ./download-packs.sh.
#
#   ./setup.sh            check, configure, log in, pull images
#   ./setup.sh --check    check only: change nothing, download nothing
#
# Safe to re-run: every step skips what is already done. It never needs sudo;
# where a host package is missing it prints the command to install it.
#
# Step 4 is the make target `make dashboard` runs first (ensure-images), so
# what setup pulls is exactly what the dashboard expects.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

CHECK_ONLY=false

usage() { sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; }
while [[ $# -gt 0 ]]; do
  case "$1" in
    --check) CHECK_ONLY=true ;;
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
step "1/4" "Checking this machine"

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
fi

# Images take about 15 GB; packs are checked by ./download-packs.sh.
free_gb=$(df -Pk "$SCRIPT_DIR" | awk 'NR==2 {printf "%d", $4/1024/1024}')
if [[ "$free_gb" -lt 15 ]]; then
  problem "only ${free_gb} GB free here; the images need about 15 GB (content packs up to 16 GB more)  →  free space"
else
  ok "${free_gb} GB free disk (images about 15 GB; content packs up to 16 GB more)"
fi

if [[ -n "${DISPLAY:-}" ]]; then
  ok "desktop display ($DISPLAY)"
else
  note "no DISPLAY in this shell. Run 'make dashboard' from a terminal on the desktop so ScenarioLab and the simulator can open windows"
fi

# ---------------------------------------------------------------------------
step "2/4" "Local configuration"

if [[ "$CHECK_ONLY" == false ]]; then
  chmod +x setup.sh download-packs.sh product.sh tools.sh 2>/dev/null || true
  mkdir -p generated scenarios runs
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
step "3/4" "Docker Hub account"
# The images are private (dhdevspace/auto_mns). GitHub, for the packs, is
# ./download-packs.sh's step.

docker_logged_in() {
  local cfg="${DOCKER_CONFIG:-$HOME/.docker}/config.json"
  [[ -f "$cfg" ]] && python3 - "$cfg" <<'PY' 2>/dev/null
import json, sys
cfg = json.load(open(sys.argv[1]))
hub = any("docker.io" in k for k in cfg.get("auths", {}))
sys.exit(0 if hub or cfg.get("credsStore") or cfg.get("credHelpers") else 1)
PY
}
if docker_logged_in; then
  ok "Docker Hub login"
elif [[ "$CHECK_ONLY" == false && "$INTERACTIVE" == true ]]; then
  echo "  Docker Hub login needed for the MnS images. Enter your Docker Hub account:"
  if docker login; then ok "Docker Hub login"; else problem "Docker Hub login failed  →  docker login"; fi
else
  problem "not logged in to Docker Hub  →  docker login"
fi


# ---------------------------------------------------------------------------
if [[ "$CHECK_ONLY" == true ]]; then
  echo
  if [[ ${#PROBLEMS[@]} -eq 0 ]]; then
    echo "Check passed. Run ./setup.sh to pull the images."
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
step "4/4" "Container images (several GB the first time)"
if ! make --no-print-directory ensure-images; then
  echo
  echo "Image download failed (message above). Usually: Docker Hub login lacks access to"
  echo "dhdevspace/auto_mns, or the network dropped. Fix it and run ./setup.sh again;"
  echo "images already downloaded are kept."
  exit 1
fi

echo
echo "========================================"
echo " Setup complete"
echo "========================================"
echo
echo "Next:"
echo
echo "    ./download-packs.sh     # the starter levels, ~0.8 GB (--all for everything, --list to choose)"
echo "    make dashboard          # from a terminal on the desktop, then http://localhost:3001"
echo
echo "Walkthrough: docs/USER_GUIDE.md"
