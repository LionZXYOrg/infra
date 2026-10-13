```bash
set -euo pipefail
: "${KUBECONFIG:?}"
kubectl --kubeconfig "$KUBECONFIG" apply -f infrastructure/namespaces/immich.yaml
kubectl --kubeconfig "$KUBECONFIG" apply -f apps/immich/storage.yaml
kubectl --kubeconfig "$KUBECONFIG" apply -f apps/immich/restore-pod.yaml
kubectl --kubeconfig "$KUBECONFIG" -n immich wait pod/restore --for=condition=Ready --timeout=300s
ssh lionzxy@home.kulikov.uk 'sudo -n docker stop immich_server immich_machine_learning' > /dev/null
ssh lionzxy@home.kulikov.uk 'sudo -n tar -C /home/docker/app/immich/library -cpf - .' | kubectl --kubeconfig "$KUBECONFIG" -n immich exec -i restore -- tar -xpf - -C /restore/immich-library
kubectl --kubeconfig "$KUBECONFIG" -n immich delete pod restore --wait=true
kubectl --kubeconfig "$KUBECONFIG" apply -f apps/immich/restore-db.yaml
kubectl --kubeconfig "$KUBECONFIG" -n immich wait pod/restore-db --for=condition=Ready --timeout=300s
ssh lionzxy@home.kulikov.uk 'sudo -n docker exec -u postgres immich_postgres pg_dump -U postgres -d immich -Fc --no-owner --no-acl' | kubectl --kubeconfig "$KUBECONFIG" -n immich exec -i restore-db -- pg_restore -U postgres -d immich --no-owner --no-acl --exit-on-error
kubectl --kubeconfig "$KUBECONFIG" -n immich delete pod restore-db --wait=true
```
