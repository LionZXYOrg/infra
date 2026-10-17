"""Boot the pinned standalone image without outbound networking or production data."""
from pathlib import Path
import subprocess
import uuid
import yaml


def command(args):
    result = subprocess.run(args, capture_output=True, timeout=180)
    if result.returncode:
        raise RuntimeError('Isolated LibreSpeed command failed; raw logs suppressed')
    return result.stdout


def main():
    root = Path(__file__).resolve().parents[2]
    release = yaml.safe_load((root / 'apps/speedtest/helmrelease-speedtest.yaml').read_text())
    container = release['spec']['values']['controllers']['main']['containers']['main']
    image = container['image']
    reference = image['repository'] + ':' + image['tag'] + '@' + image['digest']
    name = 'infra-speedtest-' + uuid.uuid4().hex[:12]
    args = ['docker', 'run', '-d', '--name', name, '--network', 'none', '-e', 'MODE=standalone',
            '--security-opt', 'no-new-privileges']
    for capability in container['securityContext']['capabilities']['drop']:
        args += ['--cap-drop', capability]
    try:
        command(args + [reference])
        code = command(['docker', 'exec', name, 'curl', '--retry', '10', '--retry-all-errors',
                        '--retry-delay', '1', '--max-time', '5', '-sS', '--fail', '-o', '/dev/null',
                        '-w', '%{http_code}', 'http://127.0.0.1:8080/'])
        if code != b'200':
            raise RuntimeError('Standalone LibreSpeed did not return HTTP 200')
        print('PASS: isolated standalone LibreSpeed HTTP 200')
    finally:
        subprocess.run(['docker', 'rm', '-f', name], capture_output=True, timeout=30)


if __name__ == '__main__':
    main()
