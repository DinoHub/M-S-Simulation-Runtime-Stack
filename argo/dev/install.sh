#!/usr/bin/env bash
# Install Argo Workflows into one namespace of a dev cluster (kind, k3s, OpenShift Local)
# and the service account tevv-campaign-run pods use. Namespace-scoped: the controller
# watches only $NS, so it cannot touch other workloads on the cluster. The CRDs are the
# only cluster-wide objects.
#
#   argo/dev/install.sh            # NS=tevv-argo
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
NS=${NS:-tevv-argo}
VERSION=v4.1.4
SHA256=947bbab7f9c99eb1e6b5ba47866afa531c1f485f3ad2586f6bfbc081a0057498   # namespace-install.yaml
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
curl -fsSL -o "$tmp/install.yaml" \
  "https://github.com/argoproj/argo-workflows/releases/download/$VERSION/namespace-install.yaml"
echo "$SHA256  $tmp/install.yaml" | sha256sum -c -
kubectl get namespace "$NS" >/dev/null 2>&1 || kubectl create namespace "$NS"
kubectl apply --server-side -n "$NS" -f "$tmp/install.yaml"
kubectl apply -n "$NS" -f "$HERE/../cluster/rbac.yaml"
kubectl -n "$NS" rollout status deploy/workflow-controller --timeout=180s
