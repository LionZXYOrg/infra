# Repository guide for agents

## Scope and layout

This repository configures a K3s cluster through Flux. The production cluster
watches `master`; changes merged there can reconcile automatically.

- `apps/`: application Deployments, Services, and Traefik routes.
- `infrastructure/`: namespaces, Helm controllers, production issuers, and TLS.
- `clusters/home/`: production Flux sources and dependency ordering.
- `bootstrap/versions.env`: tested K3s, Flux, SOPS, and Gitleaks pins.
- `scripts/`: bootstrap, provisioning, secret management, and public validation.
- `secrets/`: Gitlink to private `LionZXYOrg/infra-secrets`; optional for public checks.
- `tests/ci/`: disposable Git/SOPS fixtures and the full deployment runner.
- `tests/isolated/`: local TLS and demo overlays used by deployment tests.

Implement requested changes directly. Keep human setup and operational guidance
in `README.md`, and agent instructions in this file. Do not create a `docs/`
directory or commit process plans, design documents, or execution ledgers.
Use `codex/` for new branches and continue an existing PR on its current branch.
Do not merge a PR without an explicit request.

## Secrets and production boundaries

Do not initialize the private submodule just to run public checks. Real Secret
manifests, SOPS rules, and recovery material belong only in the private module,
including encrypted copies. Use `scripts/secretctl.py` for requested secret edits.
Never print secret values, identities, tokens, kubeconfigs, or credential-bearing
command output. Test fixtures must use generated credentials and harmless values.

Production Flux reads the private repository through the `infra-secrets` source
and its own read-only credential; infrastructure sources must not recurse into
submodules. Preserve existing Flux dependencies between namespaces, secrets,
controllers, issuers, TLS, and apps, and bootstrap credential validation/reuse.

Do not deploy to production to validate a change. Full deployment tests require a
fresh disposable Ubuntu VM with no swap. Keep the production provisioner's guards
against existing configuration, mismatched versions, occupied ports, and swap.
Keep tool version pins unless the requested change needs a tested upgrade.

## Changes and regression tests

Every code or infrastructure change should add or expand meaningful tests for its
behavior and likely failure cases. Documentation-only edits need no new runtime
tests. Test coverage is a review requirement, not a test-file-change CI gate.

When adding an application:

1. Create its resources under `apps/`, with health probes,
   security settings, Service ports, and TLS routing.
2. Add a namespace under `infrastructure/namespaces` and update that directory's
   `kustomization.yaml`. `secretctl.py index` updates both namespace and secret
   indexes and requires the private module; edit the public index directly when
   private access is unavailable.
3. Add a Flux Kustomization under `clusters/home`, include it in the root resource
   list, and preserve its namespace and dependencies.
4. Extend isolated fixtures/assertions when the application needs deployment
   coverage. Use test resources instead of production services or credentials.

For requested private changes, commit and push the private module before updating
its Gitlink here. Bootstrap requires clean, published checkouts in both repos.

## Verification and CI

Install Python requirements in a virtual environment. Install `age` separately and
use `scripts/install-tools.sh` for pinned CLIs. Keep `$HOME/.local/bin` in `PATH`.

```bash
python3 -m unittest discover -s tests -v
bash scripts/check.sh
```

The unit suite exercises real local Git authentication and SOPS round trips;
SOPS/age-dependent cases must run in CI. Public checks must work without access to
the private module. Kustomize builds cover production manifests and isolated
overlays.

Run a full deployment only on an explicitly disposable fresh host:

```bash
bash tests/ci/run.sh --disposable /tmp/infra-ci-report
```

See `tests/isolated/README.md` for setup. The test must verify Flux/Helm/application
readiness, SOPS decryption and updates, trusted HTTPS, repeat reconciliation, and
absence of production resources. Do not weaken assertions or use insecure TLS to
make a test pass. Failure artifacts may contain only allowlisted readiness data
and assertion results, never raw logs, Secret YAML, or environment dumps.

Keep public validation and deployment fork-compatible: `pull_request`, read-only permissions,
SHA-pinned Actions, no persisted checkout credentials, no private submodules, and
no repository secrets. Keep the stable `Validate` and `Deployment` checks and
verify both on the latest PR revision before reporting success.

Run the separate `Secrets main` audit only from trusted `master` code.
Treat PR trees and `.gitmodules` as untrusted data. Never execute PR code in this audit.
Keep its read-only deploy key in the `secrets-main` environment.
Restrict that environment to the `master` branch. Never move the key to a repository secret.
Fetch only private commit history. Never download private trees or Secret files for this audit.
Keep `Secrets main` required on `master`. Never publish success for a commit outside private `main`.
