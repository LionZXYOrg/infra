"""Fresh Flux deployment with local Git, SOPS, and certificate-verified HTTPS."""
import argparse
import base64
import ipaddress
import json
from pathlib import Path
import re
import signal
import sys
import tempfile

import yaml

if __package__:
    from .fixtures import FixtureSet, command
    from .git_server import GitServer
else:
    from fixtures import FixtureSet, command
    from git_server import GitServer


KUSTOMIZATIONS = ('flux-system', 'infra-namespaces', 'cluster-secrets',
                  'infra-controllers', 'infra-tls', 'apps')


def safe_resource_status(objects: list[dict]) -> list[dict]:
    return [{'kind': obj.get('kind'), 'name': obj.get('metadata', {}).get('name'),
             'namespace': obj.get('metadata', {}).get('namespace'),
             'conditions': [{key: condition.get(key) for key in ('type', 'status', 'reason')}
                            for condition in obj.get('status', {}).get('conditions', [])]}
            for obj in objects]


class Deployment:
    def __init__(self, kubeconfig: Path, node_ip: str, report_dir: Path):
        if not kubeconfig.is_absolute():
            raise ValueError('Use an absolute disposable kubeconfig path')
        ipaddress.IPv4Address(node_ip)
        self.kubeconfig = kubeconfig
        self.node_ip = node_ip
        self.report_dir = report_dir
        self.passed = []

    def kube(self, args: list[str], *, data: bytes | None = None) -> bytes:
        return command(['kubectl', '--kubeconfig', str(self.kubeconfig), *args],
                       data=data, timeout=1000)

    def flux(self, args: list[str]) -> bytes:
        return command(['flux', '--kubeconfig', str(self.kubeconfig), *args], timeout=360)

    def wait(self, resource, name, namespace, timeout='300s'):
        self.kube(['-n', namespace, 'wait', f'{resource}/{name}', '--for=condition=Ready',
                   f'--timeout={timeout}'])

    def verify_fresh_cluster(self):
        nodes = json.loads(self.kube(['get', 'nodes', '-o', 'json']))['items']
        if len(nodes) != 1 or nodes[0]['metadata']['name'] != 'ci-infra':
            raise RuntimeError('Refusing a cluster that is not the disposable ci-infra node')
        if self.kube(['get', 'namespace', 'flux-system', '--ignore-not-found', '-o', 'name']).strip():
            raise RuntimeError('Refusing an existing Flux installation')

    def verify_probe(self, expected: bytes):
        obj = json.loads(self.kube(['-n', 'demo', 'get', 'secret', 'isolated-probe', '-o', 'json']))
        try:
            actual = base64.b64decode(obj['data']['probe'], validate=True)
        except (KeyError, ValueError):
            raise RuntimeError('Encrypted probe missing or invalid; contents suppressed') from None
        if actual != expected:
            raise RuntimeError('Encrypted probe value mismatch; contents suppressed')

    def verify_https(self):
        obj = json.loads(self.kube(['-n', 'traefik', 'get', 'secret', 'isolated-tls', '-o', 'json']))
        with tempfile.TemporaryDirectory() as work:
            ca = Path(work) / 'ca.crt'
            ca.write_bytes(base64.b64decode(obj['data']['tls.crt'], validate=True))
            ca.chmod(0o600)
            body = command(['curl', '--fail', '--silent', '--show-error', '--noproxy', '*',
                            '--retry', '5', '--retry-connrefused', '--retry-delay', '2',
                            '--connect-timeout', '10', '--max-time', '30', '--cacert', str(ca),
                            '--resolve', f'demo.infra.test:443:{self.node_ip}', 'https://demo.infra.test/'],
                           timeout=200)
        if b'K3s + Flux + GitHub works.' not in body:
            raise RuntimeError('HTTPS returned an unexpected demo page')

    def verify_no_production_resources(self):
        issuers = json.loads(self.kube(['get', 'clusterissuers,issuers', '-A', '-o', 'json']))['items']
        if any('acme' in obj.get('spec', {}) for obj in issuers):
            raise RuntimeError('Unexpected production ACME issuer in isolated deployment')
        certificates = json.loads(self.kube(['get', 'certificates', '-A', '-o', 'json']))['items']
        if any((obj['metadata']['namespace'], obj['metadata']['name']) != ('traefik', 'isolated-tls')
               for obj in certificates):
            raise RuntimeError('Unexpected production certificate in isolated deployment')
        names = self.kube(['get', 'secrets', '-A', '-o',
                          'jsonpath={range .items[*]}{.metadata.namespace}{"/"}{.metadata.name}{"\\n"}{end}'])
        if b'cloudflare-api-token' in names:
            raise RuntimeError('Unexpected production Cloudflare Secret in isolated deployment')

    def verify_ready(self):
        for name in ('flux-system', 'infra-secrets'):
            self.wait('gitrepository', name, 'flux-system')
        for name in KUSTOMIZATIONS:
            self.wait('kustomization', name, 'flux-system', '900s' if name == 'infra-controllers' else '300s')
        for namespace, name in (('traefik', 'traefik'), ('cert-manager', 'cert-manager')):
            self.wait('helmrelease', name, namespace)
        self.wait('certificate', 'isolated-tls', 'traefik')
        self.kube(['-n', 'demo', 'rollout', 'status', 'deployment/demo', '--timeout=180s'])

    def verify_migrations_suspended(self, fixture):
        for contract in sorted((fixture.infra_path / 'tests/migrations').glob('*.yaml')):
            name = contract.stem
            namespace = yaml.safe_load(contract.read_text()).get('namespace', name)
            obj = json.loads(self.kube(['-n', 'flux-system', 'get', 'kustomization', name, '-o', 'json']))
            if obj['spec'].get('suspend') is not True:
                raise RuntimeError('Prepared migration became active')
            pods = json.loads(self.kube(['-n', namespace, 'get', 'pods', '-o', 'json']))['items']
            releases = json.loads(self.kube(['-n', namespace, 'get', 'helmreleases', '-o', 'json']))['items']
            if pods or releases:
                raise RuntimeError('Prepared migration started application resources')

    def passed_check(self, name):
        self.passed.append(name)
        print(f'PASS: {name}', flush=True)

    def run(self, fixture: FixtureSet) -> dict:
        self.verify_fresh_cluster()
        version = tool_versions(Path(__file__).resolve().parents[2])['FLUX_VERSION']
        components = command(['flux', 'install', f'--version={version}', '--export'], timeout=180)
        self.kube(['apply', '--server-side', '-f', '-'], data=components)
        for name in ('source-controller', 'kustomize-controller', 'helm-controller', 'notification-controller'):
            self.kube(['-n', 'flux-system', 'rollout', 'status', f'deployment/{name}', '--timeout=300s'])
        self.passed_check('flux-controllers')
        self.kube(['apply', '--server-side', '-f', '-'],
                  data=yaml.safe_dump_all(fixture.bootstrap_objects()).encode())
        # The root creates the remaining sources and Kustomizations asynchronously.
        self.wait('kustomization', 'flux-system', 'flux-system')
        self.verify_ready()
        self.passed_check('sources-helm-kustomizations-certificate-demo-ready')
        self.verify_probe(b'isolated-value-one')
        self.passed_check('sops-initial-decryption')
        self.verify_https()
        self.passed_check('trusted-https')
        self.verify_no_production_resources()
        self.passed_check('production-resources-absent')
        self.verify_migrations_suspended(fixture)
        self.passed_check('migration-preparations-suspended-without-workloads')
        fixture.update_probe(b'isolated-value-two')
        self.flux(['reconcile', 'kustomization', 'cluster-secrets', '--with-source', '--timeout=5m'])
        self.verify_probe(b'isolated-value-two')
        self.passed_check('sops-update-decryption')
        self.flux(['reconcile', 'kustomization', 'flux-system', '--with-source', '--timeout=5m'])
        self.verify_ready()
        self.verify_https()
        self.verify_no_production_resources()
        self.verify_migrations_suspended(fixture)
        self.passed_check('repeat-reconciliation')
        return {'passed': list(self.passed), 'infra_fixture_revision': fixture.infra_revision,
                'secrets_fixture_revision': fixture.secrets_revision}

    def diagnostics(self):
        records = []
        for resource in ('nodes', 'deployments', 'gitrepositories', 'kustomizations', 'helmreleases', 'certificates'):
            try:
                raw = self.kube(['get', resource, '-A', '-o', 'json', '--request-timeout=10s'])
                records.extend(safe_resource_status(json.loads(raw)['items']))
            except (RuntimeError, ValueError, KeyError):
                continue
        return records


def tool_versions(source: Path) -> dict:
    versions = {}
    for line in (source / 'bootstrap/versions.env').read_text().splitlines():
        match = re.fullmatch(r"([A-Z0-9_]+)='([^']+)'", line)
        if match and match[1] in {'K3S_VERSION', 'FLUX_VERSION', 'SOPS_VERSION'}:
            versions[match[1]] = match[2]
    return versions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'kubeconfig', 'node-ip', 'report-dir'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    source = Path(args.source).resolve()
    report_dir = Path(args.report_dir).resolve()
    report_dir.mkdir(parents=True, exist_ok=True)
    runner = Deployment(Path(args.kubeconfig), args.node_ip, report_dir)
    report = {'success': False, 'tested_revision': command(['git', 'rev-parse', 'HEAD'], cwd=source).decode().strip(),
              'versions': tool_versions(source)}
    # SystemExit unwinds context managers and removes server, fixture keys, and repositories.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    try:
        with tempfile.TemporaryDirectory(prefix='infra-ci-') as work, GitServer({}, host=args.node_ip) as server:
            fixture = FixtureSet.create(source, Path(work), server.base_url)
            server.repositories.update(fixture.repositories)
            report.update(runner.run(fixture))
            report['success'] = True
    finally:
        report['passed'] = list(runner.passed)
        if not report['success']:
            report['resources'] = runner.diagnostics()
        (report_dir / 'deployment.json').write_text(json.dumps(report, indent=2) + '\n')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (RuntimeError, ValueError, KeyError):
        print('Deployment failed; see the allowlisted readiness report. Command output suppressed.', file=sys.stderr)
        sys.exit(1)
