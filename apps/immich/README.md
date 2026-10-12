# immich migration

Move the library and PostgreSQL 14 data without silently upgrading the database extensions.

This is preparation from [the old snapshot](https://github.com/LionZXY/rpi-infra/tree/ccfc5ecd5433fa0d7fe3844f167612b8ed6e2297/app/immich), not a live inventory. Both the Flux Kustomization and every HelmRelease are suspended. Do not activate this proposal before the final inventory and isolated restore rehearsal.

The snapshot has a floating release tag; v1.131.3 is a compatible provisional baseline, not evidence of the running version. Both server and ML are pinned to this version. Confirm the actual versions before restore. The Immich 0.8.5 chart is used with the old /usr/src/app/upload mount preserved and tested. Redis 6.2 and PostgreSQL 14 + pgvecto-rs 0.2 retain the exact snapshot digests. Do not replace this database with plain PostgreSQL or VectorChord as part of migration. The model-cache volume is disposable and will be rebuilt; check outbound model downloads and CPU architecture. Back up the complete library tree, including upload, library, thumbs, encoded-video and profile; preserve relative paths.

Old host: `immich.kulikov.uk`. Rehearsal host: `immich.test.kulikov.uk`. The cluster wildcard certificate covers only the rehearsal host. Provision a matching certificate and review the hostname/domain before moving the old DNS record.

## Secrets and merge order

Private Secret names: `immich-config` in namespace `immich`. The paired private PR uses `scripts/secretctl.py`; only SOPS encrypted manifests belong in `infra-secrets`. Verify or rotate snapshot credentials before cutover. Do not print values or put them in Helm values, shell arguments or public files.

Merge the public preparation first so its namespace exists while the service stays suspended; then merge the paired private PR into private `main`. The public Gitlink references a published private commit; Flux follows `main` independently. Resolve the namespace/secret indexes by keeping every entry from merged service PRs. After merging the private service PRs, update the public Gitlink to the published private `main` commit containing all merged Secrets; do not leave it pointing at an individual draft branch for bootstrap. Wait for `cluster-secrets` Ready before activation.

## Rehearse and restore

Use an isolated cluster and a copy of the data. Run backup and restore pipelines in Bash with `set -euo pipefail`; a failed producer must fail the whole operation, and every restore must pass integrity checks before the old copy can be retired. Set `KUBECONFIG` to the explicit disposable cluster path. Use test integration credentials. Confirm storage capacity and class before provisioning; `local-path` is provisional node-local storage, not HA or a backup. Apply the namespace and preparation with suspension preserved:

```bash
kubectl --kubeconfig "$KUBECONFIG" apply -f infrastructure/namespaces/immich.yaml
kubectl --kubeconfig "$KUBECONFIG" apply -k apps/immich
```

The `storage.yaml` PVCs are protected from Flux pruning. Set their PV reclaim policy to Retain after binding and keep separate encrypted backups. Stop every writer on the source before the final backup. Record the running image digest, sizes and ownership without exporting credentials.

Prefer a logical PostgreSQL dump for cross-node/architecture restores. Stop immich-server and immich-machine-learning while leaving the old database running for the dump. Run under the old `app/immich` directory. Use the final source database user/name after verifying them:

```bash
docker compose exec -T database pg_dump -U postgres -d immich --format=custom | age -r "$BACKUP_RECIPIENT" -o "$BACKUP_DIR/immich.dump.age"
docker compose exec -T database pg_dumpall -U postgres --globals-only | age -r "$BACKUP_RECIPIENT" -o "$BACKUP_DIR/immich-globals.sql.age"
```

Restore the full `app/immich/library` tree **before starting the database backend**, so the restore helper is the only Pod mounting either data PVC. On the old host, stop the server/ML writers and encrypt an archive of the library contents:

```bash
tar -C "$OLD_ROOT/app/immich/library" -cpf - . | age -r "$BACKUP_RECIPIENT" -o "$BACKUP_DIR/immich-library.tar.age"
sha256sum "$BACKUP_DIR/immich-library.tar.age"
```

Transfer the encrypted archive and verify its checksum. In the isolated cluster with all releases still suspended:

```bash
kubectl --kubeconfig "$KUBECONFIG" apply -f apps/immich/restore-pod.yaml
kubectl --kubeconfig "$KUBECONFIG" -n immich wait pod/restore --for=condition=Ready --timeout=180s
age --decrypt -i "$BACKUP_IDENTITY" "$BACKUP_DIR/immich-library.tar.age" | kubectl --kubeconfig "$KUBECONFIG" -n immich exec -i restore -- tar -xpf - -C /restore/immich-library
kubectl --kubeconfig "$KUBECONFIG" -n immich delete pod restore
```

Model cache may be discarded. Redis has no durable source volume; drain outstanding source jobs before the final backup and requeue missing jobs after restore.

Keep the application release `immich` suspended. In the isolated copy, resume only `immich-backend` to create an empty database (the backend also starts Redis for Immich). Set the database role password via the test Secret, then privately review and restore required grants/roles from the encrypted globals backup. Do not replay CREATE ROLE for the already initialized postgres role blindly. PGDATA is `/var/lib/postgresql/data/pgdata` inside the PVC; the fresh database and a raw cold copy must both use that subdirectory. Restore with:

```bash
kubectl --kubeconfig "$KUBECONFIG" -n immich patch helmrelease immich-backend --type=merge -p '{"spec":{"suspend":false}}'
kubectl --kubeconfig "$KUBECONFIG" -n immich rollout status deployment/immich-postgres --timeout=300s
age --decrypt -i "$BACKUP_IDENTITY" "$BACKUP_DIR/immich.dump.age" | kubectl --kubeconfig "$KUBECONFIG" -n immich exec -i deployment/immich-postgres -- pg_restore -U postgres -d immich --clean --if-exists --exit-on-error
```

Preserve the database extension schema and functions in the dump; verify vectors 0.2.0 and checksums before allowing the server to run migrations. Do not use a raw PGDATA copy across major versions, incompatible architectures, or changed extensions. If a cold copy is chosen instead, stop PostgreSQL cleanly and restore the entire original `postgres` tree into `/restore/immich-postgres/pgdata` with identical image/architecture; never copy a live database.



## Health and acceptance

Require /api/server/ping and ML /ping, PostgreSQL pg_isready, Redis PONG, and zero pg_stat_database.checksum_failures. Compare user/asset/album counts, restore login, download an original and view a thumbnail/video; upload one test asset, run a search/ML task and verify queued jobs complete. Reboot the rehearsal node and repeat the checks. An image/database version mismatch is a cutover blocker.

Run the local unit suite, `bash scripts/check.sh`, and `python3 scripts/migration_check.py --render`. These render the pinned chart and verify wiring, suspension, secrets, mounts, single-writer strategy and probes. The required PR Deployment check validates the shared infrastructure and that this service remains inactive; it is not an application data-restore test. Complete the functional rehearsal above with restored data and test accounts before cutover. Never save raw application logs, Secret YAML or decrypted data in CI reports.

## Cutover and rollback

1. Reconcile this proposal with the final service inventory, version/digest, storage size, DNS, architecture and credentials. Require both PR checks on the latest revision.
2. Finish the isolated restore and acceptance checks. Stop the old writer, take a final encrypted backup and repeat the restore into production storage. Keep the old container stopped and its data intact.
3. Review a separate activation commit clearing `spec.suspend` on the service HelmRelease(s) and `clusters/home/immich.yaml`. Reconcile Flux, require all release/workload health checks, then switch the reviewed DNS/client/LAN route.
4. Monitor functional behavior and data counts through the agreed rollback window.
5. To roll back, suspend the Flux Kustomization and HelmRelease(s), stop/scale the new workloads to zero and confirm there are no writers. Restore the old DNS/client route and restart the old containers from the retained matching backup/image. If new writes occurred, reconcile them or restore a compatible backup before restarting the old writer. Do not copy migrated database files backward across schema versions. Preserve new PVCs and encrypted backups for diagnosis.

Paired private preparation: [infra-secrets PR #2](https://github.com/LionZXYOrg/infra-secrets/pull/2).
