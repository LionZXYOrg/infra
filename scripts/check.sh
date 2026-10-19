#!/usr/bin/env bash
# Generic validation: no provider-specific names, no decryption keys required.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
if [[ -f .venv/bin/activate ]]; then source .venv/bin/activate; fi
export PATH="$HOME/.local/bin:$PATH"
for script in scripts/*.sh tests/ci/*.sh; do
  [[ ! -f $script ]] || bash -n "$script"
done
python3 - <<'PY'
import ast
from pathlib import Path
for p in list(Path('scripts').glob('*.py')) + list(Path('tests/ci').glob('*.py')):
    ast.parse(p.read_text(), filename=str(p))
PY
python3 scripts/secretctl.py check --public
if [[ -f secrets/.git ]]; then python3 scripts/secretctl.py check; fi
python3 - <<'PYCHECK'
from pathlib import Path
import re
import subprocess
import yaml


def validate_yaml(path, raw, staged=False):
    try:
        docs = list(yaml.safe_load_all(raw))
    except yaml.YAMLError:
        raise SystemExit(f'Invalid YAML: {path}; contents suppressed.')
    for d in docs:
        if not isinstance(d, dict):
            continue
        if d.get('kind') == 'Secret':
            if not path.as_posix().startswith('secrets/') or not path.name.endswith('.sops.yaml'):
                raise SystemExit(f'Secret outside the central encrypted layer: {path}')
            if not d.get('sops') or not d.get('data') or 'stringData' in d:
                raise SystemExit(f'Invalid encrypted Secret: {path}')
            if any(not isinstance(v, str) or not v.startswith('ENC[AES256_GCM,')
                   for v in d['data'].values()):
                raise SystemExit(f'Unencrypted Secret data: {path}')
        if not staged and path.name == 'kustomization.yaml':
            for rel in d.get('resources', []):
                if path == Path('clusters/home/kustomization.yaml') and rel == 'flux-system':
                    continue  # Created by flux bootstrap.
                if not (path.parent / rel).exists():
                    raise SystemExit(f'Missing local resource in {path}: {rel}')

roots = [Path(x) for x in ('bootstrap', 'clusters', 'infrastructure', 'apps', 'tests/isolated')]
for root in roots:
    for p in root.rglob('*'):
        if p.suffix in ('.yaml', '.yml'):
            validate_yaml(p, p.read_bytes())
# Check staged contents too: a clean working file does not make an unsafe index safe.
paths = subprocess.check_output(['git', 'diff', '--cached', '--name-only', '--diff-filter=ACMR', '-z'])
for item in paths.split(b'\0'):
    if not item:
        continue
    name = item.decode()
    p = Path(name)
    if name in {'.sops.yaml', 'bootstrap/sops-age.age'} or name.startswith('secrets/'):
        raise SystemExit(f'Private module content staged in public repository: {name}')
    entry = subprocess.check_output(['git', 'ls-files', '--stage', '--', name])
    if entry.startswith(b'160000 '):
        if name != 'secrets':
            raise SystemExit(f'Unexpected submodule: {name}')
        continue
    if p.suffix in ('.agekey', '.key', '.pem', '.p12') or p.name.startswith('.env'):
        raise SystemExit(f'Possible plaintext credential file staged: {name}')
    raw = subprocess.check_output(['git', 'show', ':' + name])
    if re.search(rb'^\s*(AGE-SECRET-KEY-1|-----BEGIN (?:OPENSSH |RSA |EC )?PRIVATE KEY-----)', raw, re.M):
        raise SystemExit(f'Private identity detected in staged file: {name}')
    if p.suffix in ('.yaml', '.yml'):
        validate_yaml(p, raw, staged=True)
print('Local YAML references and staged Secret manifests checked.')
PYCHECK
if command -v kubectl >/dev/null 2>&1; then
  for dir in infrastructure/namespaces infrastructure/controllers infrastructure/issuers infrastructure/tls infrastructure/mcp infrastructure/mcp-endpoint infrastructure/monitoring/stack infrastructure/monitoring/config apps/*; do
    [[ -f $dir/kustomization.yaml ]] || continue
    kubectl kustomize "$dir" >/dev/null
  done
  if [[ -f secrets/.git ]]; then kubectl kustomize secrets >/dev/null; fi
  for dir in tests/isolated tests/isolated/tls tests/isolated/demo; do
    [[ ! -f $dir/kustomization.yaml ]] || kubectl kustomize "$dir" >/dev/null
  done
  # Flux creates this Kustomization during bootstrap. An empty or partial
  # flux-system directory is not enough to build the cluster root yet.
  if [[ -f clusters/home/flux-system/kustomization.yaml ]]; then
    kubectl kustomize clusters/home >/dev/null
  else
    echo 'INFO: Flux bootstrap manifests are not ready; skipping cluster root Kustomize build.'
  fi
else
  echo 'INFO: kubectl is absent; Kustomize builds are deferred.'
fi
python3 scripts/migration_check.py
git diff --check
git diff --cached --check
echo 'Checks passed. This is not a general-purpose secret scanner; review the diff.'
