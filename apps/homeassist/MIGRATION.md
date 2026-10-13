```bash
set -euo pipefail
: "${KUBECONFIG:?}"
kubectl --kubeconfig "$KUBECONFIG" apply -f infrastructure/namespaces/homeassist.yaml
kubectl --kubeconfig "$KUBECONFIG" apply -f apps/homeassist/storage.yaml
kubectl --kubeconfig "$KUBECONFIG" apply -f apps/homeassist/restore-pod.yaml
kubectl --kubeconfig "$KUBECONFIG" -n homeassist wait pod/restore --for=condition=Ready --timeout=300s
ssh lionzxy@home.kulikov.uk 'sudo -n docker stop homeassistant matter-server homeassist-matter-hub-1' > /dev/null
ssh lionzxy@home.kulikov.uk 'sudo -n tar -C /home/docker/app/homeassist/ha_config -cpf - .' | kubectl --kubeconfig "$KUBECONFIG" -n homeassist exec -i restore -- tar -xpf - -C /restore/homeassist-config
ssh lionzxy@home.kulikov.uk 'sudo -n tar -C /home/docker/app/homeassist/matter_data -cpf - .' | kubectl --kubeconfig "$KUBECONFIG" -n homeassist exec -i restore -- tar -xpf - -C /restore/homeassist-matter
ssh lionzxy@home.kulikov.uk 'sudo -n tar -C /home/docker/app/homeassist/home-assistant-matter-hub -cpf - .' | kubectl --kubeconfig "$KUBECONFIG" -n homeassist exec -i restore -- tar -xpf - -C /restore/homeassist-hub
kubectl --kubeconfig "$KUBECONFIG" -n homeassist exec restore -- sed -i 's#ws://host.docker.internal:5580/ws#ws://homeassist-matter-server.homeassist.svc.cluster.local:5580/ws#g' /restore/homeassist-config/.storage/core.config_entries
kubectl --kubeconfig "$KUBECONFIG" -n homeassist exec restore -- chown -R 1000:1000 /restore/homeassist-matter
kubectl --kubeconfig "$KUBECONFIG" -n homeassist delete pod restore --wait=true
```
