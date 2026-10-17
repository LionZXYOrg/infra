```bash
set -euo pipefail
: "${KUBECONFIG:?}"
kubectl --kubeconfig "$KUBECONFIG" apply -f infrastructure/namespaces/warden.yaml
kubectl --kubeconfig "$KUBECONFIG" apply -f apps/warden/storage.yaml
kubectl --kubeconfig "$KUBECONFIG" apply -f apps/warden/restore-pod.yaml
kubectl --kubeconfig "$KUBECONFIG" -n warden wait pod/restore --for=condition=Ready --timeout=300s
ssh root@192.168.1.109 'docker stop vaultwarden' > /dev/null
ssh root@192.168.1.109 'tar -C /home/docker/app/warden/data -cpf - .' | kubectl --kubeconfig "$KUBECONFIG" -n warden exec -i restore -- tar -xpf - -C /restore/warden-data
kubectl --kubeconfig "$KUBECONFIG" -n warden exec restore -- chown -R 0:0 /restore/warden-data
kubectl --kubeconfig "$KUBECONFIG" -n warden delete pod restore --wait=true
```

```bash
set -euo pipefail
: "${KUBECONFIG:?}"
git switch master
git pull --ff-only
git switch -c codex/activate-warden
python3 scripts/finish_migration.py warden
git add apps/warden clusters/home/warden.yaml tests/migrations/warden.yaml
git commit -m "Activate warden and remove restore helpers"
git push -u origin HEAD
gh pr create --base master --title "Activate warden" --body "Enable warden after the data restore. Remove temporary restore files."
gh pr checks --watch
gh pr merge --squash
flux --kubeconfig "$KUBECONFIG" reconcile kustomization flux-system --with-source -n flux-system --timeout=20m
flux --kubeconfig "$KUBECONFIG" reconcile kustomization warden --with-source -n flux-system --timeout=20m
```
