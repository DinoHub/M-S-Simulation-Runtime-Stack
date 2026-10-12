#!/usr/bin/env bash
# run_level.sh <scenario> <n-drones> [hover_s]
#
# One level of the swarm stress test, entirely through the dashboard API:
# generate the scenario's stack, launch it, run stress.py, stop it. The
# scenario must already be in scenarios/ (make_spec.py, then the Author
# step's Import or POST /api/scenario/import). Writes run-<scenario>.log in
# the current directory; its last line is the JSON summary.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
api=${DASHBOARD_API:-http://localhost:8001}
s=$1; n=$2; hover=${3:-120}

python3 - "$api" "$root/scenarios/$s/ScenarioSpec.yaml" "$s" <<'PY'
import json, sys, urllib.request
api, path, name = sys.argv[1:]
req = urllib.request.Request(f"{api}/api/scenario/generate", headers={"content-type": "application/json"},
                             data=json.dumps({"name": name, "spec_yaml": open(path).read()}).encode())
print("generated", len(json.loads(urllib.request.urlopen(req, timeout=1200).read())["services"]), "services")
PY
curl -sf -m 60 -X POST "$api/api/scenario/stacks/$s/up" -H 'content-type: application/json' -d '{}' >/dev/null
echo "launching $s"
python3 "$here/stress.py" "$s" "$n" "$hover" > "run-$s.log" 2>&1 || true
grep -v '^{' "run-$s.log" | tail -6
# A stop is refused while a launch is still settling; retry until it lands.
until curl -s -m 900 -X POST "$api/api/scenario/stacks/$s/down" -H 'content-type: application/json' -d '{}' \
    | grep -q succeeded; do sleep 15; done
echo "stopped $s"
