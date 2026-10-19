# Isolated deployment validation

GitHub Actions deploys the production namespaces, Traefik, cert-manager, and a test demo app on a fresh Ubuntu 24.04 runner.
The demo app and its namespace exist only under `tests/isolated/`.
Production does not deploy the demo app.
Two local read-only Git
repositories require independent disposable credentials. A fresh age identity
encrypts a harmless probe Secret. TLS uses `demo.infra.test` and the self-signed
test issuer; production ACME, Cloudflare credentials, and recovery keys are absent.
No private submodule or GitHub repository credentials are required.

The test checks all four Flux controllers, both sources, all six Kustomizations,
both Helm releases, the certificate, and the demo Deployment. It verifies the
decrypted probe, certificate-verified HTTPS, a new encrypted probe commit reaching
the cluster, and successful repeated reconciliation. Unit tests also exercise
authentication failures, cross-repository access, forbidden pushes, unsafe fixture
exports, checksum failures, and failed deployment assertions.

## Run on a disposable VM

Use a fresh Ubuntu 24.04 VM with no swap and internet access to public tool,
chart, and image registries. A full Lima VZ VM on macOS works; `lima.yaml` disables
host mounts, SSH agent forwarding, and automatic port forwarding. OrbStack Linux
machines share kernel swap devices and fail the unchanged provisioner's no-swap
check.

Inside the disposable VM:

```bash
sudo apt-get update
sudo apt-get install -y git age python3-venv
git clone https://github.com/LionZXYOrg/infra.git
cd infra
# Check out the revision you intend to test. Do not initialize submodules.
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
bash tests/ci/run.sh --disposable /tmp/infra-ci-report
cat /tmp/infra-ci-report/deployment.json
```

The command refuses an existing K3s installation/configuration and never sources
production settings. It installs the pinned tools and invokes the production
server provisioner. Every cluster command uses its new private kubeconfig. The
local Git server, fixture repositories, age identity, credentials, and kubeconfig
copy are removed when the test exits. K3s and the test resources remain only on
the disposable machine: delete the entire VM after inspecting the report.

The report records the tested public revision, pinned versions, and assertion
outcomes. On failure it adds only resource names and condition type/status/reason;
raw logs, Secret contents, kubeconfigs, and environment dumps are excluded.

Build the fixtures with `kubectl kustomize tests/isolated`,
`kubectl kustomize tests/isolated/namespaces`,
`kubectl kustomize tests/isolated/tls`, and
`kubectl kustomize tests/isolated/demo`. Production DNS/ACME and the workstation
SSH wrapper are outside this test's scope.
