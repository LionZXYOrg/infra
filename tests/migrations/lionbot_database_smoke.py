"""Boot the snapshot PostgreSQL image without networking or persistent source data."""
import os
from pathlib import Path
import secrets
import subprocess
import uuid
import yaml


def run(args, env=None):
    result = subprocess.run(args, env=env, capture_output=True, timeout=180)
    if result.returncode:
        raise RuntimeError('Isolated PostgreSQL startup check failed; raw logs suppressed')
    return result.stdout


def main():
    root = Path(__file__).resolve().parents[2]
    hr = yaml.safe_load((root / 'apps/lionbot/helmrelease-lionbot-db.yaml').read_text())
    container = hr['spec']['values']['controllers']['main']['containers']['main']
    image = container['image']
    reference = image['repository'] + ':' + image['tag'] + '@' + image['digest']
    name = 'infra-postgres-' + uuid.uuid4().hex[:12]
    environment = os.environ.copy()
    environment['POSTGRES_PASSWORD'] = secrets.token_urlsafe(32)
    args = ['docker', 'run', '-d', '--name', name, '--network', 'none',
            '--tmpfs', '/var/lib/postgresql/data:rw', '--security-opt', 'no-new-privileges',
            '-e', 'POSTGRES_PASSWORD', '-e', 'POSTGRES_USER=postgres', '-e', 'POSTGRES_DB=postgres',
            '-e', 'PGDATA=' + container['env']['PGDATA']]
    for capability in container['securityContext']['capabilities']['drop']:
        args += ['--cap-drop', capability]
    try:
        run(args + [reference], env=environment)
        run(['docker', 'exec', name, 'sh', '-c',
             'for i in 1 2 3 4 5 6 7 8 9 10; do pg_isready -U postgres -d postgres >/dev/null && exit 0; sleep 1; done; exit 1'])
        print('PASS: isolated snapshot PostgreSQL boot and readiness')
    finally:
        subprocess.run(['docker', 'rm', '-f', name], capture_output=True, timeout=30)
        environment.pop('POSTGRES_PASSWORD', None)


if __name__ == '__main__':
    main()
