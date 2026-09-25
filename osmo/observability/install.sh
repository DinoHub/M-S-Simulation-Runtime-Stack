#!/usr/bin/env bash
# Observability for the local OSMO cluster: the run registry, and Grafana
# pointed at it. Called as `osmo/setup-local-osmo.sh observability`; safe to
# re-run -- every step checks or applies, none recreates.
#
#   registry   CNPG Postgres `tevv-registry` in namespace tevv, on the service
#              node, reachable from the host at <osmo-worker IP>:30432
#   schema     osmo/registry.py migrate (observability/registry/schema.sql)
#   grafana    the host's Grafana (default the metrics-grafana container on
#              :3000) joins the kind network and gets the registry datasource
#              and the "TEVV campaign progress" dashboard
#
# Overrides: GRAFANA_URL, GRAFANA_AUTH (user:password), GRAFANA_CONTAINER
# (empty to skip the network join, for a Grafana that can already reach the
# kind nodes), SKIP_GRAFANA=1.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OSMO_DIR="$(dirname "$HERE")"
export PATH="$HOME/.local/bin:$PATH"
GRAFANA_URL="${GRAFANA_URL:-http://localhost:3000}"
GRAFANA_AUTH="${GRAFANA_AUTH:-admin:admin}"
GRAFANA_CONTAINER="${GRAFANA_CONTAINER-metrics-grafana}"

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

[[ "${SKIP_GRAFANA:-0}" == 1 ]] && { echo "grafana skipped"; exit 0; }
echo "== grafana ($GRAFANA_URL)"
if [[ -n "$GRAFANA_CONTAINER" ]] && docker inspect "$GRAFANA_CONTAINER" >/dev/null 2>&1; then
  # Docker isolates bridge networks from each other; Grafana reaches the
  # NodePort only from the kind network.
  docker inspect "$GRAFANA_CONTAINER" --format '{{json .NetworkSettings.Networks}}' | grep -q '"kind"' \
    || docker network connect kind "$GRAFANA_CONTAINER"
fi
ADDR="$(kubectl get node osmo-worker -o jsonpath='{.status.addresses[?(@.type=="InternalIP")].address}'):$(
  kubectl -n tevv get svc tevv-registry-nodeport -o jsonpath='{.spec.ports[0].nodePort}')"
PASS="$(kubectl -n tevv get secret tevv-registry-grafana -o jsonpath='{.data.password}' | base64 -d)"
DS="$(REGISTRY_ADDR="$ADDR" REGISTRY_GRAFANA_PASSWORD="$PASS" python3 -c '
import os, sys
print(os.path.expandvars(open(sys.argv[1]).read()))' "$HERE/grafana/datasource-registry.json")"
if curl -fsS -u "$GRAFANA_AUTH" "$GRAFANA_URL/api/datasources/uid/tevv-registry" >/dev/null 2>&1; then
  curl -fsS -u "$GRAFANA_AUTH" -X PUT -H 'Content-Type: application/json' \
    "$GRAFANA_URL/api/datasources/uid/tevv-registry" -d "$DS" >/dev/null
else
  curl -fsS -u "$GRAFANA_AUTH" -X POST -H 'Content-Type: application/json' \
    "$GRAFANA_URL/api/datasources" -d "$DS" >/dev/null
fi
curl -fsS -u "$GRAFANA_AUTH" "$GRAFANA_URL/api/datasources/uid/tevv-registry/health" \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print("datasource:", d.get("status"), d.get("message"))'
python3 -c '
import json, sys
print(json.dumps({"dashboard": json.load(open(sys.argv[1])), "overwrite": True}))' \
  "$HERE/grafana/tevv-campaign-progress.json" \
  | curl -fsS -u "$GRAFANA_AUTH" -X POST -H 'Content-Type: application/json' \
      "$GRAFANA_URL/api/dashboards/db" -d @- \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print("dashboard:", sys.argv[1] + d["url"])' "$GRAFANA_URL"
