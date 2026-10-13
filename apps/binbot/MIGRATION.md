```bash
set -euo pipefail
: "${KUBECONFIG:?}"
kubectl --kubeconfig "$KUBECONFIG" apply -f infrastructure/namespaces/binbot.yaml
kubectl --kubeconfig "$KUBECONFIG" apply -f apps/binbot/storage.yaml
kubectl --kubeconfig "$KUBECONFIG" apply -f apps/binbot/restore-pod.yaml
kubectl --kubeconfig "$KUBECONFIG" -n binbot wait pod/restore --for=condition=Ready --timeout=300s
ssh lionzxy@home.kulikov.uk 'sudo -n docker stop greenwich-bin-bot' > /dev/null
ssh lionzxy@home.kulikov.uk 'sudo -n tar -C /home/docker/app/binbot/data -cpf - .' | kubectl --kubeconfig "$KUBECONFIG" -n binbot exec -i restore -- tar -xpf - -C /restore/binbot-data
kubectl --kubeconfig "$KUBECONFIG" -n binbot delete pod restore --wait=true
```
