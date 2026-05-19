#!/usr/bin/env bash
# Run a dev/user tool from the airsim-tools image against the running stack.
#
#   ./tools.sh                       # list available tools
#   ./tools.sh <name> [args...]      # run a tool (talks to AirSim on 127.0.0.1:41451)
#   ./tools.sh weather_gui           # serves the web panel — open http://localhost:8088 (Ctrl-C to stop)
#
# Web tools (PORT = ..., e.g. weather_gui) stay attached and serve a port; under
# the stack's host networking that's just localhost:<port>. A native-window tool
# (GUI = True) would print host instructions instead — run those on the host:
#   pip install -e <cosys-airsim>/rpc-clients/python
#   python <cosys-airsim>/rpc-clients/python/tools/run.py <name>
# (set AIRSIM_HOST / AIRSIM_PORT if the simulator isn't on localhost:41451.)
#
# Image override: AIRSIM_TOOLS_IMAGE in .env (default dhdevspace/auto_mns:airsim-tools-latest).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ -f ".env" ]; then
  set -a
  source .env
  set +a
fi

CONFIG_ROOT="${CONFIG_ROOT:-./config}"
export CONFIG_ROOT="$(cd "$CONFIG_ROOT" && pwd)"

exec docker compose -f docker-compose-tools.yml --profile tools run --rm tools "$@"
