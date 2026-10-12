# speedtest migration

Move the standalone LibreSpeed web application.

This is preparation from [the old snapshot](https://github.com/LionZXY/rpi-infra/tree/ccfc5ecd5433fa0d7fe3844f167612b8ed6e2297/app/speedtest), not a live inventory. Both the Flux Kustomization and every HelmRelease are suspended. Do not activate this proposal before the final inventory and isolated restore rehearsal.

MODE=standalone has no persistent volume or application Secret in this snapshot. There is no database to move. Check the final MODE and whether telemetry/database/IP service settings were later added. The floating latest tag is pinned to a provisional registry digest and must be compared with the final image. Size the pod and network bandwidth for the final nodes; exposing this endpoint can consume significant bandwidth.

Old host: `speedtest.kulikov.uk`. Rehearsal host: `speedtest.test.kulikov.uk`. The cluster wildcard certificate covers only the rehearsal host. Provision a matching certificate and review the hostname/domain before moving the old DNS record.

## Secrets and merge order

No separate application Secret is present in the snapshot. Any passwords, notification URLs or credentials inside application data must remain in encrypted backups; add a private Secret if the final inventory requires new environment configuration.

## Rehearse and restore

Use an isolated cluster and a copy of the data. Run backup and restore pipelines in Bash with `set -euo pipefail`; a failed producer must fail the whole operation, and every restore must pass integrity checks before the old copy can be retired. Set `KUBECONFIG` to the explicit disposable cluster path. Use test integration credentials. Confirm storage capacity and class before provisioning; `local-path` is provisional node-local storage, not HA or a backup. Apply the namespace and preparation with suspension preserved:

```bash
kubectl --kubeconfig "$KUBECONFIG" apply -f infrastructure/namespaces/speedtest.yaml
kubectl --kubeconfig "$KUBECONFIG" apply -k apps/speedtest
```

This snapshot declares no persistent local data. Preserve upstream queues/integration state, private configuration and the old image/source for rollback.

## Health and acceptance

Require HTTP 8080 probes, load the UI over trusted staging HTTPS and perform download/upload/latency tests from the LAN and intended external route. Check the browser uses the correct origin and HTTPS for the test endpoints and that measured traffic reaches this server.

Run the local unit suite, `bash scripts/check.sh`, and `python3 scripts/migration_check.py --render`. These render the pinned chart and verify wiring, suspension, secrets, mounts, single-writer strategy and probes. The required PR Deployment check validates the shared infrastructure and that this service remains inactive; it is not an application data-restore test. Complete the functional rehearsal above with restored data and test accounts before cutover. Never save raw application logs, Secret YAML or decrypted data in CI reports.

## Cutover and rollback

1. Reconcile this proposal with the final service inventory, version/digest, storage size, DNS, architecture and credentials. Require both PR checks on the latest revision.
2. Finish the isolated restore and acceptance checks. Stop the old writer, take a final encrypted backup and repeat the restore into production storage. Keep the old container stopped and its data intact.
3. Review a separate activation commit clearing `spec.suspend` on the service HelmRelease(s) and `clusters/home/speedtest.yaml`. Reconcile Flux, require all release/workload health checks, then switch the reviewed DNS/client/LAN route.
4. Monitor functional behavior and data counts through the agreed rollback window.
5. To roll back, suspend the Flux Kustomization and HelmRelease(s), stop/scale the new workloads to zero and confirm there are no writers. Restore the old DNS/client route and restart the old containers from the retained matching backup/image. If new writes occurred, reconcile them or restore a compatible backup before restarting the old writer. Do not copy migrated database files backward across schema versions. Preserve new PVCs and encrypted backups for diagnosis.

The Apache image keeps its UID/GID and file-ownership capabilities for startup while dropping NET_RAW and disabling privilege escalation. `python3 tests/migrations/speedtest_smoke.py` boots the pinned image with an isolated network and requires HTTP 200; CI runs the same smoke test without production data.
