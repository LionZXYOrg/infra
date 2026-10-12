# warden migration

Move the complete Vaultwarden SQLite vault and attachments.

This is preparation from [the old snapshot](https://github.com/LionZXY/rpi-infra/tree/ccfc5ecd5433fa0d7fe3844f167612b8ed6e2297/app/warden), not a live inventory. Both the Flux Kustomization and every HelmRelease are suspended. Do not activate this proposal before the final inventory and isolated restore rehearsal.

Keep vaultwarden/server 1.35.2-alpine from the snapshot, not the newer chart image default. Copy all data, including db.sqlite3, WAL/SHM files, attachments, sends, RSA keys and config.json, after stopping the old server. Validate SQLite integrity on a restored copy before starting it. Treat the whole directory as a sensitive encrypted backup. config.json can override environment/chart values, including the old domain and admin token; reconcile it privately. ADMIN_TOKEN is imported into warden-config. Signups remain disabled. The staging DOMAIN is https://warden.test.kulikov.uk. The final warden.lionzxy.ru hostname needs an explicit certificate and reviewed domain change. Version 1.35 already serves websocket notifications on the same HTTP port; no legacy 3012 service is created.

Old host: `warden.lionzxy.ru`. Rehearsal host: `warden.test.kulikov.uk`. The cluster wildcard certificate covers only the rehearsal host. Provision a matching certificate and review the hostname/domain before moving the old DNS record.

## Secrets and merge order

Private Secret names: `warden-config` in namespace `warden`. The paired private PR uses `scripts/secretctl.py`; only SOPS encrypted manifests belong in `infra-secrets`. Verify or rotate snapshot credentials before cutover. Do not print values or put them in Helm values, shell arguments or public files.

Merge the public preparation first so its namespace exists while the service stays suspended; then merge the paired private PR into private `main`. The public Gitlink references a published private commit; Flux follows `main` independently. Resolve the namespace/secret indexes by keeping every entry from merged service PRs. Wait for `cluster-secrets` Ready before activation.

## Rehearse and restore

Use an isolated cluster and a copy of the data. Set `KUBECONFIG` to the explicit disposable cluster path. Use test integration credentials. Confirm storage capacity and class before provisioning; `local-path` is provisional node-local storage, not HA or a backup. Apply the namespace and preparation with suspension preserved:

```bash
kubectl --kubeconfig "$KUBECONFIG" apply -f infrastructure/namespaces/warden.yaml
kubectl --kubeconfig "$KUBECONFIG" apply -k apps/warden
```

The `storage.yaml` PVCs are protected from Flux pruning. Set their PV reclaim policy to Retain after binding and keep separate encrypted backups. Stop every writer on the source before the final backup. Record the running image digest, sizes and ownership without exporting credentials.

For each directory below, create an archive of its **contents** on the old host and stream it through age encryption. Use a backup recipient whose recovery identity is already backed up; it need not be the cluster SOPS identity. Set `SOURCE_DATA`, `BACKUP_RECIPIENT`, `BACKUP_DIR`, and `CLAIM` for one row. Never store an unencrypted archive in Git.

| Old path under app/warden | PVC | Application mount |
| --- | --- | --- |
| `data` | `warden-data` | `/data` |

```bash
# Old host, with the service stopped:
mkdir -p "$BACKUP_DIR"
tar -C "$SOURCE_DATA" -cpf - . | age -r "$BACKUP_RECIPIENT" -o "$BACKUP_DIR/$CLAIM.tar.age"
sha256sum "$BACKUP_DIR/$CLAIM.tar.age"
```

Transfer the encrypted archives, verify encrypted checksums, then restore in the rehearsal cluster:

```bash
kubectl --kubeconfig "$KUBECONFIG" apply -f apps/warden/restore-pod.yaml
kubectl --kubeconfig "$KUBECONFIG" -n warden wait pod/restore --for=condition=Ready --timeout=180s
age --decrypt -i "$BACKUP_IDENTITY" "$BACKUP_DIR/$CLAIM.tar.age" | kubectl --kubeconfig "$KUBECONFIG" -n warden exec -i restore -- tar -xpf - -C "/restore/$CLAIM"
kubectl --kubeconfig "$KUBECONFIG" -n warden delete pod restore
```

Check expected file counts, ownership, storage size and application integrity before activation. The helper runs as root to preserve numeric ownership; it has no service account token. Delete it before the application starts so the RWO volume has only one user.

## Health and acceptance

Require /alive. With a test account on the restored staging copy, verify login, vault sync, organization access, an existing attachment, Send data and websocket notifications. Restart and recheck persistence. Confirm existing production clients keep their server URL at cutover. Keep SQLite and RSA keys together for rollback; do not validate by exporting decrypted vault contents to public artifacts.

Run the local unit suite, `bash scripts/check.sh`, and `python3 scripts/migration_check.py --render`. These render the pinned chart and verify wiring, suspension, secrets, mounts, single-writer strategy and probes. The required PR Deployment check validates the shared infrastructure and that this service remains inactive; it is not an application data-restore test. Complete the functional rehearsal above with restored data and test accounts before cutover. Never save raw application logs, Secret YAML or decrypted data in CI reports.

## Cutover and rollback

1. Reconcile this proposal with the final service inventory, version/digest, storage size, DNS, architecture and credentials. Require both PR checks on the latest revision.
2. Finish the isolated restore and acceptance checks. Stop the old writer, take a final encrypted backup and repeat the restore into production storage. Keep the old container stopped and its data intact.
3. Review a separate activation commit clearing `spec.suspend` on the service HelmRelease(s) and `clusters/home/warden.yaml`. Reconcile Flux, require all release/workload health checks, then switch the reviewed DNS/client/LAN route.
4. Monitor functional behavior and data counts through the agreed rollback window.
5. To roll back, suspend the Flux Kustomization and HelmRelease(s), stop/scale the new workloads to zero and confirm there are no writers. Restore the old DNS/client route and restart the old containers from the retained matching backup/image. If new writes occurred, reconcile them or restore a compatible backup before restarting the old writer. Do not copy migrated database files backward across schema versions. Preserve new PVCs and encrypted backups for diagnosis.

Paired private preparation: [infra-secrets PR #7](https://github.com/LionZXYOrg/infra-secrets/pull/7).
