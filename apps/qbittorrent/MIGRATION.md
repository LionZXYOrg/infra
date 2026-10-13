```bash
set -euo pipefail
: "${KUBECONFIG:?}"
kubectl --kubeconfig "$KUBECONFIG" apply -f infrastructure/namespaces/copyparty.yaml
kubectl --kubeconfig "$KUBECONFIG" apply -f apps/qbittorrent/storage.yaml
kubectl --kubeconfig "$KUBECONFIG" apply -f apps/qbittorrent/restore-pod.yaml
kubectl --kubeconfig "$KUBECONFIG" -n copyparty wait pod/restore --for=condition=Ready --timeout=300s
ssh lionzxy@home.kulikov.uk 'sudo -n docker stop qbittorrent' > /dev/null
ssh lionzxy@home.kulikov.uk 'sudo -n tar -C /home/docker/app/qbittorrent/config -cpf - .' | kubectl --kubeconfig "$KUBECONFIG" -n copyparty exec -i restore -- tar -xpf - -C /restore/qbittorrent-config
kubectl --kubeconfig "$KUBECONFIG" -n copyparty delete pod restore --wait=true
```
