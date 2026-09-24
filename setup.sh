#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "========================================"
echo " Simulation Runtime Stack Setup"
echo "========================================"

echo
echo "Making scripts executable..."

chmod +x launch.sh
chmod +x stop.sh
chmod +x logs.sh
chmod +x setup.sh

echo "Done."

echo
echo "Checking environment file..."

if [ ! -f ".env" ]; then
  echo ".env not found. Creating from template..."
  cp .env.example .env
  echo ".env created."
else
  echo ".env already exists."
fi

echo
echo "Creating runtime directories..."

mkdir -p metrics_outputs
mkdir -p logs
mkdir -p tmp

echo "Directories ready."

echo
echo "Checking Docker access..."

# Advisory only — setup should still finish on a machine where Docker is not
# ready yet (the .env and directories above are useful regardless). launch.sh
# enforces this for real.
. "$SCRIPT_DIR/tools/check_docker.sh"
PREFLIGHT_OK=true
if check_docker; then
  echo "Docker reachable."
  if check_nvidia_runtime; then
    echo "NVIDIA container runtime available."
  else
    PREFLIGHT_OK=false
  fi
else
  PREFLIGHT_OK=false
  echo "WARNING: fix the above before running ./launch.sh."
fi

echo
echo "Checking Python deps (scenario generator, image tooling)..."

# launch.sh regenerates templated scenarios with bare `python3`, so these must
# resolve in the SYSTEM interpreter — a venv would not be picked up. On a
# PEP 668 host (Ubuntu 26.04+) `pip install` refuses anyway, so apt is the
# route. Keep the module->package mapping in step with tools/requirements.txt.
PY_OK=true
for mod_pkg in "jinja2:python3-jinja2" "dotenv:python3-dotenv" "yaml:python3-yaml"; do
  mod="${mod_pkg%%:*}"
  pkg="${mod_pkg##*:}"
  if ! python3 -c "import $mod" >/dev/null 2>&1; then
    echo "  MISSING: $mod        (sudo apt-get install -y $pkg)"
    PY_OK=false
  fi
done
if [ "$PY_OK" = true ]; then
  echo "Python deps present."
else
  PREFLIGHT_OK=false
  echo "WARNING: scenario regeneration will fail until the above are installed."
fi

echo
echo "Checking registry and pack credentials..."

# Advisory, like the checks above. Both registries are private: images come
# from Docker Hub (dhdevspace/auto_mns), packs from GitHub releases
# (DinoHub/TEVV-Airsim, read by tools/install_demo_packs.py).
DOCKER_LOGIN_OK=false
docker_config="${DOCKER_CONFIG:-$HOME/.docker}/config.json"
if [ -f "$docker_config" ] && python3 - "$docker_config" <<'PY'
import json, sys
cfg = json.load(open(sys.argv[1]))
hub = any("docker.io" in k for k in cfg.get("auths", {}))
sys.exit(0 if hub or cfg.get("credsStore") or cfg.get("credHelpers") else 1)
PY
then
  DOCKER_LOGIN_OK=true
  echo "Docker Hub login found."
else
  PREFLIGHT_OK=false
  echo "  MISSING: Docker Hub login  (run: docker login)"
fi
GH_LOGIN_OK=false
if [ -n "${GH_TOKEN:-}${GITHUB_TOKEN:-}" ] || { command -v gh >/dev/null 2>&1 && gh auth token >/dev/null 2>&1; }; then
  GH_LOGIN_OK=true
  echo "GitHub credentials found."
else
  PREFLIGHT_OK=false
  echo "  MISSING: GitHub credentials for the pack downloads  (run: gh auth login, or export GH_TOKEN=...)"
fi

echo
echo "========================================"
echo " Setup complete"
echo "========================================"
echo
echo "Next steps:"
echo
if [ "$PREFLIGHT_OK" = false ]; then
  echo "0. Resolve the warnings above (host prerequisites), then re-run ./setup.sh."
  echo
fi
n=1
if [ "$DOCKER_LOGIN_OK" = false ]; then echo "$n. docker login"; n=$((n+1)); fi
if [ "$GH_LOGIN_OK" = false ]; then echo "$n. gh auth login        (or: export GH_TOKEN=<token>)"; n=$((n+1)); fi
echo "$n. ./product.sh setup   # pull the release images (once; several GB)"; n=$((n+1))
echo "$n. make dashboard       # first run downloads ~15 GB of packs, then open http://localhost:3001"
echo
echo "Walkthrough: docs/USER_GUIDE.md"
