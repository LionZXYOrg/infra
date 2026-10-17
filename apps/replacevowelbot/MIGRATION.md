```bash
:
```

```bash
set -euo pipefail
: "${KUBECONFIG:?}"
git switch master
git pull --ff-only
git switch -c codex/activate-replacevowelbot
python3 scripts/finish_migration.py replacevowelbot
git add apps/replacevowelbot clusters/home/replacevowelbot.yaml tests/migrations/replacevowelbot.yaml
git commit -m "Activate replacevowelbot and remove restore helpers"
git push -u origin HEAD
gh pr create --base master --title "Activate replacevowelbot" --body "Enable replacevowelbot after the data restore. Remove temporary restore files."
gh pr checks --watch
ssh root@192.168.1.109 'if docker container inspect replace_vowel_bot >/dev/null 2>&1; then docker stop replace_vowel_bot >/dev/null; fi'
gh pr merge --squash
flux --kubeconfig "$KUBECONFIG" reconcile kustomization flux-system --with-source -n flux-system --timeout=20m
flux --kubeconfig "$KUBECONFIG" reconcile kustomization replacevowelbot --with-source -n flux-system --timeout=20m
```
