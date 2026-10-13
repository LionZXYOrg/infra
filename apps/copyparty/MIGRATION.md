```bash
set -euo pipefail
: "${KUBECONFIG:?}"
kubectl --kubeconfig "$KUBECONFIG" apply -f infrastructure/namespaces/copyparty.yaml
kubectl --kubeconfig "$KUBECONFIG" apply -f apps/copyparty/storage.yaml
kubectl --kubeconfig "$KUBECONFIG" apply -f apps/copyparty/restore-pod.yaml
kubectl --kubeconfig "$KUBECONFIG" -n copyparty wait pod/restore --for=condition=Ready --timeout=300s
ssh lionzxy@home.kulikov.uk 'sudo -n docker stop qbittorrent copyparty' > /dev/null
ssh lionzxy@home.kulikov.uk 'sudo -n tar -C /home/docker/app/copyparty/data -cpf - .' | kubectl --kubeconfig "$KUBECONFIG" -n copyparty exec -i restore -- tar -xpf - -C /restore/copyparty-data
ssh lionzxy@home.kulikov.uk 'sudo -n tar -C /home/docker/app/copyparty/config -cpf - .' | kubectl --kubeconfig "$KUBECONFIG" -n copyparty exec -i restore -- tar -xpf - -C /restore/copyparty-config
kubectl --kubeconfig "$KUBECONFIG" -n copyparty delete pod restore --wait=true
```
