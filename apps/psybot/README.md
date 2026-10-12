# psybot migration

Prepare the anonymous question bot with a reproducible container build.

This is preparation from [the old snapshot](https://github.com/LionZXY/rpi-infra/tree/ccfc5ecd5433fa0d7fe3844f167612b8ed6e2297/app/psybot), not a live inventory. Both the Flux Kustomization and every HelmRelease are suspended. Do not activate this proposal before the final inventory and isolated restore rehearsal.

No local persistent data appears in the source. update_id is held in memory; retain the Telegram server queue. Import TELEGRAM_TOKEN into psybot-config. The snapshot only has a local Dockerfile; no image is published. Build apps/psybot/image with Docker, run the source unit tests and import smoke test, then publish a multi-architecture image to the selected registry and replace the pending tag with its digest before activation. The requirements pin python-telegram-bot 13.15 because this source uses the synchronous v13 API; the old >=13 dependency would select an incompatible API today.

## Secrets and merge order

Private Secret names: `psybot-config` in namespace `psybot`. The paired private PR uses `scripts/secretctl.py`; only SOPS encrypted manifests belong in `infra-secrets`. Verify or rotate snapshot credentials before cutover. Do not print values or put them in Helm values, shell arguments or public files.

Merge the public preparation first so its namespace exists while the service stays suspended; then merge the paired private PR into private `main`. The public Gitlink references a published private commit; Flux follows `main` independently. Resolve the namespace/secret indexes by keeping every entry from merged service PRs. After merging the private service PRs, update the public Gitlink to the published private `main` commit containing all merged Secrets; do not leave it pointing at an individual draft branch for bootstrap. Wait for `cluster-secrets` Ready before activation.

## Rehearse and restore

Use an isolated cluster and a copy of the data. Run backup and restore pipelines in Bash with `set -euo pipefail`; a failed producer must fail the whole operation, and every restore must pass integrity checks before the old copy can be retired. Set `KUBECONFIG` to the explicit disposable cluster path. Use test integration credentials. Confirm storage capacity and class before provisioning; `local-path` is provisional node-local storage, not HA or a backup. Apply the namespace and preparation with suspension preserved:

```bash
kubectl --kubeconfig "$KUBECONFIG" apply -f infrastructure/namespaces/psybot.yaml
kubectl --kubeconfig "$KUBECONFIG" apply -k apps/psybot
```

This snapshot declares no persistent local data. Preserve upstream queues/integration state, private configuration and the old image/source for rollback.

## Health and acceptance

Local process probes do not establish delivery health. Run tests/test_migration_psybot.py with fake Telegram clients; no network requests or real messages are involved. Rehearse with a dedicated test bot and test recipient (replace the hardcoded recipient in the rehearsal source), verify /start and a text question, then ensure the polling worker stays responsive across a transient API outage. Stop the old worker before switching to the production token.

Run the local unit suite, `bash scripts/check.sh`, and `python3 scripts/migration_check.py --render`. These render the pinned chart and verify wiring, suspension, secrets, mounts, single-writer strategy and probes. The required PR Deployment check validates the shared infrastructure and that this service remains inactive; it is not an application data-restore test. Complete the functional rehearsal above with restored data and test accounts before cutover. Never save raw application logs, Secret YAML or decrypted data in CI reports.

## Cutover and rollback

1. Reconcile this proposal with the final service inventory, version/digest, storage size, DNS, architecture and credentials. Require both PR checks on the latest revision.
2. Finish the isolated restore and acceptance checks. Stop the old writer, take a final encrypted backup and repeat the restore into production storage. Keep the old container stopped and its data intact.
3. Review a separate activation commit clearing `spec.suspend` on the service HelmRelease(s) and `clusters/home/psybot.yaml`. Reconcile Flux, require all release/workload health checks, then switch the reviewed DNS/client/LAN route.
4. Monitor functional behavior and data counts through the agreed rollback window.
5. To roll back, suspend the Flux Kustomization and HelmRelease(s), stop/scale the new workloads to zero and confirm there are no writers. Restore the old DNS/client route and restart the old containers from the retained matching backup/image. If new writes occurred, reconcile them or restore a compatible backup before restarting the old writer. Do not copy migrated database files backward across schema versions. Preserve new PVCs and encrypted backups for diagnosis.

Paired private preparation: [infra-secrets PR #5](https://github.com/LionZXYOrg/infra-secrets/pull/5).
