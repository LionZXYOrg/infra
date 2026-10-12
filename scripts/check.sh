#!/usr/bin/env bash
# Generic validation: no provider-specific names, no decryption keys required.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
if [[ -f .venv/bin/activate ]]; then source .venv/bin/activate; fi
export PATH="$HOME/.local/bin:$PATH"
for script in scripts/*.sh; do bash -n "$script"; done
python3 - <<'PY'
import ast
from pathlib import Path
for p in Path('scripts').glob('*.py'):
    ast.parse(p.read_text(), filename=str(p))
PY
python3 scripts/secretctl.py check
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

roots = [Path(x) for x in ('bootstrap', 'clusters', 'secrets', 'infrastructure', 'apps')]
for root in roots:
    for p in root.rglob('*'):
        if p.suffix in ('.yaml', '.yml'):
            validate_yaml(p, p.read_bytes())
validate_yaml(Path('.sops.yaml'), Path('.sops.yaml').read_bytes())
# Check staged contents too: a clean working file does not make an unsafe index safe.
paths = subprocess.check_output(['git', 'diff', '--cached', '--name-only', '--diff-filter=ACMR', '-z'])
for item in paths.split(b'\0'):
    if not item:
        continue
    name = item.decode()
    p = Path(name)
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
  for dir in infrastructure/namespaces secrets infrastructure/controllers infrastructure/issuers infrastructure/tls apps/demo; do
    kubectl kustomize "$dir" >/dev/null
  done
  if [[ -d clusters/home/flux-system ]]; then
    kubectl kustomize clusters/home >/dev/null
  fi
else
  echo 'INFO: kubectl is absent; Kustomize builds are deferred.'
fi
git diff --check
git diff --cached --check
echo 'Checks passed. This is not a general-purpose secret scanner; review the diff.'
