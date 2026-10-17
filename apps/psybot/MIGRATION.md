```bash
:
```

```bash
set -euo pipefail
: "${KUBECONFIG:?}"
git switch master
git pull --ff-only
git switch -c codex/activate-psybot
python3 scripts/finish_migration.py psybot
git add apps/psybot clusters/home/psybot.yaml tests/migrations/psybot.yaml
git commit -m "Activate psybot and remove restore helpers"
git push -u origin HEAD
gh pr create --base master --title "Activate psybot" --body "Enable psybot after the data restore. Remove temporary restore files."
gh pr checks --watch
ssh root@192.168.1.109 'if docker container inspect psybot >/dev/null 2>&1; then docker stop psybot >/dev/null; fi'
gh pr merge --squash
flux --kubeconfig "$KUBECONFIG" reconcile kustomization flux-system --with-source -n flux-system --timeout=20m
flux --kubeconfig "$KUBECONFIG" reconcile kustomization psybot --with-source -n flux-system --timeout=20m
```
