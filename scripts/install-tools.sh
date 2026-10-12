#!/usr/bin/env bash
# macOS / Linux; Bash 3.2+; installs pinned CLIs without sudo.
set -euo pipefail
set +x
umask 077
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
source "$ROOT/bootstrap/versions.env"
if [[ $(uname -s) == Darwin && $(id -u) -eq 0 ]]; then
  echo 'On macOS run as a normal user; root is supported on Linux.' >&2
  exit 1
fi
case "$(uname -s)" in Darwin) os=darwin ;; Linux) os=linux ;; *) exit 1 ;; esac
case "$(uname -m)" in x86_64) arch=amd64 ;; aarch64|arm64) arch=arm64 ;; *) exit 1 ;; esac
for tool in curl tar python3; do command -v "$tool" >/dev/null; done
DEST="$HOME/.local/bin"
mkdir -p "$DEST"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
fetch() {
  curl --fail --location --retry 3 --proto '=https' --tlsv1.2 "$1" -o "$2"
}
verify() {
  python3 - "$1" "$2" <<'PYSHA'
import hashlib, re, sys
from pathlib import Path
p, sums = map(Path, sys.argv[1:])
matches = []
for line in sums.read_text().splitlines():
    fields = line.split()
    if len(fields) == 1 or (len(fields) == 2 and fields[1].lstrip('*') == p.name):
        matches.append(fields[0])
if len(matches) != 1 or not re.fullmatch(r'[0-9a-fA-F]{64}', matches[0]):
    raise SystemExit('Missing or ambiguous SHA-256 checksum.')
h = hashlib.sha256()
with p.open('rb') as stream:
    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
        h.update(chunk)
if h.hexdigest() != matches[0].lower():
    raise SystemExit('SHA-256 verification failed.')
print(p.name + ': SHA-256 OK')
PYSHA
}
[[ $# -gt 0 ]] || set -- sops flux kubectl
for tool in "$@"; do
  dir="$work/$tool"
  mkdir -p "$dir"
  case "$tool" in
    sops)
      name="sops-${SOPS_VERSION}.${os}.${arch}"
      base="https://github.com/getsops/sops/releases/download/${SOPS_VERSION}"
      fetch "$base/$name" "$dir/$name"
      fetch "$base/sops-${SOPS_VERSION}.checksums.txt" "$dir/checksums.txt"
      verify "$dir/$name" "$dir/checksums.txt"
      binary="$dir/$name"
      ;;
    flux)
      version=${FLUX_VERSION#v}
      name="flux_${version}_${os}_${arch}.tar.gz"
      base="https://github.com/fluxcd/flux2/releases/download/${FLUX_VERSION}"
      fetch "$base/$name" "$dir/$name"
      fetch "$base/flux_${version}_checksums.txt" "$dir/checksums.txt"
      verify "$dir/$name" "$dir/checksums.txt"
      tar -xzf "$dir/$name" -C "$dir" flux
      binary="$dir/flux"
      ;;
    kubectl)
      # Match the Kubernetes version embedded in the pinned K3s release.
      version=${K3S_VERSION%%+*}
      base="https://dl.k8s.io/release/${version}/bin/${os}/${arch}"
      fetch "$base/kubectl" "$dir/kubectl"
      fetch "$base/kubectl.sha256" "$dir/checksums.txt"
      verify "$dir/kubectl" "$dir/checksums.txt"
      binary="$dir/kubectl"
      ;;
    gitleaks)
      version=${GITLEAKS_VERSION#v}
      release_arch=$arch
      [[ $arch != amd64 ]] || release_arch=x64
      name="gitleaks_${version}_${os}_${release_arch}.tar.gz"
      base="https://github.com/gitleaks/gitleaks/releases/download/${GITLEAKS_VERSION}"
      fetch "$base/$name" "$dir/$name"
      fetch "$base/gitleaks_${version}_checksums.txt" "$dir/checksums.txt"
      verify "$dir/$name" "$dir/checksums.txt"
      tar -xzf "$dir/$name" -C "$dir" gitleaks
      binary="$dir/gitleaks"
      ;;
    helm)
      name="helm-${HELM_VERSION}-${os}-${arch}.tar.gz"
      base="https://get.helm.sh"
      fetch "$base/$name" "$dir/$name"
      fetch "$base/$name.sha256sum" "$dir/checksums.txt"
      verify "$dir/$name" "$dir/checksums.txt"
      tar -xzf "$dir/$name" -C "$dir" "$os-$arch/helm"
      binary="$dir/$os-$arch/helm"
      ;;
    *) echo "Unknown tool: $tool" >&2; exit 1 ;;
  esac
  install -m 0755 "$binary" "$DEST/$tool"
  if [[ $tool == kubectl ]]; then
    "$DEST/$tool" version --client
  elif [[ $tool == gitleaks || $tool == helm ]]; then
    "$DEST/$tool" version
  else
    "$DEST/$tool" --version
  fi
done
printf '\nFor this shell: export PATH="$HOME/.local/bin:$PATH"\n'
