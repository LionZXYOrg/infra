```bash
set -euo pipefail
: "${KUBECONFIG:?}"
kubectl --kubeconfig "$KUBECONFIG" apply -f infrastructure/namespaces/changedetection.yaml
kubectl --kubeconfig "$KUBECONFIG" apply -f apps/changedetection/storage.yaml
kubectl --kubeconfig "$KUBECONFIG" apply -f apps/changedetection/restore-pod.yaml
kubectl --kubeconfig "$KUBECONFIG" -n changedetection wait pod/restore --for=condition=Ready --timeout=300s
ssh lionzxy@home.kulikov.uk 'sudo -n docker stop changedetection changedetection-sockpuppetbrowser-1' > /dev/null
ssh lionzxy@home.kulikov.uk 'sudo -n tar -C /home/docker/app/changedetection/data -cpf - .' | kubectl --kubeconfig "$KUBECONFIG" -n changedetection exec -i restore -- tar -xpf - -C /restore/changedetection-data
kubectl --kubeconfig "$KUBECONFIG" -n changedetection delete pod restore --wait=true
```
