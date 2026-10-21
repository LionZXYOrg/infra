```bash
bash <<'LOCALCONTENTSHARE_MIGRATION'
set -euo pipefail
: "${KUBECONFIG:?}"
umask 077
WORK_DIR="$(mktemp -d)"
ARCHIVE="$WORK_DIR/localcontentshare-data.tar"
KNOWN_HOSTS="$WORK_DIR/known_hosts"
trap 'rm -f -- "$ARCHIVE" "$KNOWN_HOSTS"; rmdir -- "$WORK_DIR"' EXIT
printf '%s\n' '192.168.1.109 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAINVS7UdSqrX5YtD1oy14VtvHr5YrQMbr46YVW8tfwOVs' > "$KNOWN_HOSTS"
SSH=(ssh -n -T -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$KNOWN_HOSTS" -o HostKeyAlgorithms=ssh-ed25519 -o ConnectTimeout=10 root@192.168.1.109)
"${SSH[@]}" 'test -d /home/docker/app/localcontentshare/data'
"${SSH[@]}" 'test -z "$(docker ps --filter name=local-content-share --format "{{.ID}}")"'
"${SSH[@]}" 'tar -C /home/docker/app/localcontentshare/data -cpf - .' > "$ARCHIVE"
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
LOCALCONTENTSHARE_MIGRATION
```
