```bash
:
```

```bash
set -euo pipefail
: "${KUBECONFIG:?}"
git switch master
git pull --ff-only
git switch -c codex/activate-speedtest
python3 scripts/finish_migration.py speedtest
git add apps/speedtest clusters/home/speedtest.yaml tests/migrations/speedtest.yaml
git commit -m "Activate speedtest and remove restore helpers"
git push -u origin HEAD
gh pr create --base master --title "Activate speedtest" --body "Enable speedtest after the data restore. Remove temporary restore files."
gh pr checks --watch
ssh root@192.168.1.109 'if docker container inspect speedtest >/dev/null 2>&1; then docker stop speedtest >/dev/null; fi'
gh pr merge --squash
flux --kubeconfig "$KUBECONFIG" reconcile kustomization flux-system --with-source -n flux-system --timeout=20m
flux --kubeconfig "$KUBECONFIG" reconcile kustomization speedtest --with-source -n flux-system --timeout=20m
```
