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
The Telegram chart uses a Helm post-render patch for its fixed defaults and startup container.
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

## Shared Telegram bot

The [application repository](https://github.com/LionZXYOrg/acorn-care-bot) owns the workers, gateway, Helm chart, tests, and operating procedure.
`apps/telegram-bot-suite` contains only Flux resources and this cluster's values.
Flux reads `charts/telegram-bot-suite` from an exact application commit.
The image uses a tested commit tag and immutable digest.

The preparation keeps both Flux objects suspended and sets zero replicas.
The current Acorn Deployment, reminder jobs, and saved records remain unchanged.
Do not enable both care receivers. Duplicate reminders can cause caregivers to issue the same portion twice.
Follow the [activation and rollback procedure](https://github.com/LionZXYOrg/acorn-care-bot/blob/main/docs/shared-suite.md) before enabling the suite.

For this cluster, preserve Secret `acorn-care-bot`, the existing care claim, and the approved care plan.
Supply Secret `greenwich-bin-bot-property` through the private secret procedure before activation.
Stop bin job `d3cf4dc9-a75d-42b2-943c-0e1d6ba7ac36` during the coordinated cutover.
Keep retired duplicate `cb3196b1-dad3-4ea6-82b1-608143c735ad` disabled.
Old standalone Acorn manifests remain until that cutover. They still control the live service.

## Acorn care bot

The [separate bot repository](https://github.com/LionZXYOrg/acorn-care-bot) contains the application and its image build.
The bot sends shared care reminders to one Telegram topic.
It saves each caregiver confirmation before it answers a button press.
The planned meal times are `07:00`, `14:00`, and `19:00` in `Europe/London`.
Wet food uses can fractions. Dry food uses grams.

The owners approved public storage of the chat, topic, caregiver IDs, and recurring care plan.
`apps/acorn-care-bot/runtime.properties` contains the Telegram destination, numeric caregiver IDs, and the fresh-start setting.
`apps/acorn-care-bot/config.json` contains the approved portions, stage dates, caregiver mentions, and `add_full` transfer policy.
The morning time applies from `2026-10-20`. This configuration does not recreate the earlier breakfast on `2026-10-19`.
The Deployment reads these files through a generated ConfigMap, a Kubernetes object for nonsecret configuration.
A change to either file changes the ConfigMap name and the Deployment references.

The owners disabled automatic repeat reminders with `automatic_repeats: false` on `2026-10-20`.
The bot still sends each scheduled checklist once at `07:00`, `14:00`, and `19:00`.
An explicit snooze sends one reminder at its requested time.
The updated image applies this setting to existing state at startup without changing confirmations, portions, holds, or pending transfers.

The owners requested bowl washing and wiping the feeding area once daily, at breakfast.
`bowls_morning_only: true` selects this schedule. Dinner keeps its litter task.
Breakfast keeps litter, bowls, and fresh water. Lunch has no routine cleaning.
This setting requires a bot image that supports the morning-only startup update.
That update preserves completed tasks, historical records, food portions, and explicit one-time transfers.
It cancels only open routine bowl tasks outside breakfast from the activation date onward.
Removing the flag does not restore the previous cleaning schedule.

Keep the bot token in the private secrets module.
Do not add it to either public configuration file.
The application Secret needs only the existing `bot-token` key.
The Deployment mounts that key separately from the public configuration.
Numeric caregiver IDs still control access. A displayed username does not grant access.

The repository and GHCR image are public.
The Deployment pulls the image without registry credentials or `imagePullSecrets`.
Repository visibility does not change package visibility. Configure the GHCR package separately.
The image uses a tested commit tag and an immutable digest for both supported architectures.
The bot needs outbound HTTPS, not a public HTTP route.
Its health endpoints stay inside the cluster.

The Flux object enables one replica with the `Recreate` strategy.
Do not run both reminder services. Duplicate requests can cause caregivers to issue the same portion twice.
Before merging this activation, complete the coordinated cutover:

1. Make sure that the pinned image is available without registry credentials.
2. Make sure that the existing application Secret contains `bot-token`.
3. Give the separate bot access to the configured Telegram group and topic.
4. Make sure that the old care record has no active health-related hold.
5. Stop the old reminder scheduler and any pending reminder jobs before merging the activation.
6. Keep the old care record unchanged as an archive.

If the application Secret is absent, use `python3 scripts/secretctl.py add acorn-care-bot acorn-care-bot --prompt bot-token`.
If `bot-token` already exists, preserve it. No additional application Secret keys are required.
Use a separate bot token. Do not reuse the assistant token or change its Telegram listener.
Commit private changes before updating the public submodule pointer.
Do not read or copy the bot token to test public registry access.

The owners requested a fresh record without importing existing confirmations or queued transfers.
`ACORN_FRESH_START=1` selects this mode for the first start on an empty volume.
The bot records its startup time and creates checklists only for later planned care times.
It does not replay earlier feedings or change the recurring portions and stage dates.
On restart, it keeps that original cutoff and all records that the new bot creates.
This setting never resets an existing volume.

Stop the old care scheduler before activating the new bot.
Keep the old record unchanged as an archive. Do not import its marks, transfers, or Telegram message bindings.
If a later cutover requires history, follow the bot repository's explicit migration procedure instead of the fresh-start mode.
A health-related hold needs separate review before a fresh start. Do not discard an active hold.
The new service saves its record at `/data/state.json` on the `acorn-care-bot-state` volume.
The volume survives Flux pruning and needs a separate backup.

After the activation merges, make sure that Flux applies its revision.
Use `Recreate` so that only one process writes the record.
Do not scale the bot above one replica.
Make sure that `/readyz` succeeds and test one confirmation with the new bot.
If startup fails, stop the new bot before resuming the old scheduler.
A local-path volume stays on its original node. Follow the cluster backup procedure before moving it.

Public tests render the ConfigMap, Secret references, storage, and single-replica activation.
They also exercise configuration-name changes without contacting Telegram or reading credentials.
The disposable deployment test excludes the active production Flux object and rejects any bot workload that starts there.
Prepared applications still enter CI only as suspended Flux objects.
The disposable cluster does not run the bot or use live care records.
A real Telegram confirmation test remains part of activation, not a public CI test.

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
