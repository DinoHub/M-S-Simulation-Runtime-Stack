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
echo "Checking Python deps for tools/generate_scenario.py..."

# launch.sh regenerates templated scenarios with bare `python3`, so these must
# resolve in the SYSTEM interpreter — a venv would not be picked up. On a
# PEP 668 host (Ubuntu 26.04+) `pip install` refuses anyway, so apt is the
# route. Keep the module->package mapping in step with tools/requirements.txt.
PY_OK=true
for mod_pkg in "jinja2:python3-jinja2" "dotenv:python3-dotenv"; do
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
echo "1. Review environment configuration:"
echo "   nano .env"
echo
echo "2. Log in to the registry holding the sim images (dhdevspace/auto_mns is"
echo "   private — pulls fail with 'pull access denied' without this):"
echo "   docker login"
echo
echo "3. Launch the stack:"
echo "   ./launch.sh"
echo