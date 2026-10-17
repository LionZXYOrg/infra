# Home infrastructure

K3s runs the services. Flux applies changes from Git once a minute.
Traefik handles HTTPS. cert-manager renews the certificate for `*.kulikov.uk`.
SOPS encrypts all user-managed secrets with one age key.

## Start or restore the cluster

Use a clean Ubuntu 24.04 or Debian server with a fixed LAN IP, cgroup v2, and no swap.
You need GitHub admin access to both repositories and the password for
`secrets/bootstrap/sops-age.age`, or the matching local SOPS age identity.

On Linux, install the tools as root. For a regular account, add `sudo` before each `apt-get` command.

```bash
apt-get update
apt-get install -y git gh age curl ca-certificates python3 python3-venv openssh-client
```

On a Mac with Homebrew, run `brew install git gh age python` without `sudo`.
Make sure that SSH works from your Mac to the Linux server.
Run the shared steps on the computer that will start bootstrap:

```bash
gh auth login --hostname github.com --git-protocol https --web --scopes repo
gh auth setup-git
gh repo clone LionZXYOrg/infra -- --recurse-submodules
cd infra
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
bash scripts/install-tools.sh
export PATH="$HOME/.local/bin:$PATH"
```

Replace `192.168.1.50` with your server IP. On the Linux server, run:

```bash
SERVER_IP=192.168.1.50 bash scripts/bootstrap.sh
```

From your Mac, use SSH instead. Replace `root` with your SSH user if needed:

```bash
SERVER_IP=192.168.1.50 bash scripts/bootstrap.sh --ssh root@192.168.1.50
```

Enter the recovery password. The script installs K3s and Flux, then restores services and secrets from Git.
For another branch, set `GITHUB_BRANCH` and check out that branch first.
Commit and push all changes in both repositories before bootstrap.

Apply the [K3s network rules](https://docs.k3s.io/installation/requirements#networking). Keep port `6443` private.
For public access, forward TCP ports `80` and `443` to the server.
Point `*.kulikov.uk` to your public IP with Cloudflare DNS only.

```bash
export KUBECONFIG="$HOME/.kube/home-k3s.yaml"
flux get kustomizations -A
kubectl get nodes
```

Open `https://demo.kulikov.uk`.
The kubeconfig file gives you cluster access. To fetch only that file, add `--fetch-kubeconfig` to the bootstrap command.
Do not replace the recovery key during a restore. Back up its password outside Git.
Git restores configuration, not databases or stored files. Back up that data separately.

## Add a service

Use `apps/demo/` as a template for the image, ports, probes, storage, and HTTPS
route. Add the service's namespace under `infrastructure/namespaces/` and a Flux
Kustomization under `clusters/home/`; include both in their resource lists.
Use `myapp.kulikov.uk` for the route and `tls: {}` for the shared certificate.

If the service needs a secret, update the private module first:

```bash
git submodule update --init secrets
git -C secrets switch main
git -C secrets pull --ff-only
```

```bash
python3 scripts/secretctl.py add myapp myapp-config --prompt API_KEY
```

Use `set` instead of `add` to change an existing secret, and reference its name and
field from the Deployment. Commit and push the private changes before committing
the updated module pointer here. Validate changes with the commands below and
merge into `master`; Flux deploys them automatically.
For errors, run `flux logs --all-namespaces --level=error`.
Restart affected Pods after a Secret change if they read it through environment variables.

## Private secrets module

`secrets/` is a Git submodule of the private [infra-secrets repository](https://github.com/LionZXYOrg/infra-secrets).
It contains encrypted Secret manifests and the recovery bundle.

For an existing checkout, run `git submodule update --init secrets`. After pulling a
new module pointer, run `git submodule update secrets`. Secret changes require a
commit and push inside `secrets/`, followed by a commit of the module pointer here.
Keep real secret files and recovery material in that private repository.

## Monitor the cluster

Monitoring is managed by Flux under `infrastructure/monitoring/`. It uses
[kube-prometheus-stack](https://github.com/prometheus-community/helm-charts/tree/main/charts/kube-prometheus-stack)
92.3.0: Prometheus, Grafana, Alertmanager, node-exporter, and kube-state-metrics.
This is a single-node baseline, not an HA deployment. It keeps the Prometheus
Operator, Kubernetes dashboards, recording rules, and alerting in one chart. The
VictoriaMetrics Kubernetes stack is also an integrated alternative, especially
worth evaluating if measured Prometheus resource use becomes a constraint; no
head-to-head resource benchmark has been performed for this host. Loki, tracing,
and remote storage are deliberately not installed on a small home node.

`infra-monitoring` waits for the existing secrets layer, then installs the chart.
`monitoring-config` waits for its HelmRelease to be healthy before creating
PodMonitors, PrometheusRules, and the home dashboard. Neither layer blocks apps or
depends on public TLS issuance. The stateful stack is orphaned rather than pruned
when its Flux Kustomization is removed; decommission it explicitly.

### Access and storage

No monitoring ingress, LoadBalancer, or NodePort is created. Use your existing
private kubeconfig and leave port-forward bound to localhost:

```bash
kubectl -n monitoring port-forward svc/grafana 3000:80
```

Open `http://127.0.0.1:3000` and log in as `admin`. The chart generates a random
password in the `grafana` Secret; anonymous access and signup are disabled. On
macOS, copy it directly to the clipboard without printing it:

```bash
kubectl -n monitoring get secret grafana -o jsonpath='{.data.admin-password}' | base64 --decode | pbcopy
```

Do not paste credentials into chat, tickets, or Git. The random password belongs
to this installation; restoring the Grafana database does not automatically reset
its existing administrator password. For predictable credential management, use
`grafana.admin.existingSecret` in private values and manage that Secret with SOPS.

The chart provides Kubernetes dashboards; **Home K3s overview** adds host CPU,
memory, disk space, pod memory, Flux state, Traefik traffic, and certificate life.
The sidecars only discover dashboard/datasource ConfigMaps in `monitoring`.
The deployment check asserts Grafana availability and dashboard ConfigMap
reconciliation; it does not authenticate to Grafana's UI or inspect the
provisioned dashboard API.

Prometheus scrapes every 60 seconds and retains up to **7 days or 8 GB**, whichever
limit is reached first. Its PVC requests 10 GiB to leave room for WAL/head data;
Grafana and Alertmanager request 1 GiB each. `local-path` requests are **not hard
disk quotas**, replicas, or backups. Watch the backing filesystem and back up
Grafana data separately. Historical metrics are expendable during disaster
recovery. The long-running containers request approximately 1 GiB RAM on one
node, including sidecars/reloaders, with additional transient installation work.
This is a scheduling budget, not measured consumption or a guarantee of fit.
Check node capacity and current workload usage before merging:

```bash
kubectl top nodes
kubectl top pods -A
flux get kustomizations -A
kubectl -n monitoring get helmreleases,pods,pvc
```

The standard workload, node, disk, and monitoring-health alerts remain enabled.
K3s's separate scheduler/controller-manager/proxy and etcd targets/rules are
turned off, rather than opening host metrics listeners or generating false
component-down alerts. API-server, kubelet/cAdvisor, and CoreDNS monitoring remain.
Kubelet serving certificates must validate against the cluster CA; investigate CA
or SAN errors instead of disabling TLS verification when a target is down.

Flux readiness comes from kube-state-metrics custom-resource metrics
(`gotk_resource_info`), while PodMonitors collect controller performance, Traefik,
and cert-manager metrics. Core Kubernetes collectors remain enabled. Suspended
Flux resources do not trigger the reconciliation-failed alert. See the
[Flux monitoring documentation](https://fluxcd.io/flux/monitoring/metrics/) and
[K3s metrics notes](https://docs.k3s.io/reference/metrics).

### Enable actual notifications

**The default Alertmanager receiver is `null`: alerts are evaluated and visible,
but no email, Telegram message, or webhook is sent.** A delivery destination has
not been chosen or configured. Inspect the alerts and targets locally:

```bash
kubectl -n monitoring port-forward svc/monitoring-prometheus 9090:9090
# In another terminal, as needed:
kubectl -n monitoring port-forward svc/monitoring-alertmanager 9093:9093
```

Use `scripts/secretctl.py` to manage an optional `monitoring-private-values`
Secret in namespace `monitoring`, with a `values.yaml` field containing Helm
values. Keep it exclusively in `infra-secrets`, even when encrypted. Configure
`alertmanager.config.route` and `alertmanager.config.receivers` for your chosen
provider, including credentials there. The private values are merged **after**
the public ConfigMap. Commit/push the private module before its public Gitlink.
No new monitoring Secret is required for the initial monitoring installation.
The existing cluster bootstrap still needs its private secrets source; public
checks and isolated CI use no production secrets. Add `reconcile.fluxcd.io/watch: Enabled` to the Secret for immediate Helm
reconciliation; otherwise the HelmRelease interval is 10 minutes.

Test a real notification after setup. Monitoring inside a single node cannot
report that node's complete power/network failure. Use an external uptime check
or an outside heartbeat receiver for `Watchdog`; set its repeat interval shorter
than the external timeout. This external service is not provisioned here.

### Validate and upgrade

The existing public checks now build both monitoring Kustomizations. The isolated
Deployment check deploys the same monitoring configuration with disposable
secrets/local TLS, checks real metrics and all discovered scrape targets, checks
PVCs/private Services/Grafana health, and verifies custom rule evaluation.
Synthetic PromQL tests run in a short-lived Job using the deployed Prometheus
image; they cover sustained/transient failures, healthy/suspended resources,
missing metrics, certificate readiness, and the expiry threshold. These checks
run again after repeat reconciliation. No production kubeconfig is used.

Change the pinned chart version only through a reviewed PR. Flux manages CRD
upgrades with `CreateReplace`; consult the chart's upgrade notes for each change.
Do not assume that reverting a chart version safely downgrades its CRD schemas.
Tune `infrastructure/monitoring/stack/values.yaml` after measuring actual load.

## Test changes

```bash
python3 -m unittest discover -s tests -v
bash scripts/check.sh
```

These checks work without private repository access. Every PR also runs a fresh
deployment with disposable secrets and local TLS in GitHub Actions. See the
[isolated deployment procedure](tests/isolated/README.md) to run it in a VM.
