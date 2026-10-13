```bash
set -euo pipefail
: "${KUBECONFIG:?}"
kubectl --kubeconfig "$KUBECONFIG" apply -f infrastructure/namespaces/localcontentshare.yaml
kubectl --kubeconfig "$KUBECONFIG" apply -f apps/localcontentshare/storage.yaml
kubectl --kubeconfig "$KUBECONFIG" apply -f apps/localcontentshare/restore-pod.yaml
kubectl --kubeconfig "$KUBECONFIG" -n localcontentshare wait pod/restore --for=condition=Ready --timeout=300s
ssh lionzxy@home.kulikov.uk 'sudo -n docker stop local-content-share' > /dev/null
ssh lionzxy@home.kulikov.uk 'sudo -n tar -C /home/docker/app/localcontentshare/data -cpf - .' | kubectl --kubeconfig "$KUBECONFIG" -n localcontentshare exec -i restore -- tar -xpf - -C /restore/localcontentshare-data
kubectl --kubeconfig "$KUBECONFIG" -n localcontentshare delete pod restore --wait=true
```
