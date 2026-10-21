```bash
set -euo pipefail
: "${KUBECONFIG:?}"
SSH=(ssh -n -T -o BatchMode=yes -o StrictHostKeyChecking=yes -o HostKeyAlias=home.kulikov.uk -o ConnectTimeout=10 lionzxy@192.168.1.109)
"${SSH[@]}" 'sudo -n test -d /home/docker/app/localcontentshare/data'
"${SSH[@]}" 'test -z "$(sudo -n docker ps --filter name=local-content-share --format "{{.ID}}")"'
umask 077
WORK_DIR="$(mktemp -d)"
ARCHIVE="$WORK_DIR/localcontentshare-data.tar"
trap 'rm -f -- "$ARCHIVE"; rmdir -- "$WORK_DIR"' EXIT
"${SSH[@]}" 'sudo -n tar -C /home/docker/app/localcontentshare/data -cpf - .' > "$ARCHIVE"
test -s "$ARCHIVE"
tar -tf "$ARCHIVE" > /dev/null
ARCHIVE_SHA="$(shasum -a 256 "$ARCHIVE")"
ARCHIVE_SHA="${ARCHIVE_SHA%% *}"
kubectl --kubeconfig "$KUBECONFIG" apply -f infrastructure/namespaces/localcontentshare.yaml
kubectl --kubeconfig "$KUBECONFIG" -n localcontentshare apply -f apps/localcontentshare/storage.yaml
kubectl --kubeconfig "$KUBECONFIG" -n localcontentshare apply -f apps/localcontentshare/restore-pod.yaml
kubectl --kubeconfig "$KUBECONFIG" -n localcontentshare wait pod/restore --for=condition=Ready --timeout=300s
kubectl --kubeconfig "$KUBECONFIG" -n localcontentshare cp "$ARCHIVE" restore:/tmp/localcontentshare-data.tar
POD_SHA="$(kubectl --kubeconfig "$KUBECONFIG" -n localcontentshare exec restore -- sha256sum /tmp/localcontentshare-data.tar)"
test "$ARCHIVE_SHA" = "${POD_SHA%% *}"
kubectl --kubeconfig "$KUBECONFIG" -n localcontentshare exec restore -- tar -xpf /tmp/localcontentshare-data.tar -C /restore/localcontentshare-data
kubectl --kubeconfig "$KUBECONFIG" -n localcontentshare delete pod restore --wait=true --timeout=120s
```
