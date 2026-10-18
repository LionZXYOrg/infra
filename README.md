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

## Local MCP access through stdio

Flux installs a dedicated `k3s-mcp` ServiceAccount and read-only RBAC bindings
from `infrastructure/mcp/` through the `cluster-mcp-rbac` Kustomization. This
lets [ry-ops/k3s-mcp-server](https://github.com/ry-ops/k3s-mcp-server) inspect
pods, logs, nodes, Flux resources and Traefik routes. It **cannot** read Kubernetes
Secrets, exec into pods, or alter cluster objects. No token or kubeconfig is
stored in Git. This optional setup uses stdio on your workstation.
The LAN endpoint above uses a separate server in the cluster with the same read-only account.

After the Flux Kustomization is ready, mint a temporary read-only kubeconfig **on
your workstation**, using an admin kubeconfig for this step only:

```bash
export KUBECONFIG="$HOME/.kube/home-k3s.yaml"
flux reconcile kustomization cluster-mcp-rbac --with-source
bash scripts/k3s-mcp-kubeconfig.sh
unset KUBECONFIG  # do not pass the administrator identity to the MCP process
```

The output is `~/.kube/k3s-mcp-readonly.yaml` (mode `0600`). Kubernetes may shorten
the requested 24-hour token lifetime. Re-run the script with the admin kubeconfig
when the token expires. You can change the output with `K3S_MCP_KUBECONFIG=/absolute/path`.
Restart the MCP client after refreshing a token; the server loads its kubeconfig at startup.
The Kubernetes API (TCP 6443) must be reachable privately from the MCP client.

Install [`uv`](https://docs.astral.sh/uv/getting-started/installation/) and add
this entry to your **local** OpenCode `~/.config/opencode/opencode.json`, merging
it with any existing MCP configuration. Replace both `/absolute/path/...` values
with real absolute paths (`~` and `$HOME` are not expanded in JSON). Keep the
second directory dedicated to this MCP server: **do not put an admin kubeconfig
there**, since the upstream `use_cluster` tool can switch identities.

```json
{
  "mcp": {
    "k3s-readonly": {
      "type": "local",
      "command": [
        "uvx", "--from",
        "git+https://github.com/ry-ops/k3s-mcp-server@e2c3d3b11c3d2d1a833e6e134ab5f2109b850b6a",
        "k3s-mcp-server"
      ],
      "environment": {
        "KUBECONFIG": "/absolute/path/.kube/k3s-mcp-readonly.yaml",
        "K3S_KUBECONFIG_DIR": "/absolute/path/.kube/k3s-mcp-only",
        "K3S_PROVISIONING": "false"
      }
    }
  }
}
```

The example pins the upstream v1.6.0 commit. For another stdio-compatible MCP
client, use the same command and environment variables. Verify the RBAC with
these checks, using **only** the generated credential:

```bash
MCP_KUBECONFIG="$HOME/.kube/k3s-mcp-readonly.yaml"
kubectl --kubeconfig="$MCP_KUBECONFIG" auth can-i list pods --all-namespaces  # yes
kubectl --kubeconfig="$MCP_KUBECONFIG" auth can-i get pods/log -n demo       # yes
kubectl --kubeconfig="$MCP_KUBECONFIG" auth can-i get secrets -n demo        # no
kubectl --kubeconfig="$MCP_KUBECONFIG" auth can-i create pods/exec -n demo  # no
kubectl --kubeconfig="$MCP_KUBECONFIG" auth can-i patch deployments -n demo # no
```

RBAC only constrains this MCP identity; an AI tool with separate shell access to
an admin kubeconfig can still modify the cluster. Keep administrator credentials
outside agent-accessible paths and never expose the Kubernetes API publicly.

## Test changes

```bash
python3 -m unittest discover -s tests -v
bash scripts/check.sh
```

These checks work without private repository access. Every PR also runs a fresh
deployment with disposable secrets and local TLS in GitHub Actions. See the
[isolated deployment procedure](tests/isolated/README.md) to run it in a VM.
