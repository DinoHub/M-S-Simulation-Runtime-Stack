#!/usr/bin/env bash
# Started by TEVV once its side is up. Must return once the stack is started.
set -euo pipefail
cd "$(dirname "$0")"
echo "starting the VIO navigation stack on $TEVV_NETWORK, ROS domain $ROS_DOMAIN_ID"
docker compose -p "tevv-${TEVV_COMPONENT_ID}-${TEVV_RUN_ID}" -f compose.yml up -d
