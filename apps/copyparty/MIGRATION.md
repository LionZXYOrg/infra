```bash
set -euo pipefail
: "${KUBECONFIG:?}"
kubectl --kubeconfig "$KUBECONFIG" apply -f infrastructure/namespaces/copyparty.yaml
kubectl --kubeconfig "$KUBECONFIG" apply -f apps/copyparty/storage.yaml
kubectl --kubeconfig "$KUBECONFIG" apply -f apps/copyparty/restore-pod.yaml
kubectl --kubeconfig "$KUBECONFIG" -n copyparty wait pod/restore --for=condition=Ready --timeout=300s
ssh root@192.168.1.109 'docker stop qbittorrent copyparty' > /dev/null
ssh root@192.168.1.109 'tar -C /home/docker/app/copyparty/data -cpf - .' | kubectl --kubeconfig "$KUBECONFIG" -n copyparty exec -i restore -- tar -xpf - -C /restore/copyparty-data
ssh root@192.168.1.109 'tar -C /home/docker/app/copyparty/config -cpf - .' | kubectl --kubeconfig "$KUBECONFIG" -n copyparty exec -i restore -- tar -xpf - -C /restore/copyparty-config
kubectl --kubeconfig "$KUBECONFIG" -n copyparty delete pod restore --wait=true
```

```bash
set -euo pipefail
: "${KUBECONFIG:?}"
git switch master
git pull --ff-only
git switch -c codex/activate-copyparty
python3 scripts/finish_migration.py copyparty
git add apps/copyparty clusters/home/copyparty.yaml tests/migrations/copyparty.yaml
git commit -m "Activate copyparty and remove restore helpers"
git push -u origin HEAD
gh pr create --base master --title "Activate copyparty" --body "Enable copyparty after the data restore. Remove temporary restore files."
gh pr checks --watch
gh pr merge --squash
flux --kubeconfig "$KUBECONFIG" reconcile kustomization flux-system --with-source -n flux-system --timeout=20m
flux --kubeconfig "$KUBECONFIG" reconcile kustomization copyparty --with-source -n flux-system --timeout=20m
```
