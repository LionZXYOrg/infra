```bash
set -euo pipefail
: "${KUBECONFIG:?}"
kubectl --kubeconfig "$KUBECONFIG" apply -f infrastructure/namespaces/binbot.yaml
kubectl --kubeconfig "$KUBECONFIG" apply -f apps/binbot/storage.yaml
kubectl --kubeconfig "$KUBECONFIG" apply -f apps/binbot/restore-pod.yaml
kubectl --kubeconfig "$KUBECONFIG" -n binbot wait pod/restore --for=condition=Ready --timeout=300s
ssh root@192.168.1.109 'docker stop greenwich-bin-bot' > /dev/null
ssh root@192.168.1.109 'tar -C /home/docker/app/binbot/data -cpf - .' | kubectl --kubeconfig "$KUBECONFIG" -n binbot exec -i restore -- tar -xpf - -C /restore/binbot-data
kubectl --kubeconfig "$KUBECONFIG" -n binbot delete pod restore --wait=true
```

```bash
set -euo pipefail
: "${KUBECONFIG:?}"
git switch master
git pull --ff-only
git switch -c codex/activate-binbot
python3 scripts/finish_migration.py binbot
git add apps/binbot clusters/home/binbot.yaml tests/migrations/binbot.yaml
git commit -m "Activate binbot and remove restore helpers"
git push -u origin HEAD
gh pr create --base master --title "Activate binbot" --body "Enable binbot after the data restore. Remove temporary restore files."
gh pr checks --watch
gh pr merge --squash
flux --kubeconfig "$KUBECONFIG" reconcile kustomization flux-system --with-source -n flux-system --timeout=20m
flux --kubeconfig "$KUBECONFIG" reconcile kustomization binbot --with-source -n flux-system --timeout=20m
```
