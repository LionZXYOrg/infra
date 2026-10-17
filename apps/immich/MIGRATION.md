```bash
set -euo pipefail
: "${KUBECONFIG:?}"
kubectl --kubeconfig "$KUBECONFIG" apply -f infrastructure/namespaces/immich.yaml
kubectl --kubeconfig "$KUBECONFIG" apply -f apps/immich/storage.yaml
kubectl --kubeconfig "$KUBECONFIG" apply -f apps/immich/restore-pod.yaml
kubectl --kubeconfig "$KUBECONFIG" -n immich wait pod/restore --for=condition=Ready --timeout=300s
ssh root@192.168.1.109 'docker stop immich_server immich_machine_learning' > /dev/null
tar -C /home/lionzxy/tmp-moving/immich -cpf - . | kubectl --kubeconfig "$KUBECONFIG" -n immich exec -i restore -- tar -xpf - -C /restore/immich-library
kubectl --kubeconfig "$KUBECONFIG" -n immich delete pod restore --wait=true
kubectl --kubeconfig "$KUBECONFIG" apply -f apps/immich/restore-db.yaml
kubectl --kubeconfig "$KUBECONFIG" -n immich wait pod/restore-db --for=condition=Ready --timeout=300s
ssh root@192.168.1.109 'docker exec -u postgres immich_postgres pg_dump -U postgres -d immich -Fc --no-owner --no-acl' | kubectl --kubeconfig "$KUBECONFIG" -n immich exec -i restore-db -- pg_restore -U postgres -d immich --no-owner --no-acl --exit-on-error
kubectl --kubeconfig "$KUBECONFIG" -n immich delete pod restore-db --wait=true
for claim in immich-library immich-postgres; do
  volume="$(kubectl --kubeconfig "$KUBECONFIG" -n immich get pvc "$claim" -o jsonpath='{.spec.volumeName}')"
  kubectl --kubeconfig "$KUBECONFIG" get pv "$volume" -o jsonpath='{.metadata.name}{"\t"}{.spec.hostPath.path}{.spec.local.path}{"\t"}{.spec.nodeAffinity.required.nodeSelectorTerms[0].matchExpressions[0].values[0]}{"\n"}'
done
```

```bash
set -euo pipefail
: "${KUBECONFIG:?}"
git switch master
git pull --ff-only
git switch -c codex/activate-immich
python3 scripts/finish_migration.py immich
git add apps/immich clusters/home/immich.yaml tests/migrations/immich.yaml
git commit -m "Activate immich and remove restore helpers"
git push -u origin HEAD
gh pr create --base master --title "Activate immich" --body "Enable immich after the data restore. Remove temporary restore files."
gh pr checks --watch
gh pr merge --squash
flux --kubeconfig "$KUBECONFIG" reconcile kustomization flux-system --with-source -n flux-system --timeout=20m
flux --kubeconfig "$KUBECONFIG" reconcile kustomization immich --with-source -n flux-system --timeout=20m
```
