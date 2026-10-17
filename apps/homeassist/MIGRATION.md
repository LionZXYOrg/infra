```bash
set -euo pipefail
: "${KUBECONFIG:?}"
kubectl --kubeconfig "$KUBECONFIG" apply -f infrastructure/namespaces/homeassist.yaml
kubectl --kubeconfig "$KUBECONFIG" apply -f apps/homeassist/storage.yaml
kubectl --kubeconfig "$KUBECONFIG" apply -f apps/homeassist/restore-pod.yaml
kubectl --kubeconfig "$KUBECONFIG" -n homeassist wait pod/restore --for=condition=Ready --timeout=300s
ssh root@192.168.1.109 'docker stop homeassistant matter-server homeassist-matter-hub-1' > /dev/null
ssh root@192.168.1.109 'tar -C /home/docker/app/homeassist/ha_config -cpf - .' | kubectl --kubeconfig "$KUBECONFIG" -n homeassist exec -i restore -- tar -xpf - -C /restore/homeassist-config
ssh root@192.168.1.109 'tar -C /home/docker/app/homeassist/matter_data -cpf - .' | kubectl --kubeconfig "$KUBECONFIG" -n homeassist exec -i restore -- tar -xpf - -C /restore/homeassist-matter
ssh root@192.168.1.109 'tar -C /home/docker/app/homeassist/home-assistant-matter-hub -cpf - .' | kubectl --kubeconfig "$KUBECONFIG" -n homeassist exec -i restore -- tar -xpf - -C /restore/homeassist-hub
kubectl --kubeconfig "$KUBECONFIG" -n homeassist exec restore -- sed -i 's#ws://host.docker.internal:5580/ws#ws://homeassist-matter-server.homeassist.svc.cluster.local:5580/ws#g' /restore/homeassist-config/.storage/core.config_entries
kubectl --kubeconfig "$KUBECONFIG" -n homeassist exec restore -- chown -R 1000:1000 /restore/homeassist-matter
kubectl --kubeconfig "$KUBECONFIG" -n homeassist delete pod restore --wait=true
```

```bash
set -euo pipefail
: "${KUBECONFIG:?}"
git switch master
git pull --ff-only
git switch -c codex/activate-homeassist
python3 scripts/finish_migration.py homeassist
git add apps/homeassist clusters/home/homeassist.yaml tests/migrations/homeassist.yaml
git commit -m "Activate homeassist and remove restore helpers"
git push -u origin HEAD
gh pr create --base master --title "Activate homeassist" --body "Enable homeassist after the data restore. Remove temporary restore files."
gh pr checks --watch
gh pr merge --squash
flux --kubeconfig "$KUBECONFIG" reconcile kustomization flux-system --with-source -n flux-system --timeout=20m
flux --kubeconfig "$KUBECONFIG" reconcile kustomization homeassist --with-source -n flux-system --timeout=20m
```
