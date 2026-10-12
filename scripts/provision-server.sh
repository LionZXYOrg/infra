#!/usr/bin/env bash
# Linux ONLY. No GitHub credentials, SOPS identities or application secrets.
# Args: SERVER_IP NODE_NAME K3S_VERSION OUTPUT_KUBECONFIG
set -euo pipefail
set +x
umask 077
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ $(uname -s) == Linux ]] || fail 'Linux only.'
[[ $# == 4 ]] || fail 'Expected: SERVER_IP NODE_NAME K3S_VERSION OUTPUT_KUBECONFIG'
IP=$1; NODE=$2; VERSION=$3; OUT=$4
[[ $VERSION =~ ^v[0-9]+\.[0-9]+\.[0-9]+\+k3s[0-9]+$ ]] || fail 'Invalid K3s version.'
[[ $NODE =~ ^[a-z0-9][a-z0-9-]*$ && ${#NODE} -le 63 ]] || fail 'Invalid node name.'
[[ $OUT == /* && ! -e $OUT && ! -L $OUT ]] || fail 'Output must be a new absolute file path.'
if [[ $(id -u) -eq 0 ]]; then
  as_root() { "$@"; }
else
  command -v sudo >/dev/null || fail 'sudo is required for non-root installation.'
  sudo -v
  as_root() { sudo "$@"; }
fi
# The supported host is a clean Ubuntu/Debian system.
as_root apt-get update
as_root apt-get install -y curl ca-certificates python3 iproute2 util-linux
python3 - "$IP" <<'PYIP'
import ipaddress, sys
ipaddress.IPv4Address(sys.argv[1])
PYIP
ip -o -4 addr show | awk '{print $4}' | cut -d/ -f1 | grep -Fxq "$IP" \
  || fail 'SERVER_IP is not assigned to this host. Check DHCP.'
[[ $(stat -fc %T /sys/fs/cgroup) == cgroup2fs ]] || fail 'cgroup v2 is required.'
grep -qw memory /sys/fs/cgroup/cgroup.controllers || fail 'Memory cgroup is missing.'
[[ -z $(swapon --noheadings --show=NAME) ]] || fail 'Disable server swap/zram persistently first.'
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
cat > "$work/config.yaml" <<CONFIG
node-name: ${NODE}
node-ip: "${IP}"
tls-san:
  - "${IP}"
write-kubeconfig-mode: "0600"
secrets-encryption: true
disable:
  - traefik
CONFIG
if command -v k3s >/dev/null 2>&1; then
  [[ $(k3s --version | head -n1 | awk '{print $3}') == "$VERSION" ]] \
    || fail 'Existing K3s version differs. This is not an upgrade script.'
  as_root cmp -s /etc/rancher/k3s/config.yaml "$work/config.yaml" \
    || fail 'Existing K3s config differs; it will not be overwritten.'
  as_root systemctl is-active --quiet k3s || fail 'Existing k3s service is not active.'
else
  [[ -z $(as_root ss -H -ltn '( sport = :80 or sport = :443 or sport = :6443 )') ]] \
    || fail 'Ports 80/443/6443 are already in use.'
  if as_root test -e /etc/rancher/k3s/config.yaml; then
    as_root cmp -s /etc/rancher/k3s/config.yaml "$work/config.yaml" || fail 'Unknown existing K3s config.'
  fi
  as_root install -d -m 0755 /etc/rancher/k3s
  as_root install -m 0600 "$work/config.yaml" /etc/rancher/k3s/config.yaml
  curl --fail --location --retry 3 --proto '=https' --tlsv1.2 \
    "https://raw.githubusercontent.com/k3s-io/k3s/${VERSION}/install.sh" \
    -o "$work/install-k3s.sh"
  as_root env INSTALL_K3S_VERSION="$VERSION" sh "$work/install-k3s.sh" server
fi
ready=false
for ((attempt=0; attempt<90; attempt++)); do
  if as_root k3s kubectl get node "$NODE" >/dev/null 2>&1; then ready=true; break; fi
  sleep 2
done
[[ $ready == true ]] || fail 'Node did not register. Check journalctl -u k3s.'
as_root k3s kubectl wait --for=condition=Ready "node/$NODE" --timeout=180s
as_root install -m 0600 -o "$(id -u)" -g "$(id -g)" /etc/rancher/k3s/k3s.yaml "$OUT"
printf '\nK3s is ready; a private kubeconfig copy is prepared.\n'
