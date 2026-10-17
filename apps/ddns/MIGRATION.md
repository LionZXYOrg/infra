```bash
:
```

```bash
set -euo pipefail
: "${KUBECONFIG:?}"
git switch master
git pull --ff-only
git switch -c codex/activate-ddns
python3 scripts/finish_migration.py ddns
git add apps/ddns clusters/home/ddns.yaml tests/migrations/ddns.yaml
git commit -m "Activate ddns and remove restore helpers"
git push -u origin HEAD
gh pr create --base master --title "Activate ddns" --body "Enable ddns after the data restore. Remove temporary restore files."
gh pr checks --watch
ssh root@192.168.1.109 'if docker container inspect cloudflare-ddns >/dev/null 2>&1; then docker stop cloudflare-ddns >/dev/null; fi'
gh pr merge --squash
flux --kubeconfig "$KUBECONFIG" reconcile kustomization flux-system --with-source -n flux-system --timeout=20m
flux --kubeconfig "$KUBECONFIG" reconcile kustomization ddns --with-source -n flux-system --timeout=20m
```
