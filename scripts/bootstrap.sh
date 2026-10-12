#!/usr/bin/env bash
# On Mac: bash scripts/bootstrap.sh --ssh ubuntu@192.168.1.50
# On the Linux server: bash scripts/bootstrap.sh
set -euo pipefail
set +x
umask 077
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"
[[ $(id -u) != 0 ]] || { echo 'Run without sudo.' >&2; exit 1; }
if [[ -f .venv/bin/activate ]]; then source .venv/bin/activate; fi
export PATH="$HOME/.local/bin:$PATH"
set -a
source bootstrap/settings.env
source bootstrap/versions.env
set +a
python3 -c 'import sys, yaml; assert sys.version_info >= (3, 10), "Python 3.10+ required"'
# Install the pinned CLIs in step 3; never silently upgrade an existing cluster.
for tool in git gh age age-keygen sops flux kubectl; do
  command -v "$tool" >/dev/null || { echo "Missing: $tool; complete tool installation." >&2; exit 1; }
done
exec python3 scripts/bootstrap.py "$@"
