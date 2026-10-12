# ddns migration

Preserve Cloudflare DDNS without two active DNS writers.

This is preparation from [the old snapshot](https://github.com/LionZXY/rpi-infra/tree/ccfc5ecd5433fa0d7fe3844f167612b8ed6e2297/app/ddns), not a live inventory. Both the Flux Kustomization and every HelmRelease are suspended. Do not activate this proposal before the final inventory and isolated restore rehearsal.

The entire old config.json is imported into the private ddns-config Secret. No PVC or database is needed. Preserve zone IDs, subdomains, A/AAAA settings, proxy flags and authentication mode. The final image is a Rust scratch container; the local binary --help probes need no shell and perform no DNS writes. They detect binary availability, not Cloudflare authentication or successful updates. Inspect the final config schema and permissions before activation. Rehearse with a dedicated test zone and purgeUnknownRecords disabled; do not use the production config in CI.

## Secrets and merge order

Private Secret names: `ddns-config` in namespace `ddns`. The paired private PR uses `scripts/secretctl.py`; only SOPS encrypted manifests belong in `infra-secrets`. Verify or rotate snapshot credentials before cutover. Do not print values or put them in Helm values, shell arguments or public files.

Merge the public preparation first so its namespace exists while the service stays suspended; then merge the paired private PR into private `main`. The public Gitlink references a published private commit; Flux follows `main` independently. Resolve the namespace/secret indexes by keeping every entry from merged service PRs. After merging the private service PRs, update the public Gitlink to the published private `main` commit containing all merged Secrets; do not leave it pointing at an individual draft branch for bootstrap. Wait for `cluster-secrets` Ready before activation.

## Rehearse and restore

Use an isolated cluster and a copy of the data. Run backup and restore pipelines in Bash with `set -euo pipefail`; a failed producer must fail the whole operation, and every restore must pass integrity checks before the old copy can be retired. Set `KUBECONFIG` to the explicit disposable cluster path. Use test integration credentials. Confirm storage capacity and class before provisioning; `local-path` is provisional node-local storage, not HA or a backup. Apply the namespace and preparation with suspension preserved:

```bash
kubectl --kubeconfig "$KUBECONFIG" apply -f infrastructure/namespaces/ddns.yaml
kubectl --kubeconfig "$KUBECONFIG" apply -k apps/ddns
```

This snapshot declares no persistent local data. Preserve upstream queues/integration state, private configuration and the old image/source for rollback.

## Health and acceptance

Check the old configuration with the operator locally without printing authentication fields. Using a test zone, compare public IP discovery and the intended A/AAAA records before and after one update cycle; confirm unrelated records remain. Confirm only one updater owns each production record. Observe two successful update intervals after cutover; the local probe alone is insufficient.

Run the local unit suite, `bash scripts/check.sh`, and `python3 scripts/migration_check.py --render`. These render the pinned chart and verify wiring, suspension, secrets, mounts, single-writer strategy and probes. The required PR Deployment check validates the shared infrastructure and that this service remains inactive; it is not an application data-restore test. Complete the functional rehearsal above with restored data and test accounts before cutover. Never save raw application logs, Secret YAML or decrypted data in CI reports.

## Cutover and rollback

1. Reconcile this proposal with the final service inventory, version/digest, storage size, DNS, architecture and credentials. Require both PR checks on the latest revision.
2. Finish the isolated restore and acceptance checks. Stop the old writer, take a final encrypted backup and repeat the restore into production storage. Keep the old container stopped and its data intact.
3. Review a separate activation commit clearing `spec.suspend` on the service HelmRelease(s) and `clusters/home/ddns.yaml`. Reconcile Flux, require all release/workload health checks, then switch the reviewed DNS/client/LAN route.
4. Monitor functional behavior and data counts through the agreed rollback window.
5. To roll back, suspend the Flux Kustomization and HelmRelease(s), stop/scale the new workloads to zero and confirm there are no writers. Restore the old DNS/client route and restart the old containers from the retained matching backup/image. If new writes occurred, reconcile them or restore a compatible backup before restarting the old writer. Do not copy migrated database files backward across schema versions. Preserve new PVCs and encrypted backups for diagnosis.

Paired private preparation: [infra-secrets PR #1](https://github.com/LionZXYOrg/infra-secrets/pull/1).
