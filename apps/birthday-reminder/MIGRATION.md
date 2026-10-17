```bash
set -euo pipefail
: "${KUBECONFIG:?}"
kubectl --kubeconfig "$KUBECONFIG" apply -f infrastructure/namespaces/birthday-reminder.yaml
kubectl --kubeconfig "$KUBECONFIG" apply -f apps/birthday-reminder/storage.yaml
kubectl --kubeconfig "$KUBECONFIG" apply -f apps/birthday-reminder/restore-pod.yaml
kubectl --kubeconfig "$KUBECONFIG" -n birthday-reminder wait pod/restore --for=condition=Ready --timeout=300s
ssh root@192.168.1.109 'docker stop personal_birthday_reminder-bh_reminder_bot-1 personal_birthday_reminder-bh_reminder_cron-1' > /dev/null
ssh root@192.168.1.109 'docker cp personal_birthday_reminder-bh_reminder_bot-1:/app/birthday.csv -' | kubectl --kubeconfig "$KUBECONFIG" -n birthday-reminder exec -i restore -- tar -xpf - -C /restore/birthday-reminder-data
kubectl --kubeconfig "$KUBECONFIG" -n birthday-reminder delete pod restore --wait=true
```

```bash
set -euo pipefail
: "${KUBECONFIG:?}"
git switch master
git pull --ff-only
git switch -c codex/activate-birthday-reminder
python3 scripts/finish_migration.py birthday-reminder
git add apps/birthday-reminder clusters/home/birthday-reminder.yaml tests/migrations/birthday-reminder.yaml
git commit -m "Activate birthday-reminder and remove restore helpers"
git push -u origin HEAD
gh pr create --base master --title "Activate birthday-reminder" --body "Enable birthday-reminder after the data restore. Remove temporary restore files."
gh pr checks --watch
gh pr merge --squash
flux --kubeconfig "$KUBECONFIG" reconcile kustomization flux-system --with-source -n flux-system --timeout=20m
flux --kubeconfig "$KUBECONFIG" reconcile kustomization birthday-reminder --with-source -n flux-system --timeout=20m
```
