# lionbot migration

Move the Telegram/VK bots with their PostgreSQL 13 database and one polling consumer.

This is preparation from [the old snapshot](https://github.com/LionZXY/rpi-infra/tree/ccfc5ecd5433fa0d7fe3844f167612b8ed6e2297/app/lionbot), not a live inventory. Both the Flux Kustomization and every HelmRelease are suspended. Do not activate this proposal before the final inventory and isolated restore rehearsal.

Import all prod.env fields into lionbot-config, plus the Compose database password. PostgreSQL uses POSTGRES_PASSWORD from that Secret. The bot overrides SERVER_DATABASE_URL to the in-cluster JDBC URL; verify the database name, user, JDBC options and grants against the final service. Check that SERVER_DATABASE_PASSWORD agrees with the restored role password before starting the bot. The published legacy image is amd64 only, and the candidate Pod explicitly selects amd64 nodes; verify the final architecture and rebuild from original sources if it does not match the final node. No source code was present in this snapshot for rebuilding this image.

## Secrets and merge order

Private Secret names: `lionbot-config` in namespace `lionbot`. The paired private PR uses `scripts/secretctl.py`; only SOPS encrypted manifests belong in `infra-secrets`. Verify or rotate snapshot credentials before cutover. Do not print values or put them in Helm values, shell arguments or public files.

Merge the public preparation first so its namespace exists while the service stays suspended; then merge the paired private PR into private `main`. The public Gitlink references a published private commit; Flux follows `main` independently. Resolve the namespace/secret indexes by keeping every entry from merged service PRs. After merging the private service PRs, update the public Gitlink to the published private `main` commit containing all merged Secrets; do not leave it pointing at an individual draft branch for bootstrap. Wait for `cluster-secrets` Ready before activation.

## Rehearse and restore

Use an isolated cluster and a copy of the data. Run backup and restore pipelines in Bash with `set -euo pipefail`; a failed producer must fail the whole operation, and every restore must pass integrity checks before the old copy can be retired. Set `KUBECONFIG` to the explicit disposable cluster path. Use test integration credentials. Confirm storage capacity and class before provisioning; `local-path` is provisional node-local storage, not HA or a backup. Apply the namespace and preparation with suspension preserved:

```bash
kubectl --kubeconfig "$KUBECONFIG" apply -f infrastructure/namespaces/lionbot.yaml
kubectl --kubeconfig "$KUBECONFIG" apply -k apps/lionbot
```

The `storage.yaml` PVCs are protected from Flux pruning. Set their PV reclaim policy to Retain after binding and keep separate encrypted backups. Stop every writer on the source before the final backup. Record the running image digest, sizes and ownership without exporting credentials.

Prefer a logical PostgreSQL dump for cross-node/architecture restores. Stop bots while leaving the old database running for the dump. Run under the old `app/lionbot` directory. Use the final source database user/name after verifying them:

```bash
docker compose exec -T postgresdb pg_dump -U postgres -d postgres --format=custom | age -r "$BACKUP_RECIPIENT" -o "$BACKUP_DIR/lionbot.dump.age"
docker compose exec -T postgresdb pg_dumpall -U postgres --globals-only | age -r "$BACKUP_RECIPIENT" -o "$BACKUP_DIR/lionbot-globals.sql.age"
```

Keep the application release `lionbot` suspended. In the isolated copy, resume only `lionbot-db` to create an empty database (the backend also starts Redis for Immich). Set the database role password via the test Secret, then privately review and restore required grants/roles from the encrypted globals backup. Do not replay CREATE ROLE for the already initialized postgres role blindly. PGDATA is `/var/lib/postgresql/data/pgdata` inside the PVC; the fresh database and a raw cold copy must both use that subdirectory. Restore with:

```bash
kubectl --kubeconfig "$KUBECONFIG" -n lionbot patch helmrelease lionbot-db --type=merge -p '{"spec":{"suspend":false}}'
kubectl --kubeconfig "$KUBECONFIG" -n lionbot rollout status deployment/lionbot-postgres --timeout=300s
age --decrypt -i "$BACKUP_IDENTITY" "$BACKUP_DIR/lionbot.dump.age" | kubectl --kubeconfig "$KUBECONFIG" -n lionbot exec -i deployment/lionbot-postgres -- pg_restore -U postgres -d postgres --clean --if-exists --exit-on-error
```

Compare schemas, table/row counts, roles, ownership and grants before resuming bots. Verify JDBC credentials match the restored role. If choosing a cold copy instead, stop PostgreSQL cleanly, match major/minor image and architecture, and restore the contents of the old `postgres` tree into `/restore/lionbot-postgres/pgdata` using the helper; never copy a live database.

## Health and acceptance

PostgreSQL has pg_isready probes. The closed-source bot has local process probes only; these do not prove Telegram/VK delivery or worker progress. Rehearse using dedicated test bot/VK credentials and a disposable restored database. Verify database connectivity, read/write persistence and each enabled bot workflow in a test chat. Stop the original bots before any new production polling instance starts. Do not run getUpdates from a smoke script alongside the bot.

Run the local unit suite, `bash scripts/check.sh`, and `python3 scripts/migration_check.py --render`. These render the pinned chart and verify wiring, suspension, secrets, mounts, single-writer strategy and probes. The required PR Deployment check validates the shared infrastructure and that this service remains inactive; it is not an application data-restore test. Complete the functional rehearsal above with restored data and test accounts before cutover. Never save raw application logs, Secret YAML or decrypted data in CI reports.

## Cutover and rollback

1. Reconcile this proposal with the final service inventory, version/digest, storage size, DNS, architecture and credentials. Require both PR checks on the latest revision.
2. Finish the isolated restore and acceptance checks. Stop the old writer, take a final encrypted backup and repeat the restore into production storage. Keep the old container stopped and its data intact.
3. Review a separate activation commit clearing `spec.suspend` on the service HelmRelease(s) and `clusters/home/lionbot.yaml`. Reconcile Flux, require all release/workload health checks, then switch the reviewed DNS/client/LAN route.
4. Monitor functional behavior and data counts through the agreed rollback window.
5. To roll back, suspend the Flux Kustomization and HelmRelease(s), stop/scale the new workloads to zero and confirm there are no writers. Restore the old DNS/client route and restart the old containers from the retained matching backup/image. If new writes occurred, reconcile them or restore a compatible backup before restarting the old writer. Do not copy migrated database files backward across schema versions. Preserve new PVCs and encrypted backups for diagnosis.

Paired private preparation: [infra-secrets PR #3](https://github.com/LionZXYOrg/infra-secrets/pull/3).

The PostgreSQL image retains its volume-initialization capabilities, drops NET_RAW and disallows privilege escalation. CI boots the actual pinned image with an empty tmpfs, generated disposable password and no networking; it requires pg_isready without reading application data or exposing credentials.
