# Home infrastructure

K3s runs the services. Flux applies changes from Git once a minute.
Traefik handles HTTPS. cert-manager renews the certificate for `*.test.kulikov.uk`.
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

Use `apps/demo/` as a template for the image, ports, probes, storage, and HTTPS
route. Add the service's namespace under `infrastructure/namespaces/` and a Flux
Kustomization under `clusters/home/`; include both in their resource lists.
Use `myapp.test.kulikov.uk` for the route and `tls: {}` for the shared certificate.

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

## Test changes

```bash
python3 -m unittest discover -s tests -v
bash scripts/check.sh
```

These checks work without private repository access. Every PR also runs a fresh
deployment with disposable secrets and local TLS in GitHub Actions. See the
[isolated deployment procedure](tests/isolated/README.md) to run it in a VM.

## Prepare the Raspberry Pi service migrations

Migration PRs use `LionZXY/rpi-infra` commit
`ccfc5ecd5433fa0d7fe3844f167612b8ed6e2297` as a provisional configuration snapshot.
It is about nine months old and does not prove which services are running today.
The prepared services are changedetection, ddns, immich, lionbot,
localcontentshare, obsidian (CouchDB LiveSync), psybot, qbittorrent,
replacevowelbot, speedtest, transfersh, and warden (Vaultwarden).
`SMS2Telegram` and `personal_birthday_reminder` are empty directories with no
runnable definition; revisit them with the final inventory. Excluded services are
pihole, n8n, litnetbot, imgur, samba, switchstatic, tabby, aiobashim, and
QBittorrentBot. qBittorrent itself remains in scope.

Each service PR contains an `apps/SERVICE/README.md` restore/cutover/rollback
procedure, namespace, suspended Flux Kustomization, suspended HelmRelease(s),
health checks, persistent storage where applicable, and a public test contract
under `tests/migrations/`. Helm archives are version and SHA-256 pinned. Available
container images are digest pinned; floating snapshot tags are provisional
candidates and must be matched to the final running image before cutover.
Custom image publication prerequisites are documented explicitly.

Install Helm for rendering with `bash scripts/install-tools.sh helm`.
Run `python3 scripts/migration_check.py --render` in addition to the normal checks.
Rendering checks real chart output for single writers, probes, credentials,
PVC mounts, service selectors/ports, and TLS route destinations. It does not
claim that the restored application works. PR `Deployment` checks also verify
that the prepared migrations stay suspended and create no application Pods in
the disposable cluster; they exercise the existing Flux/SOPS/HTTPS stack.
Service-specific restore and functional acceptance checks still run in an
isolated rehearsal after the final inventory arrives, before production activation.

For services with secrets, the matching private PR stores encrypted Secret
manifests in `infra-secrets`. The public PR's Gitlink points to its published
private commit. Flux follows private `main`, not that Gitlink. Merge the public
preparation first to create its namespace while the application stays suspended,
then merge the matching private PR. Resolve indexes by keeping all resources.
Never switch the production secrets source to a draft branch. Secret values from
this old snapshot must be verified or rotated against the final running service.
Credential-bearing application data/configuration must travel in encrypted
backups, not public ConfigMaps or Git.

Before activation, confirm the final service inventory, running image digests,
CPU architecture, DNS, LAN/public exposure, data size, ownership, backup/restore,
credential validity, and actual storage classes. The provisional `local-path`
PVCs are node-local and are not HA or backups. Choose the final storage backend
and capacity before provisioning. Each PVC disables Flux pruning; retain its PV
and encrypted backups for rollback. Use the staging `SERVICE.test.kulikov.uk`
host during rehearsals; the existing wildcard certificate does not cover legacy
`*.kulikov.uk` or `warden.lionzxy.ru`.

Keep both `spec.suspend` flags true until the data restore and acceptance checks
pass. For stateful services, activate the release in the isolated rehearsal only
against a restored copy. Telegram polling and DDNS writes require special care:
use test credentials during rehearsal and stop the old writer before production
activation. Enable a production service by reviewing a separate commit that
clears suspension on its HelmRelease(s) and Flux Kustomization after following
its README. Retain the old host and stopped containers through the rollback window.
