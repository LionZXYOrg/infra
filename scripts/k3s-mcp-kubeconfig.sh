#!/usr/bin/env bash
# Mint a short-lived Kubernetes read-only credential for a local MCP client.
# Keep the admin kubeconfig OUT of the MCP client's environment.
set -euo pipefail
set +x  # never trace bearer tokens, even if the caller enabled xtrace

if [[ -z "${KUBECONFIG:-}" ]]; then
  echo 'Set KUBECONFIG to your admin kubeconfig before running this script.' >&2
  exit 1
fi

target="${K3S_MCP_KUBECONFIG:-$HOME/.kube/k3s-mcp-readonly.yaml}"
if [[ "$target" != /* ]]; then
  echo 'K3S_MCP_KUBECONFIG must be an absolute path.' >&2
  exit 1
fi

# These calls use the admin kubeconfig only during one-time credential minting.
kubectl -n k3s-mcp get serviceaccount k3s-mcp -o name >/dev/null
server="$(kubectl config view --raw --flatten --minify -o 'jsonpath={.clusters[0].cluster.server}')"
ca="$(kubectl config view --raw --flatten --minify -o 'jsonpath={.clusters[0].cluster.certificate-authority-data}')"
if [[ -z "$server" || -z "$ca" || "$server" != https://* ]]; then
  echo 'Admin kubeconfig must contain an HTTPS API server and a trusted CA.' >&2
  exit 1
fi

# Kubernetes may issue a token shorter than the requested 24h.
token="$(kubectl -n k3s-mcp create token k3s-mcp --duration=24h)"
if [[ -z "$token" ]]; then
  echo 'Kubernetes did not issue a token.' >&2
  exit 1
fi

umask 077
mkdir -p "$(dirname "$target")"
tmp="$(mktemp "${target}.tmp.XXXXXX")"
trap 'rm -f "$tmp"' EXIT
cat > "$tmp" <<KUBECONFIG_YAML
apiVersion: v1
kind: Config
clusters:
  - name: home
    cluster:
      server: $server
      certificate-authority-data: $ca
users:
  - name: k3s-mcp
    user:
      token: $token
contexts:
  - name: home-readonly
    context:
      cluster: home
      user: k3s-mcp
      namespace: default
current-context: home-readonly
KUBECONFIG_YAML
chmod 600 "$tmp"
mv -f "$tmp" "$target"
echo "Wrote $target (short-lived token; regenerate after it expires)."
