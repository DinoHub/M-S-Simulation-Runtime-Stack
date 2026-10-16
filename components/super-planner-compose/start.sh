#!/usr/bin/env bash
# Started by TEVV once its stack is up. Must return once SUPER is started.
set -euo pipefail
cd "$(dirname "$0")"
echo "starting SUPER on $TEVV_NETWORK, ROS domain $ROS_DOMAIN_ID"
docker compose -p "tevv-${TEVV_COMPONENT_ID}-${TEVV_RUN_ID}" -f compose.yml up -d
