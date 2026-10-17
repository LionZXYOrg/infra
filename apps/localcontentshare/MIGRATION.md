```bash
set -euo pipefail
: "${KUBECONFIG:?}"
kubectl --kubeconfig "$KUBECONFIG" apply -f infrastructure/namespaces/localcontentshare.yaml
kubectl --kubeconfig "$KUBECONFIG" apply -f apps/localcontentshare/storage.yaml
kubectl --kubeconfig "$KUBECONFIG" apply -f apps/localcontentshare/restore-pod.yaml
kubectl --kubeconfig "$KUBECONFIG" -n localcontentshare wait pod/restore --for=condition=Ready --timeout=300s
ssh root@192.168.1.109 'docker stop local-content-share' > /dev/null
ssh root@192.168.1.109 'tar -C /home/docker/app/localcontentshare/data -cpf - .' | kubectl --kubeconfig "$KUBECONFIG" -n localcontentshare exec -i restore -- tar -xpf - -C /restore/localcontentshare-data
kubectl --kubeconfig "$KUBECONFIG" -n localcontentshare delete pod restore --wait=true
```

```bash
set -euo pipefail
: "${KUBECONFIG:?}"
git switch master
git pull --ff-only
git switch -c codex/activate-localcontentshare
python3 scripts/finish_migration.py localcontentshare
git add apps/localcontentshare clusters/home/localcontentshare.yaml tests/migrations/localcontentshare.yaml
git commit -m "Activate localcontentshare and remove restore helpers"
git push -u origin HEAD
gh pr create --base master --title "Activate localcontentshare" --body "Enable localcontentshare after the data restore. Remove temporary restore files."
gh pr checks --watch
gh pr merge --squash
flux --kubeconfig "$KUBECONFIG" reconcile kustomization flux-system --with-source -n flux-system --timeout=20m
flux --kubeconfig "$KUBECONFIG" reconcile kustomization localcontentshare --with-source -n flux-system --timeout=20m
```
