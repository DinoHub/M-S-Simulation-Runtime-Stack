#!/usr/bin/env bash
# Observability for the local OSMO cluster: the run registry, and Grafana
# pointed at it. Called as `osmo/setup-local-osmo.sh observability`; safe to
# re-run -- every step checks or applies, none recreates.
#
#   registry   CNPG Postgres `tevv-registry` in namespace tevv, on the service
#              node, reachable from the host at <osmo-worker IP>:30432
#   schema     osmo/registry.py migrate (observability/registry/schema.sql)
#   logs       Loki (grafana/loki 7.3.0, one process on the service node, 30 days,
#              <osmo-worker IP>:31100) and Alloy (grafana/alloy 1.12.1, a
#              DaemonSet reading every node's container logs: OSMO workflow
#              pods and the OSMO control plane)
#   grafana    the host's Grafana (default the metrics-grafana container on
#              :3000) joins the kind network and gets the registry and Loki
#              datasources and the "TEVV campaign progress" and "TEVV run
#              logs" dashboards
#
# Overrides: GRAFANA_URL, GRAFANA_AUTH (user:password), GRAFANA_CONTAINER
# (empty to skip the network join, for a Grafana that can already reach the
# kind nodes), SKIP_LOGS=1, SKIP_GRAFANA=1.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OSMO_DIR="$(dirname "$HERE")"
export PATH="$HOME/.local/bin:$PATH"
GRAFANA_URL="${GRAFANA_URL:-http://localhost:3000}"
GRAFANA_AUTH="${GRAFANA_AUTH:-admin:admin}"
GRAFANA_CONTAINER="${GRAFANA_CONTAINER-metrics-grafana}"
LOKI_CHART_VERSION=7.3.0
ALLOY_CHART_VERSION=1.12.1

kubectl get crd clusters.postgresql.cnpg.io >/dev/null 2>&1 \
  || { echo "the CNPG operator is not installed; run setup-local-osmo.sh gpu|cpu first" >&2; exit 1; }

echo "== registry"
kubectl create namespace tevv --dry-run=client -o yaml | kubectl apply -f - >/dev/null
# Grafana's read-only role: its password is made once and kept, so re-running
# this never breaks a datasource that already works.
if ! kubectl -n tevv get secret tevv-registry-grafana >/dev/null 2>&1; then
  kubectl -n tevv create secret generic tevv-registry-grafana --type=kubernetes.io/basic-auth \
    --from-literal=username=grafana_ro --from-literal=password="$(openssl rand -hex 16)" >/dev/null
fi
kubectl -n tevv label secret tevv-registry-grafana cnpg.io/reload=true --overwrite >/dev/null
kubectl apply -f "$HERE/registry/registry.yaml"
kubectl -n tevv wait --for=condition=Ready cluster/tevv-registry --timeout=300s

echo "== schema"
# The operator creates grafana_ro shortly after the cluster is Ready; the
# schema grants it SELECT only if it exists, so wait for it first.
for _ in $(seq 30); do
  kubectl -n tevv get cluster tevv-registry -o jsonpath='{.status.managedRolesStatus.byStatus.reconciled}' \
    | grep -q grafana_ro && break
  sleep 2
done
python3 "$OSMO_DIR/registry.py" migrate

node_ip() {
  kubectl get node osmo-worker -o jsonpath='{.status.addresses[?(@.type=="InternalIP")].address}'
}

if [[ "${SKIP_LOGS:-0}" != 1 ]]; then
  echo "== logs"
  helm repo list 2>/dev/null | grep -q '^grafana\b' \
    || helm repo add grafana https://grafana.github.io/helm-charts >/dev/null
  helm repo update grafana >/dev/null
  helm upgrade --install loki grafana/loki --version "$LOKI_CHART_VERSION" -n tevv \
    -f "$HERE/loki/values.yaml" --wait --timeout 5m >/dev/null
  kubectl apply -f "$HERE/loki/service.yaml"
  helm upgrade --install alloy grafana/alloy --version "$ALLOY_CHART_VERSION" -n tevv \
    -f "$HERE/alloy/values.yaml" --set-file alloy.configMap.content="$HERE/alloy/config.alloy" \
    --wait --timeout 5m >/dev/null
  kubectl -n tevv rollout status daemonset/alloy --timeout=120s
  for _ in $(seq 30); do
    curl -fsS "http://$(node_ip):31100/ready" >/dev/null 2>&1 && break
    sleep 2
  done
  echo "loki: $(curl -fsS "http://$(node_ip):31100/ready")"
fi

[[ "${SKIP_GRAFANA:-0}" == 1 ]] && { echo "grafana skipped"; exit 0; }
echo "== grafana ($GRAFANA_URL)"
if [[ -n "$GRAFANA_CONTAINER" ]] && docker inspect "$GRAFANA_CONTAINER" >/dev/null 2>&1; then
  # Docker isolates bridge networks from each other; Grafana reaches the
  # NodePort only from the kind network.
  docker inspect "$GRAFANA_CONTAINER" --format '{{json .NetworkSettings.Networks}}' | grep -q '"kind"' \
    || docker network connect kind "$GRAFANA_CONTAINER"
fi
ADDR="$(node_ip):$(
  kubectl -n tevv get svc tevv-registry-nodeport -o jsonpath='{.spec.ports[0].nodePort}')"
PASS="$(kubectl -n tevv get secret tevv-registry-grafana -o jsonpath='{.data.password}' | base64 -d)"
DS="$(REGISTRY_ADDR="$ADDR" REGISTRY_GRAFANA_PASSWORD="$PASS" python3 -c '
import os, sys
print(os.path.expandvars(open(sys.argv[1]).read()))' "$HERE/grafana/datasource-registry.json")"
put_datasource() {  # uid, rendered JSON
  if curl -fsS -u "$GRAFANA_AUTH" "$GRAFANA_URL/api/datasources/uid/$1" >/dev/null 2>&1; then
    curl -fsS -u "$GRAFANA_AUTH" -X PUT -H 'Content-Type: application/json' \
      "$GRAFANA_URL/api/datasources/uid/$1" -d "$2" >/dev/null
  else
    curl -fsS -u "$GRAFANA_AUTH" -X POST -H 'Content-Type: application/json' \
      "$GRAFANA_URL/api/datasources" -d "$2" >/dev/null
  fi
  curl -fsS -u "$GRAFANA_AUTH" "$GRAFANA_URL/api/datasources/uid/$1/health" \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); print("datasource", sys.argv[1] + ":", d.get("status"), d.get("message"))' "$1"
}
put_datasource tevv-registry "$DS"
if [[ "${SKIP_LOGS:-0}" != 1 ]]; then
  put_datasource tevv-loki "$(LOKI_ADDR="$(node_ip):31100" python3 -c '
import os, sys
print(os.path.expandvars(open(sys.argv[1]).read()))' "$HERE/grafana/datasource-loki.json")"
fi
for board in tevv-campaign-progress tevv-run-logs; do
  [[ -f "$HERE/grafana/$board.json" ]] || continue
  python3 -c '
import json, sys
print(json.dumps({"dashboard": json.load(open(sys.argv[1])), "overwrite": True}))' \
    "$HERE/grafana/$board.json" \
    | curl -fsS -u "$GRAFANA_AUTH" -X POST -H 'Content-Type: application/json' \
        "$GRAFANA_URL/api/dashboards/db" -d @- \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); print("dashboard:", sys.argv[1] + d["url"])' "$GRAFANA_URL"
done
