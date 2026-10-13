```bash
set -euo pipefail
: "${KUBECONFIG:?}"
kubectl --kubeconfig "$KUBECONFIG" apply -f infrastructure/namespaces/birthday-reminder.yaml
kubectl --kubeconfig "$KUBECONFIG" apply -f apps/birthday-reminder/storage.yaml
kubectl --kubeconfig "$KUBECONFIG" apply -f apps/birthday-reminder/restore-pod.yaml
kubectl --kubeconfig "$KUBECONFIG" -n birthday-reminder wait pod/restore --for=condition=Ready --timeout=300s
ssh lionzxy@home.kulikov.uk 'sudo -n docker stop personal_birthday_reminder-bh_reminder_bot-1 personal_birthday_reminder-bh_reminder_cron-1' > /dev/null
ssh lionzxy@home.kulikov.uk 'sudo -n docker cp personal_birthday_reminder-bh_reminder_bot-1:/app/birthday.csv -' | kubectl --kubeconfig "$KUBECONFIG" -n birthday-reminder exec -i restore -- tar -xpf - -C /restore/birthday-reminder-data
kubectl --kubeconfig "$KUBECONFIG" -n birthday-reminder delete pod restore --wait=true
```
