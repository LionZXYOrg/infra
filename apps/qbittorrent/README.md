# qbittorrent migration

Move qBittorrent and its download files while Samba stays excluded.

This is preparation from [the old snapshot](https://github.com/LionZXY/rpi-infra/tree/ccfc5ecd5433fa0d7fe3844f167612b8ed6e2297/app/qbittorrent), not a live inventory. Both the Flux Kustomization and every HelmRelease are suspended. Do not activate this proposal before the final inventory and isolated restore rehearsal.

Pause torrents and stop qBittorrent before copying config and all ../samba/data/Downloads files. Preserve .torrent files, fastresume state and exact /downloads paths. The downloads PVC is independent of Samba; do not stop or migrate Samba. If Samba still serves this directory, agree on ownership and final shared storage: copying creates two divergent directories and is not ongoing synchronization. WebUI remains ClusterIP behind staging HTTPS. A separate LoadBalancer exposes only TCP/UDP 6881; reserve the final LAN address and router forwarding. K3s ServiceLB also occupies host ports; check conflicts with the old host and other services. PUID/PGID stay 1000, and the timezone spelling is corrected to Europe/London. The LinuxServer image needs its root init process and is not forced to run as an unprivileged UID.

Old host: `torrent.kulikov.uk`. Rehearsal host: `qbittorrent.test.kulikov.uk`. The cluster wildcard certificate covers only the rehearsal host. Provision a matching certificate and review the hostname/domain before moving the old DNS record.

## Secrets and merge order

No separate application Secret is present in the snapshot. Any passwords, notification URLs or credentials inside application data must remain in encrypted backups; add a private Secret if the final inventory requires new environment configuration.

## Rehearse and restore

Use an isolated cluster and a copy of the data. Run backup and restore pipelines in Bash with `set -euo pipefail`; a failed producer must fail the whole operation, and every restore must pass integrity checks before the old copy can be retired. Set `KUBECONFIG` to the explicit disposable cluster path. Use test integration credentials. Confirm storage capacity and class before provisioning; `local-path` is provisional node-local storage, not HA or a backup. Apply the namespace and preparation with suspension preserved:

```bash
kubectl --kubeconfig "$KUBECONFIG" apply -f infrastructure/namespaces/qbittorrent.yaml
kubectl --kubeconfig "$KUBECONFIG" apply -k apps/qbittorrent
```

The `storage.yaml` PVCs are protected from Flux pruning. Set their PV reclaim policy to Retain after binding and keep separate encrypted backups. Stop every writer on the source before the final backup. Record the running image digest, sizes and ownership without exporting credentials.

For each directory below, create an archive of its **contents** on the old host and stream it through age encryption. Use a backup recipient whose recovery identity is already backed up; it need not be the cluster SOPS identity. Set `SOURCE_DATA`, `BACKUP_RECIPIENT`, `BACKUP_DIR`, and `CLAIM` for one row. Never store an unencrypted archive in Git.

| Old path under app/qbittorrent | PVC | Application mount |
| --- | --- | --- |
| `config` | `qbittorrent-config` | `/config` |
| `../samba/data/Downloads` | `qbittorrent-downloads` | `/downloads` |

```bash
# Old host, with the service stopped:
mkdir -p "$BACKUP_DIR"
tar -C "$SOURCE_DATA" -cpf - . | age -r "$BACKUP_RECIPIENT" -o "$BACKUP_DIR/$CLAIM.tar.age"
sha256sum "$BACKUP_DIR/$CLAIM.tar.age"
```

Transfer the encrypted archives, verify encrypted checksums, then restore in the rehearsal cluster:

```bash
kubectl --kubeconfig "$KUBECONFIG" apply -f apps/qbittorrent/restore-pod.yaml
kubectl --kubeconfig "$KUBECONFIG" -n qbittorrent wait pod/restore --for=condition=Ready --timeout=180s
age --decrypt -i "$BACKUP_IDENTITY" "$BACKUP_DIR/$CLAIM.tar.age" | kubectl --kubeconfig "$KUBECONFIG" -n qbittorrent exec -i restore -- tar -xpf - -C "/restore/$CLAIM"
kubectl --kubeconfig "$KUBECONFIG" -n qbittorrent delete pod restore
```

Check expected file counts, ownership, storage size and application integrity before activation. The helper runs as root to preserve numeric ownership; it has no service account token. Delete it before the application starts so the RWO volume has only one user.

## Health and acceptance

TCP 8080 probes cover the listener, not authenticated API behavior. Test WebUI login, API version and torrent listing with credentials supplied privately. Confirm all save paths are /downloads, recheck a known torrent without starting an external download, compare fastresume state, and verify both 6881 protocols from the chosen LAN route. Verify access from the remaining Samba service if storage will stay shared.

Run the local unit suite, `bash scripts/check.sh`, and `python3 scripts/migration_check.py --render`. These render the pinned chart and verify wiring, suspension, secrets, mounts, single-writer strategy and probes. The required PR Deployment check validates the shared infrastructure and that this service remains inactive; it is not an application data-restore test. Complete the functional rehearsal above with restored data and test accounts before cutover. Never save raw application logs, Secret YAML or decrypted data in CI reports.

## Cutover and rollback

1. Reconcile this proposal with the final service inventory, version/digest, storage size, DNS, architecture and credentials. Require both PR checks on the latest revision.
2. Finish the isolated restore and acceptance checks. Stop the old writer, take a final encrypted backup and repeat the restore into production storage. Keep the old container stopped and its data intact.
3. Review a separate activation commit clearing `spec.suspend` on the service HelmRelease(s) and `clusters/home/qbittorrent.yaml`. Reconcile Flux, require all release/workload health checks, then switch the reviewed DNS/client/LAN route.
4. Monitor functional behavior and data counts through the agreed rollback window.
5. To roll back, suspend the Flux Kustomization and HelmRelease(s), stop/scale the new workloads to zero and confirm there are no writers. Restore the old DNS/client route and restart the old containers from the retained matching backup/image. If new writes occurred, reconcile them or restore a compatible backup before restarting the old writer. Do not copy migrated database files backward across schema versions. Preserve new PVCs and encrypted backups for diagnosis.
