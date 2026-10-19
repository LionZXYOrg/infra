```bash
set -euo pipefail
: "${KUBECONFIG:?}"
kubectl --kubeconfig "$KUBECONFIG" apply -f infrastructure/namespaces/portainer.yaml
kubectl --kubeconfig "$KUBECONFIG" apply -f apps/portainer/storage.yaml
kubectl --kubeconfig "$KUBECONFIG" apply -f apps/portainer/restore-pod.yaml
kubectl --kubeconfig "$KUBECONFIG" -n portainer wait pod/restore --for=condition=Ready --timeout=300s
ssh root@192.168.1.109 'docker stop portainer' > /dev/null
ssh root@192.168.1.109 'tar -C /home/docker/core/portainer-data -cpf - .' | kubectl --kubeconfig "$KUBECONFIG" -n portainer exec -i restore -- tar -xpf - -C /restore/portainer-data
kubectl --kubeconfig "$KUBECONFIG" -n portainer delete pod restore --wait=true
```

```bash
set -euo pipefail
: "${KUBECONFIG:?}"
git switch master
git pull --ff-only
git switch -c codex/activate-portainer
python3 scripts/finish_migration.py portainer
git add apps/portainer clusters/home/portainer.yaml tests/migrations/portainer.yaml
git commit -m "Activate portainer and remove restore helpers"
git push -u origin HEAD
gh pr create --base master --title "Activate portainer" --body "Enable portainer after the data restore. Remove temporary restore files."
gh pr checks --watch
gh pr merge --squash
flux --kubeconfig "$KUBECONFIG" reconcile kustomization flux-system --with-source -n flux-system --timeout=20m
flux --kubeconfig "$KUBECONFIG" reconcile kustomization portainer --with-source -n flux-system --timeout=20m
```
