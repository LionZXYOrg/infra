```bash
set -euo pipefail
: "${KUBECONFIG:?}"
kubectl --kubeconfig "$KUBECONFIG" apply -f infrastructure/namespaces/transfersh.yaml
kubectl --kubeconfig "$KUBECONFIG" apply -f apps/transfersh/storage.yaml
kubectl --kubeconfig "$KUBECONFIG" apply -f apps/transfersh/restore-pod.yaml
kubectl --kubeconfig "$KUBECONFIG" -n transfersh wait pod/restore --for=condition=Ready --timeout=300s
ssh lionzxy@home.kulikov.uk 'sudo -n docker stop transfersh' > /dev/null
ssh lionzxy@home.kulikov.uk 'sudo -n tar -C /home/docker/app/transfersh/storage -cpf - .' | kubectl --kubeconfig "$KUBECONFIG" -n transfersh exec -i restore -- tar -xpf - -C /restore/transfersh-storage
kubectl --kubeconfig "$KUBECONFIG" -n transfersh delete pod restore --wait=true
```
