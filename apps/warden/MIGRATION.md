```bash
set -euo pipefail
: "${KUBECONFIG:?}"
kubectl --kubeconfig "$KUBECONFIG" apply -f infrastructure/namespaces/warden.yaml
kubectl --kubeconfig "$KUBECONFIG" apply -f apps/warden/storage.yaml
kubectl --kubeconfig "$KUBECONFIG" apply -f apps/warden/restore-pod.yaml
kubectl --kubeconfig "$KUBECONFIG" -n warden wait pod/restore --for=condition=Ready --timeout=300s
ssh lionzxy@home.kulikov.uk 'sudo -n docker stop vaultwarden' > /dev/null
ssh lionzxy@home.kulikov.uk 'sudo -n tar -C /home/docker/app/warden/data -cpf - .' | kubectl --kubeconfig "$KUBECONFIG" -n warden exec -i restore -- tar -xpf - -C /restore/warden-data
kubectl --kubeconfig "$KUBECONFIG" -n warden exec restore -- chown -R 0:0 /restore/warden-data
kubectl --kubeconfig "$KUBECONFIG" -n warden delete pod restore --wait=true
```
