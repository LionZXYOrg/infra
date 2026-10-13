```bash
set -euo pipefail
: "${KUBECONFIG:?}"
kubectl --kubeconfig "$KUBECONFIG" apply -f infrastructure/namespaces/portainer.yaml
kubectl --kubeconfig "$KUBECONFIG" apply -f apps/portainer/storage.yaml
kubectl --kubeconfig "$KUBECONFIG" apply -f apps/portainer/restore-pod.yaml
kubectl --kubeconfig "$KUBECONFIG" -n portainer wait pod/restore --for=condition=Ready --timeout=300s
ssh lionzxy@home.kulikov.uk 'sudo -n docker stop portainer' > /dev/null
ssh lionzxy@home.kulikov.uk 'sudo -n tar -C /home/docker/core/portainer-data -cpf - .' | kubectl --kubeconfig "$KUBECONFIG" -n portainer exec -i restore -- tar -xpf - -C /restore/portainer-data
kubectl --kubeconfig "$KUBECONFIG" -n portainer delete pod restore --wait=true
```
