#!/usr/bin/env bash
# Submit one tevv-campaign-run workflow for a generated stack.
#
#   argo/submit.sh STACK_DIR [name=value ...]     # e.g. fly=true estimator=true record_sec=120
#
# STACK_DIR is a generated stack on the host, under the checkout the cluster nodes mount
# at /workspace (kind: osmo/kind-osmo-cluster-config.gpu.yaml). Images default to the v1
# image set; override any with RUNTIME_HOST_IMAGE, BRIDGE_IMAGE, ARDUPILOT_IMAGE,
# VIO_ESTIMATOR_IMAGE, SIM_REAL_EVAL_IMAGE. WAIT=1 waits and prints the result.
# LOCAL_IMAGES=1 drops the @sha256 digests: images side-loaded into a dev cluster's
# nodes (kind load) keep their tag but lose the registry digest, so a digest reference
# can never match them. A cluster that pulls from a registry keeps the digests.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=${MNS_WORKSPACE_HOST_ROOT:-$(git -C "$HERE" rev-parse --path-format=absolute --git-common-dir | sed 's#/\.git$##')}
NS=${NS:-tevv-argo}
ARGO=${ARGO:-argo}
stack=$(cd "${1:?stack dir}" && pwd); shift
case "$stack" in "$ROOT"/*) ;; *) echo "submit: $stack is not under $ROOT (the nodes' /workspace)" >&2; exit 2 ;; esac
node_stack="/workspace/${stack#"$ROOT"/}"

read -r host bridge ardupilot estimator evaluator < <(python3 - "$HERE/../images/image-set.generated.yaml" <<'PY'
import sys, yaml
i = yaml.safe_load(open(sys.argv[1]))["image_sets"]["v1"]["images"]
print(i["simulators"]["tevv_runtime_host"], i["ros2_bridge"], i["autopilots"]["ardupilot"],
      i["vio_estimator"], i["sim_real_eval"])
PY
)
kubectl -n "$NS" create configmap tevv-run-files --from-file="$HERE/../osmo/files" \
  --from-file="$HERE/files" --dry-run=client -o yaml | kubectl -n "$NS" apply -f - >/dev/null
kubectl -n "$NS" apply -f "$HERE/workflows/tevv-campaign-run.yaml" >/dev/null

if [ -n "${LOCAL_IMAGES:-}" ]; then
  host=${host%@*}; bridge=${bridge%@*}; ardupilot=${ardupilot%@*}; estimator=${estimator%@*}; evaluator=${evaluator%@*}
fi
params=(-p "stack_dir=$node_stack"
        -p "runtime_host_image=${RUNTIME_HOST_IMAGE:-$host}"
        -p "bridge_image=${BRIDGE_IMAGE:-$bridge}"
        -p "ardupilot_image=${ARDUPILOT_IMAGE:-$ardupilot}"
        -p "vio_estimator_image=${VIO_ESTIMATOR_IMAGE:-$estimator}"
        -p "sim_real_eval_image=${SIM_REAL_EVAL_IMAGE:-$evaluator}")
for kv in "$@"; do params+=(-p "$kv"); done
wf=$("$ARGO" submit -n "$NS" --from workflowtemplate/tevv-campaign-run "${params[@]}" -o name)
echo "$wf"
if [ -n "${WAIT:-}" ]; then "$ARGO" wait -n "$NS" "$wf"; "$ARGO" get -n "$NS" "$wf" --no-color | sed -n '1,40p'; fi
