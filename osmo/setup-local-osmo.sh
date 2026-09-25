#!/usr/bin/env bash
# Stand up a local NVIDIA OSMO control plane on a kind cluster.
#
#   osmo/setup-local-osmo.sh cpu     three CPU nodes, no GPU, no sudo anywhere
#   osmo/setup-local-osmo.sh gpu     compute node gets the host GPU (see below)
#   osmo/setup-local-osmo.sh observability
#                                    on a cluster already up: the run registry
#                                    and Grafana's view of it (observability/install.sh)
#
# The GPU variant needs three things done on the host first, all requiring root,
# and none of which this script will attempt:
#
#   sudo nvidia-ctk runtime configure --runtime=docker --set-as-default
#   sudo systemctl restart docker          # this stops every running container
#   sudo sysctl -w fs.inotify.max_user_instances=512
#
# Everything else -- kind, kubectl, helm, the osmo CLI, Go and nvkind -- lives
# under ~/.local and needs no root.
#
# Why the 6.3.1 tag and not the checkout's main: the umbrella chart on main runs
# five bootstrap binaries that no published image contains, and no older commit
# fixes it because the chart directory postdates the release. docs/osmo-mapping.md
# has the full account. The quick-start chart at the 6.3.1 tag bundles postgres,
# redis, localstack and envoy and bootstraps with alpine images, so it matches
# the image it names.
set -euo pipefail

MODE="${1:-cpu}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Not a cluster build: the steps below start by deleting the cluster.
[[ "$MODE" == observability ]] && exec "$HERE/observability/install.sh"
OSMO_SRC="${OSMO_SRC:-$HOME/OSMO}"
OSMO_631="${OSMO_631:-$HOME/OSMO-6.3.1}"
CHART="$OSMO_631/deployments/charts/quick-start"
export PATH="$HOME/.local/bin:$PATH"

case "$MODE" in cpu|gpu) ;; *) echo "usage: $0 [cpu|gpu|observability]" >&2; exit 2 ;; esac

for tool in kind kubectl helm osmo; do
  command -v "$tool" >/dev/null || { echo "missing $tool; see docs/osmo-mapping.md" >&2; exit 1; }
done
if [[ "$MODE" == gpu ]]; then
  command -v nvkind >/dev/null || { echo "missing nvkind" >&2; exit 1; }
  [[ "$(docker info --format '{{.DefaultRuntime}}')" == nvidia ]] || {
    echo "docker's default runtime is not nvidia; run the sudo steps in this file's header" >&2; exit 1; }
fi

echo "== 1/6 the 6.3.1 chart tree"
if [[ ! -d "$CHART" ]]; then
  git -C "$OSMO_SRC" fetch --depth 1 origin refs/tags/6.3.1:refs/tags/6.3.1 || true
  git -C "$OSMO_SRC" worktree add "$OSMO_631" 6.3.1
fi
helm dependency update "$CHART" >/dev/null
grep -q '^version: 1.3.1' "$CHART/Chart.yaml" || { echo "chart is not 1.3.1" >&2; exit 1; }

echo "== 2/6 cluster"
kind delete cluster --name osmo 2>/dev/null || true
if [[ "$MODE" == gpu ]]; then
  nvkind cluster create --config-template="$HERE/kind-osmo-cluster-config.gpu.yaml"
  nvkind cluster print-gpus
else
  kind create cluster --config "$HERE/kind-osmo-cluster-config.yaml"
fi
kubectl config use-context kind-osmo
kubectl wait --for=condition=Ready node --all --timeout=300s

# The quick-start chart selects on node_group; our config sets
# osmo.nvidia.com/node-pool, which KAI uses. Both labels, no conflict.
kubectl label node osmo-worker  node_group=service --overwrite
kubectl label node osmo-worker2 node_group=compute --overwrite

echo "== 3/6 KAI and CloudNativePG"
helm upgrade --install kai-scheduler oci://ghcr.io/nvidia/kai-scheduler/kai-scheduler \
  --version v0.12.10 --create-namespace -n kai-scheduler \
  --set-string 'global.nodeSelector.osmo\.nvidia\.com/node-pool=control-plane' \
  --set-string 'global.affinity.nodeAffinity.requiredDuringSchedulingIgnoredDuringExecution.nodeSelectorTerms[0].matchExpressions[0].key=osmo.nvidia.com/node-pool' \
  --set-string 'global.affinity.nodeAffinity.requiredDuringSchedulingIgnoredDuringExecution.nodeSelectorTerms[0].matchExpressions[0].operator=In' \
  --set-string 'global.affinity.nodeAffinity.requiredDuringSchedulingIgnoredDuringExecution.nodeSelectorTerms[0].matchExpressions[0].values[0]=control-plane' \
  --set "scheduler.additionalArgs[0]=--default-staleness-grace-period=-1s" \
  --set "scheduler.additionalArgs[1]=--update-pod-eviction-condition=true" >/dev/null
kubectl -n kai-scheduler wait --for=condition=Available=True --timeout=5m config.kai.scheduler/kai-config
helm repo add cnpg https://cloudnative-pg.github.io/charts >/dev/null 2>&1 || true
helm repo update cnpg >/dev/null
helm upgrade --install cnpg cnpg/cloudnative-pg --version 0.29.0 -n cnpg-system --create-namespace \
  --set-string 'nodeSelector.osmo\.nvidia\.com/node-pool=control-plane' --wait --timeout 10m >/dev/null

if [[ "$MODE" == gpu ]]; then
  echo "== GPU operator"
  helm repo add nvidia https://helm.ngc.nvidia.com/nvidia >/dev/null 2>&1 || true
  helm repo update nvidia >/dev/null
  # The host already has the driver and the Container Toolkit; let it manage
  # neither, or it fights nvkind's pass-through.
  helm upgrade --install gpu-operator nvidia/gpu-operator --version v25.10.1 \
    -n gpu-operator --create-namespace \
    --set driver.enabled=false --set toolkit.enabled=false --set nfd.enabled=true \
    --wait --timeout 15m >/dev/null
else
  # Workaround 1. The default pod template asks for runtimeClassName: nvidia and
  # the API server rejects every workflow pod without it. The GPU operator ships
  # the real one; on a CPU cluster, map it to runc.
  kubectl apply -f - >/dev/null <<'YAML'
apiVersion: node.k8s.io/v1
kind: RuntimeClass
metadata: {name: nvidia}
handler: runc
YAML
fi

echo "== 4/6 render gate"
# Proves the chart names no binary the image lacks. An empty grep is the point.
render="$(mktemp)"
helm template osmo "$CHART" -n osmo --set global.osmoImageTag=6.3.1 \
  --set-string service.services.postgres.nodeSelector.node_group=service \
  --set-string service.services.redis.nodeSelector.node_group=service \
  --set-string service.services.localstackS3.nodeSelector.node_group=service > "$render"
if grep -qE 'osmo-bootstrap|bootstrap-step|identity-bootstrap|internal-tls-bootstrap|mek-lifecycle|service-auth-bootstrap' "$render"; then
  echo "chart references binaries absent from the published image; wrong tree" >&2; exit 1
fi
rm -f "$render"

echo "== 5/6 install"
helm upgrade --install osmo "$CHART" -n osmo --create-namespace \
  --set global.osmoImageTag=6.3.1 \
  --set-string service.services.postgres.nodeSelector.node_group=service \
  --set-string service.services.redis.nodeSelector.node_group=service \
  --set-string service.services.localstackS3.nodeSelector.node_group=service \
  --timeout 20m
# Deliberately no --wait: every consumer Deployment blocks on a token that the
# next step creates, so waiting here would only burn the timeout.

echo "== 6/6 bootstrap fixes"
# Workaround 2+3, done directly rather than by patching the chart's Job.
#
# That Job addresses http://osmo-service, but the service answers only requests
# arriving through the Envoy gateway and closes the connection otherwise, so
# curl exits 52 and `set -e` kills it. It also carries ttlSecondsAfterFinished:
# 30, so once its retries are exhausted Kubernetes deletes it -- patching it in
# place is a race that is lost as soon as helm's --wait runs longer than the
# job's backoff. Do what the job would have done, against the gateway, and
# write the secret ourselves.
kubectl -n osmo delete secret backend-operator-token --ignore-not-found >/dev/null
kubectl -n osmo delete job tokenboot --ignore-not-found >/dev/null 2>&1
kubectl -n osmo create configmap tokenboot-script --dry-run=client -o yaml \
  --from-literal=boot.sh='set -e
B=http://quick-start.osmo.svc.cluster.local
curl -s -o /dev/null -X POST -H "Content-Type: application/json" \
  "$B/api/auth/user" -d "{\"id\":\"backend-operator\",\"roles\":[\"osmo-backend\"]}"
Y=$(date +%Y)
T=$(curl -s -X POST -G \
  --data-urlencode "expires_at=$((Y+1))-$(date +%m-%d)" \
  --data-urlencode "description=Access token for default backend" \
  --data-urlencode "roles=osmo-backend" \
  "$B/api/auth/user/backend-operator/access_token/backend-operator-token")
TOK=$(printf "%s" "$T" | jq -r "if type==\"object\" then .token else . end")
[ -n "$TOK" ] && [ "$TOK" != "null" ] || { echo "no token: $T" >&2; exit 1; }
kubectl create secret generic backend-operator-token --from-literal=token="$TOK" \
  -n osmo --dry-run=client -o yaml | kubectl apply -f -' | kubectl apply -f - >/dev/null
kubectl apply -f - >/dev/null <<'YAML'
apiVersion: batch/v1
kind: Job
metadata: {name: tokenboot, namespace: osmo}
spec:
  backoffLimit: 3
  template:
    spec:
      restartPolicy: Never
      serviceAccountName: osmo-agent-token-generator
      containers:
      - name: boot
        image: alpine/k8s:1.28.4
        command: ["sh", "/script/boot.sh"]
        volumeMounts: [{name: script, mountPath: /script}]
      volumes:
      - name: script
        configMap: {name: tokenboot-script}
YAML

# Workaround 4. The data credential is created without addressing_style, so the
# S3 client builds virtual-hosted URLs like http://osmo.localstack-s3.osmo:4566.
# Setting addressing_style=path is accepted but not honoured in 6.3.1, so make
# the name resolve instead: <hostname>.<subdomain>.<namespace> is a real
# Kubernetes DNS record, and the bucket is always "osmo".
kubectl -n osmo patch deploy localstack-s3 --type=strategic \
  -p '{"spec":{"template":{"spec":{"hostname":"osmo","subdomain":"localstack-s3"}}}}' >/dev/null
kubectl -n osmo rollout status deploy/localstack-s3 --timeout=180s >/dev/null

echo
echo "waiting for the token (every consumer Deployment blocks on it)"
for _ in $(seq 60); do
  kubectl -n osmo get job tokenboot -o jsonpath='{.status.succeeded}' 2>/dev/null | grep -q 1 && break
  sleep 5
done
kubectl -n osmo get job tokenboot -o jsonpath='{.status.succeeded}' 2>/dev/null | grep -q 1 \
  || { echo "token bootstrap failed; kubectl -n osmo logs job/tokenboot" >&2; exit 1; }
kubectl -n osmo delete job tokenboot configmap/tokenboot-script --ignore-not-found >/dev/null 2>&1

echo "waiting for the control plane to settle"
for _ in $(seq 60); do
  [ "$(kubectl -n osmo get pods --no-headers 2>/dev/null | grep -cvE ' 1/1 .*Running| Completed')" -eq 0 ] && break
  sleep 10
done
kubectl -n osmo get pods --no-headers 2>/dev/null | grep -vE ' 1/1 .*Running| Completed' || true

cat <<'DONE'

Control plane up. Next:

  osmo login http://localhost --method=dev --username=testuser
  osmo pool list
  osmo workflow submit ~/OSMO-6.3.1/cookbook/tutorials/hello_world.yaml
  osmo workflow query hello-osmo-1

DONE
