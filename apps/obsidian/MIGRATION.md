```bash
set -euo pipefail
: "${KUBECONFIG:?}"
kubectl --kubeconfig "$KUBECONFIG" apply -f infrastructure/namespaces/obsidian.yaml
kubectl --kubeconfig "$KUBECONFIG" apply -f apps/obsidian/storage.yaml
kubectl --kubeconfig "$KUBECONFIG" apply -f apps/obsidian/restore-pod.yaml
kubectl --kubeconfig "$KUBECONFIG" -n obsidian wait pod/restore --for=condition=Ready --timeout=300s
ssh root@192.168.1.109 'docker stop obsidian-livesync' > /dev/null
ssh root@192.168.1.109 'tar -C /home/docker/app/obsidian/couchdb-data -cpf - .' | kubectl --kubeconfig "$KUBECONFIG" -n obsidian exec -i restore -- tar -xpf - -C /restore/obsidian-data
kubectl --kubeconfig "$KUBECONFIG" -n obsidian exec restore -- chown -R 5984:5984 /restore/obsidian-data
kubectl --kubeconfig "$KUBECONFIG" -n obsidian delete pod restore --wait=true
```

```bash
set -euo pipefail
: "${KUBECONFIG:?}"
git switch master
git pull --ff-only
git switch -c codex/activate-obsidian
python3 scripts/finish_migration.py obsidian
git add apps/obsidian clusters/home/obsidian.yaml tests/migrations/obsidian.yaml
git commit -m "Activate obsidian and remove restore helpers"
git push -u origin HEAD
gh pr create --base master --title "Activate obsidian" --body "Enable obsidian after the data restore. Remove temporary restore files."
gh pr checks --watch
gh pr merge --squash
flux --kubeconfig "$KUBECONFIG" reconcile kustomization flux-system --with-source -n flux-system --timeout=20m
flux --kubeconfig "$KUBECONFIG" reconcile kustomization obsidian --with-source -n flux-system --timeout=20m
```
