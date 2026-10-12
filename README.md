# Home infrastructure

K3s runs the services. Flux applies changes from Git once a minute.
Traefik handles HTTPS. cert-manager renews the certificate for `*.test.kulikov.uk`.
SOPS encrypts all user-managed secrets with one age key.

## Start or restore the cluster

Use a clean Ubuntu 24.04 or Debian server with a fixed LAN IP, cgroup v2, and no swap.
You need GitHub admin access and the password for `bootstrap/sops-age.age`.
Use your own checkout and GitHub login, including in a Linux root session.

On Linux, install the tools as root. For a regular account, add `sudo` before each `apt-get` command.

```bash
apt-get update
apt-get install -y git gh age curl ca-certificates python3 python3-venv
```

On a Mac with Homebrew, run `brew install git gh age python` without `sudo`.
Make sure that SSH works from your Mac to the Linux server.
Run the shared steps on the computer that will start bootstrap:

```bash
gh auth login --hostname github.com --git-protocol https --web --scopes repo
gh auth setup-git
gh repo clone LionZXYOrg/infra
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
It detects the owner type and default branch (`master` here).
For another branch, set `GITHUB_BRANCH` and check out that branch first.
Commit and push all changes before bootstrap.

Apply the [K3s network rules](https://docs.k3s.io/installation/requirements#networking). Keep port `6443` private.
For public access, forward TCP ports `80` and `443` to the server.
Point `*.test.kulikov.uk` to your public IP with Cloudflare DNS only.

```bash
export KUBECONFIG="$HOME/.kube/home-k3s.yaml"
flux get kustomizations -A
kubectl get nodes
```

Open `https://demo.test.kulikov.uk`.
The kubeconfig file gives you cluster access. To fetch only that file, add `--fetch-kubeconfig` to the bootstrap command.
Do not replace the recovery key during a restore. Back up its password outside Git.
Git restores configuration, not databases or stored files. Back up that data separately.

## Add a service

Work in this repository on your Mac or Linux computer. Activate `.venv` and keep `$HOME/.local/bin` in `PATH`.
Use `myapp` as the example name:

1. Copy `apps/demo/` to `apps/myapp/`. Replace `demo` with `myapp` in the copied files.
   Set your image, ports, health probes, and storage. Remove Nginx-only files and mounts if they are not needed.
2. Copy `infrastructure/namespaces/demo.yaml` to `infrastructure/namespaces/myapp.yaml`. Set `metadata.name` to `myapp`.
   Run `python3 scripts/secretctl.py index` to update the file list.
3. Copy `clusters/home/apps.yaml` to `clusters/home/myapp.yaml`.
   Set `metadata.name: myapp` and `spec.path: ./apps/myapp`. Keep `namespace: flux-system` and the dependencies.
   Add `myapp.yaml` to `resources` in `clusters/home/kustomization.yaml`.
4. For HTTPS, set the IngressRoute host to `myapp.test.kulikov.uk`. Keep `tls: {}` for the shared certificate.
5. If the service needs a secret, use the command below. Set its `secretKeyRef` to the matching name and field.
   Otherwise, remove the copied `secretKeyRef` environment entry from the Deployment.

```bash
python3 scripts/secretctl.py add myapp myapp-config --prompt API_KEY
```

Use any namespace, secret name, and field. Use `set` instead of `add` to change an existing secret.
Never put a password or token in a plain file in Git.

```bash
kubectl kustomize apps/myapp >/dev/null
git add apps/myapp clusters/home infrastructure/namespaces secrets
bash scripts/check.sh
git diff --cached
git commit -m "Add myapp"
git push
```

If you use a pull request, merge it into the branch that Flux watches.
Flux applies the change automatically. For errors, run `flux logs --all-namespaces --level=error`.
Restart affected Pods after a Secret change if they read it through environment variables.
