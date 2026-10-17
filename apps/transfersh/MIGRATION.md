```bash
set -euo pipefail
: "${KUBECONFIG:?}"
kubectl --kubeconfig "$KUBECONFIG" apply -f infrastructure/namespaces/transfersh.yaml
kubectl --kubeconfig "$KUBECONFIG" apply -f apps/transfersh/storage.yaml
kubectl --kubeconfig "$KUBECONFIG" apply -f apps/transfersh/restore-pod.yaml
kubectl --kubeconfig "$KUBECONFIG" -n transfersh wait pod/restore --for=condition=Ready --timeout=300s
ssh root@192.168.1.109 'docker stop transfersh' > /dev/null
ssh root@192.168.1.109 'tar -C /home/docker/app/transfersh/storage -cpf - .' | kubectl --kubeconfig "$KUBECONFIG" -n transfersh exec -i restore -- tar -xpf - -C /restore/transfersh-storage
kubectl --kubeconfig "$KUBECONFIG" -n transfersh delete pod restore --wait=true
```

```bash
set -euo pipefail
: "${KUBECONFIG:?}"
git switch master
git pull --ff-only
git switch -c codex/activate-transfersh
python3 scripts/finish_migration.py transfersh
git add apps/transfersh clusters/home/transfersh.yaml tests/migrations/transfersh.yaml
git commit -m "Activate transfersh and remove restore helpers"
git push -u origin HEAD
gh pr create --base master --title "Activate transfersh" --body "Enable transfersh after the data restore. Remove temporary restore files."
gh pr checks --watch
gh pr merge --squash
flux --kubeconfig "$KUBECONFIG" reconcile kustomization flux-system --with-source -n flux-system --timeout=20m
flux --kubeconfig "$KUBECONFIG" reconcile kustomization transfersh --with-source -n flux-system --timeout=20m
```
