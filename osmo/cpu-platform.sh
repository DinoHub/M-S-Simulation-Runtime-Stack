#!/usr/bin/env bash
# Give pool `default` a second platform, `cpu`, on the service node
# (osmo-worker, node_group=service), and load onto that node the images the
# evaluate and aggregate groups run. Idempotent: re-running changes nothing.
#
#   osmo/cpu-platform.sh            # after setup-local-osmo.sh and osmo login
#
# Why. Every task used to land on the GPU node: pool `default` has one platform,
# and its pod template `default_compute` selects node_group=compute. A run's
# evaluation (vio-eval, spawn-eval, validate, verdict) needs no GPU, but it
# queued for the GPU node behind the next run's flight gang -- 138 s on XFS --
# and two evaluations held the node's cores long enough to delay the next
# gang by 31 s. The workflow's `eval` resources name this platform.
#
# What stays on the GPU node. Everything in the `run` group: sim needs the GPU,
# and sim, bridge and vio mount /workspace, which only osmo-worker2 has (the
# kind config's extraMounts). The cpu platform allows no mounts, so a task
# that asks for one there is refused at submit instead of starting on an empty
# hostPath.
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

echo "== pod templates cpu_user, service_node"
# Two templates, both derived from the ones the GPU platform uses:
#   cpu_user      default_user without its nvidia.com/gpu request. OSMO writes
#                 that key into every task, as "0" when no GPU is asked for, and
#                 KAI's admission webhook treats any pod carrying it as a GPU pod
#                 and gives it runtimeClassName nvidia -- which the service node
#                 has no handler for, so the pod never starts ("no runtime for
#                 \"nvidia\" is configured"; XFS run 79).
#   service_node  default_compute (pull policy, dev login) selecting
#                 node_group=service instead of compute.
osmo config show POD_TEMPLATE > "$TMP/pt.json"
if python3 - "$TMP/pt.json" <<'PY'
import copy, json, sys
p = sys.argv[1]; d = json.load(open(p)); changed = False
user = copy.deepcopy(d["default_user"])
for c in user["spec"]["containers"]:
    for part in ("limits", "requests"):
        c.get("resources", {}).get(part, {}).pop("nvidia.com/gpu", None)
node = copy.deepcopy(d["default_compute"])
node["spec"]["nodeSelector"] = {"node_group": "service"}
for name, want in (("cpu_user", user), ("service_node", node)):
    if d.get(name) != want:
        d[name] = want; changed = True
json.dump(d, open(p, "w"), indent=2)
sys.exit(0 if changed else 1)
PY
then
  osmo config update POD_TEMPLATE --file "$TMP/pt.json" --description "cpu_user, service_node: CPU tasks on osmo-worker"
else
  echo "present"
fi

echo "== pool default: platforms default and cpu"
# The user and node templates move from the pool's common list into each
# platform's own: the default platform keeps exactly the merge it had
# (default_ctrl, default_user, default_compute), and cpu gets
# (default_ctrl, cpu_user, service_node).
osmo config show POOL default > "$TMP/pool.json"
if python3 - "$TMP/pool.json" <<'PY'
import json, sys
p = sys.argv[1]; d = json.load(open(p)); before = json.dumps(d, sort_keys=True)
d["common_pod_template"] = ["default_ctrl"]
d["platforms"]["default"]["override_pod_template"] = ["default_user", "default_compute"]
d["platforms"]["cpu"] = {
    "description": "CPU-only tasks on the service node (osmo-worker): evaluation and aggregation",
    "host_network_allowed": False, "privileged_allowed": False,
    "allowed_mounts": [], "default_variables": {}, "resource_validations": [],
    "override_pod_template": ["cpu_user", "service_node"]}
json.dump(d, open(p, "w"), indent=2)
sys.exit(0 if json.dumps(d, sort_keys=True) != before else 1)
PY
then
  osmo config update POOL default --file "$TMP/pool.json" --description "platform cpu on the service node"
else
  echo "present"
fi

echo "== images on the service node"
# The evaluate and aggregate tasks run the bridge image (spawn-eval, validate)
# and the sim_real_eval image (vio-eval, verdict), as the catalog pins them.
mapfile -t refs < <(python3 - "$HERE" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import campaign
_, refs = campaign.pinned_images()
for var in campaign.EVAL_IMAGES:
    print(campaign.split_ref(refs[var])[0])
PY
)
for ref in "${refs[@]}"; do
  if docker exec osmo-worker crictl inspecti "docker.io/$ref" >/dev/null 2>&1; then
    echo "present: $ref"
  else
    kind load docker-image --name osmo --nodes osmo-worker "$ref"
  fi
done

osmo resource list --pool default --platform cpu 2>/dev/null || true
