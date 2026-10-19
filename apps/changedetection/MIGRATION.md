```bash
set -euo pipefail
: "${KUBECONFIG:?}"
kubectl --kubeconfig "$KUBECONFIG" apply -f infrastructure/namespaces/changedetection.yaml
kubectl --kubeconfig "$KUBECONFIG" apply -f apps/changedetection/storage.yaml
kubectl --kubeconfig "$KUBECONFIG" apply -f apps/changedetection/restore-pod.yaml
kubectl --kubeconfig "$KUBECONFIG" -n changedetection wait pod/restore --for=condition=Ready --timeout=300s
ssh root@192.168.1.109 'docker stop changedetection changedetection-sockpuppetbrowser-1' > /dev/null
ssh root@192.168.1.109 'tar -C /home/docker/app/changedetection/data -cpf - .' | kubectl --kubeconfig "$KUBECONFIG" -n changedetection exec -i restore -- tar -xpf - -C /restore/changedetection-data
kubectl --kubeconfig "$KUBECONFIG" -n changedetection delete pod restore --wait=true
```

```bash
set -euo pipefail
: "${KUBECONFIG:?}"
git switch master
git pull --ff-only
git switch -c codex/activate-changedetection
python3 scripts/finish_migration.py changedetection
git add apps/changedetection clusters/home/changedetection.yaml tests/migrations/changedetection.yaml
git commit -m "Activate changedetection and remove restore helpers"
git push -u origin HEAD
gh pr create --base master --title "Activate changedetection" --body "Enable changedetection after the data restore. Remove temporary restore files."
gh pr checks --watch
gh pr merge --squash
flux --kubeconfig "$KUBECONFIG" reconcile kustomization flux-system --with-source -n flux-system --timeout=20m
flux --kubeconfig "$KUBECONFIG" reconcile kustomization changedetection --with-source -n flux-system --timeout=20m
```
