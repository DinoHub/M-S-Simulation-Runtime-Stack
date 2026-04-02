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
echo "========================================"
echo " Setup complete"
echo "========================================"
echo
echo "Next steps:"
echo
echo "1. Review environment configuration:"
echo "   nano .env"
echo
echo "2. Launch the stack:"
echo "   ./launch.sh"
echo