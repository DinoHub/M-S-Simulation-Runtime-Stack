#!/usr/bin/env bash
# Run by TEVV at teardown, also when the run failed.
set -euo pipefail
cd "$(dirname "$0")"
docker compose -p "tevv-${TEVV_COMPONENT_ID}-${TEVV_RUN_ID}" -f compose.yml down --volumes
