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

The kubeconfig file gives you cluster access. To fetch only that file, add `--fetch-kubeconfig` to the bootstrap command.
Do not replace the recovery key during a restore. Back up its password outside Git.
Git restores configuration, not databases or stored files. Back up that data separately.

## Add a service

Create the service under `apps/` with its image, ports, health probes, storage, and HTTPS route.
Add its namespace under `infrastructure/namespaces/` and a Flux Kustomization under `clusters/home/`.
Include both files in their resource lists.
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
new module pointer, run `git submodule update secrets`.
Merge the private Secret PR into `main` before merging its public PR.
Update the public pointer to a commit that belongs to private `main`.
Keep real secret files and recovery material in that private repository.

The required `Secrets main` status blocks pointers outside private `main`.
It also rejects a changed submodule repository, path, or branch.
The audit reads public PR metadata and private commit history. It never runs PR code or reads Secret files.
Its read-only deploy key stays in the `secrets-main` environment, which allows only `master`.
Public validation and deployment tests use no private credentials.

After a private merge, the scheduled audit refreshes open PR statuses every five minutes.
GitHub can delay scheduled jobs. To request an audit immediately, run this command from the public repository:

```bash
gh workflow run secrets-main.yaml --repo LionZXYOrg/infra --ref master
```

## MCP access over the LAN

The MCP endpoint is `https://SERVER_IP:8443/mcp`, with HTTP Basic authentication over HTTPS.
For this cluster, the LAN address is `192.168.1.150`.
The official [Kubernetes MCP chart](https://github.com/containers/kubernetes-mcp-server/tree/v0.0.67/charts/kubernetes-mcp-server) uses the existing read-only account.
Kubernetes blocks writes, Secret reads, and commands in pods.
The server also filters out write tools and blocks Secret resources.

The gateway requires a password for every MCP request.
The backend listens only on the loopback address inside its pod.
Only port `8443` is exposed on `SERVER_IP`.
The endpoint does not use a public domain or the shared Traefik routes.
Do not forward port `8443` through the router. That would expose the endpoint outside the LAN.

Keep credentials in the private `k3s-mcp/mcp-http-auth.sops.yaml` Secret.
Merge the private credentials PR before the public endpoint PR.
On an existing cluster, set the address before merging the public PR.
Run these commands on the workstation with the administrator kubeconfig:

```bash
export KUBECONFIG="$HOME/.kube/home-k3s.yaml"
export SERVER_IP=192.168.1.150
kubectl -n flux-system create configmap cluster-settings \
  --from-literal=SERVER_IP="$SERVER_IP" --dry-run=client -o yaml | kubectl apply -f -
```

New bootstrap runs store their `SERVER_IP` value in this ConfigMap automatically.
If the address or credentials are absent, the endpoint cannot become ready.
Flux installs the server after the account, secrets, and certificate controller are ready.
cert-manager creates a private certificate authority (CA), which signs the certificate for the IP address.
The gateway loads renewed certificates without a restart.

After merging both PRs, wait for the endpoint:

```bash
flux reconcile kustomization flux-system --with-source
kubectl -n flux-system wait kustomization/cluster-mcp-endpoint \
  --for=condition=Ready --timeout=10m
```

On your trusted workstation, install the Python requirements in `.venv`.
Initialize the private module before exporting client credentials.
Use your local SOPS identity to unlock the credentials.
The export command also reads the public CA certificate through your administrator kubeconfig.

```bash
git submodule update --init secrets
.venv/bin/python scripts/mcp_auth.py client --server-ip "$SERVER_IP"
unset KUBECONFIG
```

The command writes `ca.crt`, `curl.conf`, and `opencode.json` under `~/.config/k3s-mcp/`.
The directory uses mode `0700`. Each file uses mode `0600`.
Copy the `mcp` entry from that protected JSON file into your OpenCode configuration.
Start OpenCode with the CA certificate:

```bash
NODE_EXTRA_CA_CERTS="$HOME/.config/k3s-mcp/ca.crt" opencode
```

For another MCP client, use the same URL, Authorization header, and CA certificate.
Keep the exported files outside Git. They contain the password.
Do not disable certificate validation.
To test the connection with curl, run:

```bash
curl --config "$HOME/.config/k3s-mcp/curl.conf" \
  --header 'Content-Type: application/json' \
  --header 'Accept: application/json, text/event-stream' \
  --data '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl","version":"1"}}}'
```

For a new installation without the encrypted credential file, run `python3 scripts/mcp_auth.py create` first.
This helper uses `secretctl.py` to encrypt a random password and its authentication hash.
Commit and push the private module before updating its pointer in the public repository.
The Deployment tests use disposable credentials and make sure that unauthorized requests and writes fail.

## Matter over Thread

Home Assistant uses the ZBT-2 radio through a separate OpenThread Border Router.
The Sonoff radio stays with the Zigbee integration.
The router restores the preferred Thread network from Home Assistant and keeps its data on a persistent volume.
It stops if the saved network is missing or conflicts with the router configuration.

After deployment, add the OpenThread Border Router integration in Home Assistant.
Use `http://127.0.0.1:8081` as its address.
The control API listens only on the server itself.
Existing Matter pairing and Thread credentials stay in their current storage.

## Memory resources

Repository-managed workloads have no memory reservation or memory limit.
The manifests use `memory: null` to remove memory requests and limits.
Charts that reject null values omit the memory key instead.
Without a request, Kubernetes can copy a memory limit into the request.
Both fields stay unset to prevent that default.
CPU requests, CPU limits, and storage requests keep their existing values.

Flux controllers use a Kustomize patch so that generated manifests stay unchanged.
External application repositories own their chart patches and application-specific resource tests.
Tests inspect generated workloads to detect defaults that return after chart updates.
K3s system components outside this repository keep their own resource configuration.

This configuration does not reduce application RAM use.
If total RAM use exceeds capacity, Kubernetes can evict applications or the kernel can terminate processes.
Monitor actual RAM use and leave capacity for the operating system and K3s.

## Test changes

```bash
python3 -m unittest discover -s tests -v
bash scripts/check.sh
```

These checks work without private repository access. Every PR also runs a fresh
deployment with disposable secrets and local TLS in GitHub Actions. See the
[isolated deployment procedure](tests/isolated/README.md) to run it in a VM.

## Telegram bots

The [bot repository](https://github.com/LionZXYOrg/acorn-care-bot) owns application code, deployment manifests, public configuration, tests, and operating procedures.
Infrastructure selects one exact source commit and matching image digest.
It keeps the namespace, private Secret dependencies, and Flux deployment references.
The standalone service stays active. The shared suite stays suspended.
Follow the [bot deployment procedure](https://github.com/LionZXYOrg/acorn-care-bot/blob/main/docs/home-deployment.md) for activation or rollback.

## Automatic dependency updates

This repository uses [Renovate](https://docs.renovatebot.com/) to propose version
updates through pull requests. Install the
[Renovate GitHub App](https://github.com/apps/renovate) with access to
`LionZXYOrg/infra`, then merge the `renovate.json` configuration PR.
An app installation or organization administrator must grant access; committing
the configuration alone does not activate the bot.

Renovate opens update PRs during 00:00–06:00 Europe/London time, with no
concurrent or hourly PR limits and no automatic merging. Updates cover pinned bootstrap
tools, GitHub Actions, Python packages, Flux resources, application Helm charts,
and Docker image tags **together with their SHA-256 digests**. The dependency
dashboard lists updates that are waiting for review. Local application images
built from Git commit SHAs and the Immich database image are deliberately left
for coordinated manual upgrades.

Review each proposed update and require both GitHub Actions checks (`Validate`
and `Deployment`) before merging. The migration fixtures and chart archive
checksums under `tests/migrations/` may need manual refresh when versions change.
Because production Flux watches `master`, **merging a Renovate PR can deploy
updated software to the live cluster**. Keep high-risk database, controller, and
chart-major upgrades behind human review and backups.

## Simlock: K3s gateway + Raspberry Pi 5 + Khadas Edge2

### Assumptions

- The existing K3s/Flux stack in this repository is machine 1; it runs the **gateway only**.
- The Pi 5 and Edge2 use ARM64 Ubuntu and run native Simlock **workers**, not K3s workloads.
- `simlock.kulikov.uk` resolves from external networks to the Traefik HTTPS endpoint (with split-horizon DNS for LAN clients if needed), and the existing wildcard TLS certificate is valid.
- The gateway ingress has no client IP allowlist; requests are protected by Simlock's own role-based bearer tokens.
- Worker Android emulator support on Linux ARM64 is experimental: Google's standard host binaries are not Linux ARM64 builds. Prove a working emulator, arm64 system image, adb and KVM on EACH board before joining.

### Gateway deployment

This patch adds a Flux Kustomization and a namespace with a single gateway Deployment, Service,
PVC, and Traefik IngressRoute. It uses `node:22.13.1-bookworm-slim`, installs pinned
`simlock@0.8.0` from npm in an init container, and exposes port 4700 only via Traefik.
No Android SDK, emulator, or `/dev/kvm` is installed on the manager.

Before applying, consider replacing the runtime tag with a **digest-pinned**, prebuilt image
containing simlock@0.8.0. The init-container npm download is suitable for testing but
requires public npm registry reachability on every pod replacement.

After applying the patch and merging it into master (Flux deploys automatically):

```bash
kubectl -n simlock rollout status deploy/simlock-gateway
curl --fail https://simlock.kulikov.uk/v1/healthz
kubectl -n simlock exec deploy/simlock-gateway -c gateway -- simlock --gateway worker list
```

To make the gateway reachable from any network, publish a public DNS A/AAAA record
for `simlock.kulikov.uk` pointing to the publicly reachable Traefik ingress address.
Forward TCP 443 at the edge router/firewall to Traefik if the cluster is behind NAT;
use split-horizon DNS for local clients if hairpin NAT is unavailable. If inbound
connections cannot reach the cluster (such as with CGNAT), provide a compatible
TLS reverse tunnel that supports both WebSockets and long-lived HTTP streams.
Keep Simlock's internal port 4700 and the workers' ADB ports private; only
Traefik's HTTPS entrypoint should be public. HTTP must not be exposed directly.

**Security:** this intentionally exposes the Simlock gateway API and login page to
untrusted networks. Simlock enforces role-based bearer-token access, but an agent
token can execute commands on a leased worker and has the worker daemon user's
filesystem permissions. Issue a unique token per agent/worker, store tokens securely,
revoke compromised tokens immediately, and do not add an anonymous authentication
bypass. Consider additional edge rate limiting and monitoring before relying on
this service for public Internet access. Do not put an interactive login proxy
in front of the worker WebSocket uplink unless it can authenticate machine clients.

Create three tokens **inside the gateway pod** (commands print each secret once):

```bash
kubectl -n simlock exec deploy/simlock-gateway -c gateway --   simlock --gateway token create --role worker --label raspberry-pi-5
kubectl -n simlock exec deploy/simlock-gateway -c gateway --   simlock --gateway token create --role worker --label khadas-edge2
kubectl -n simlock exec deploy/simlock-gateway -c gateway --   simlock --gateway token create --role agent --label remote-automation
kubectl -n simlock exec deploy/simlock-gateway -c gateway --   simlock --gateway token create --role operator --label remote-console
```

Do not save or commit token values to the public infra repo; store them in a password manager.
The private infra-secrets repo is the only permitted repository location for secrets.
The gateway state (including its token records) persists in the PVC.

### On each worker: Pi 5 and Edge2

Install Node.js >=22.13 and simlock@0.8.0, and separately validate a working native ARM64
Android Emulator + platform-tools before joining. The previous single-board instructions
cover that experimental emulator installation. Then:

```bash
npm install -g simlock@0.8.0
simlock doctor
simlock config set http.enabled false
simlock config set android.emulator.headless true
simlock config set android.emulator.audio false
simlock config set android.emulator.gpu swiftshader_indirect
simlock config set capacity '{"strategy":"fixed","config":{"maxRunning":1,"android":{"maxRunning":1,"maxDevices":1}}}'
```

Connect each board with **its own** token (and adjust the label):

```bash
read -rsp 'Worker join token: ' JOIN_TOKEN; echo
LABEL=raspberry-pi-5  # use khadas-edge2 on the Khadas board
CFG=$(jq -cn --arg url 'wss://simlock.kulikov.uk' --arg token "$JOIN_TOKEN" --arg label "$LABEL" '{url:$url,token:$token,label:$label}')
simlock config set gateway "$CFG"
unset JOIN_TOKEN CFG
sudo loginctl enable-linger "$USER"
simlock daemon install
simlock daemon status
```

**Do not set `http.enabled=true` on a joined worker.** Workers maintain outbound
WebSocket uplinks to the gateway and need no incoming firewall/ADB ports.
Each board starts with capacity 1. Only increase concurrency after real RAM and boot tests.

### Validate the fleet

```bash
kubectl -n simlock exec deploy/simlock-gateway -c gateway -- simlock --gateway worker list
```

Expected: `raspberry-pi-5` and `khadas-edge2` both connected, with Android
capability available. Validate lease provisioning separately after both workers
can boot ARM64 Android emulators.

### Deployment notes

- `local-path` storage is node-local; if the K3s host is rebuilt without restoring the PVC,
  you will lose gateway token records and need to reissue worker/agent credentials.
- Traefik automatically proxies WebSocket upgrades. Test SSE streaming and long-lived
  requests end-to-end if you have strict upstream idle timeouts.
- This is an experimental image-on-start gateway deployment; for production,
  build and push a pinned immutable image and update the Deployment.
- `simlock --gateway` (not `mode: gateway`) is the current mode selector.
- A 0.6.0 worker cannot serve leases through a 0.8.0 gateway. Upgrade both ends before you resume leases.
