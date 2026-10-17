```bash
set -euo pipefail
: "${KUBECONFIG:?}"
kubectl --kubeconfig "$KUBECONFIG" apply -f infrastructure/namespaces/copyparty.yaml
kubectl --kubeconfig "$KUBECONFIG" apply -f apps/qbittorrent/storage.yaml
kubectl --kubeconfig "$KUBECONFIG" apply -f apps/qbittorrent/restore-pod.yaml
kubectl --kubeconfig "$KUBECONFIG" -n copyparty wait pod/restore --for=condition=Ready --timeout=300s
ssh root@192.168.1.109 'docker stop qbittorrent' > /dev/null
ssh root@192.168.1.109 'tar -C /home/docker/app/qbittorrent/config -cpf - .' | kubectl --kubeconfig "$KUBECONFIG" -n copyparty exec -i restore -- tar -xpf - -C /restore/qbittorrent-config
kubectl --kubeconfig "$KUBECONFIG" -n copyparty delete pod restore --wait=true
```

```bash
set -euo pipefail
: "${KUBECONFIG:?}"
git switch master
git pull --ff-only
git switch -c codex/activate-qbittorrent
python3 scripts/finish_migration.py qbittorrent
git add apps/qbittorrent clusters/home/qbittorrent.yaml tests/migrations/qbittorrent.yaml
git commit -m "Activate qbittorrent and remove restore helpers"
git push -u origin HEAD
gh pr create --base master --title "Activate qbittorrent" --body "Enable qbittorrent after the data restore. Remove temporary restore files."
gh pr checks --watch
gh pr merge --squash
flux --kubeconfig "$KUBECONFIG" reconcile kustomization flux-system --with-source -n flux-system --timeout=20m
flux --kubeconfig "$KUBECONFIG" reconcile kustomization qbittorrent --with-source -n flux-system --timeout=20m
```
