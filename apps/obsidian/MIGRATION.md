```bash
set -euo pipefail
: "${KUBECONFIG:?}"
kubectl --kubeconfig "$KUBECONFIG" apply -f infrastructure/namespaces/obsidian.yaml
kubectl --kubeconfig "$KUBECONFIG" apply -f apps/obsidian/storage.yaml
kubectl --kubeconfig "$KUBECONFIG" apply -f apps/obsidian/restore-pod.yaml
kubectl --kubeconfig "$KUBECONFIG" -n obsidian wait pod/restore --for=condition=Ready --timeout=300s
ssh lionzxy@home.kulikov.uk 'sudo -n docker stop obsidian-livesync' > /dev/null
ssh lionzxy@home.kulikov.uk 'sudo -n tar -C /home/docker/app/obsidian/couchdb-data -cpf - .' | kubectl --kubeconfig "$KUBECONFIG" -n obsidian exec -i restore -- tar -xpf - -C /restore/obsidian-data
kubectl --kubeconfig "$KUBECONFIG" -n obsidian exec restore -- chown -R 5984:5984 /restore/obsidian-data
kubectl --kubeconfig "$KUBECONFIG" -n obsidian delete pod restore --wait=true
```
