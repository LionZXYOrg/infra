```bash
set -euo pipefail
: "${KUBECONFIG:?}"
SSH=(ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o HostKeyAlias=home.kulikov.uk -o ConnectTimeout=10 lionzxy@192.168.1.109)
"${SSH[@]}" 'sudo -n test -d /home/docker/app/localcontentshare/data'
"${SSH[@]}" 'test -z "$(sudo -n docker ps --filter name=local-content-share --format "{{.ID}}")"'
kubectl --kubeconfig "$KUBECONFIG" apply -f infrastructure/namespaces/localcontentshare.yaml
kubectl --kubeconfig "$KUBECONFIG" -n localcontentshare apply -f apps/localcontentshare/storage.yaml
kubectl --kubeconfig "$KUBECONFIG" -n localcontentshare apply -f apps/localcontentshare/restore-pod.yaml
kubectl --kubeconfig "$KUBECONFIG" -n localcontentshare wait pod/restore --for=condition=Ready --timeout=300s
"${SSH[@]}" 'sudo -n tar -C /home/docker/app/localcontentshare/data -cpf - .' | kubectl --kubeconfig "$KUBECONFIG" -n localcontentshare exec -i restore -- tar -xpf - -C /restore/localcontentshare-data
kubectl --kubeconfig "$KUBECONFIG" -n localcontentshare delete pod restore --wait=true --timeout=120s
```
