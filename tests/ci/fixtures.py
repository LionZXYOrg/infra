"""Generate local Git/SOPS fixtures exclusively from the public checkout."""
import base64
from dataclasses import dataclass
import os
from pathlib import Path, PurePosixPath
import secrets
import subprocess
import yaml


def clean_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('SOPS_', 'AGE_', 'GIT_'))
           and k not in {'GH_TOKEN', 'GITHUB_TOKEN', 'SSH_AUTH_SOCK'}}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1', GIT_TERMINAL_PROMPT='0')
    return env


def command(args, *, data=None, cwd=None, env=None, timeout=60) -> bytes:
    try:
        result = subprocess.run(args, input=data, cwd=cwd, env=env or clean_env(),
                                capture_output=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        raise RuntimeError(f'{Path(args[0]).name} unavailable or timed out; output suppressed') from None
    if result.returncode:
        raise RuntimeError(f'{Path(args[0]).name} failed ({result.returncode}); output suppressed')
    return result.stdout


def export_public(source: Path, target: Path):
    entries = command(['git', 'ls-files', '--stage', '-z'], cwd=source).split(b'\0')
    for entry in entries:
        if not entry:
            continue
        metadata, name_bytes = entry.split(b'\t', 1)
        mode, _, stage = metadata.decode().split()
        name = name_bytes.decode()
        path = PurePosixPath(name)
        if mode == '160000':
            if name != 'secrets':
                raise ValueError('Unexpected private Gitlink in public export')
            continue
        if name == '.gitmodules':
            continue
        if path.is_absolute() or '..' in path.parts or name in {'.sops.yaml', 'bootstrap/sops-age.age'} \
                or path.parts[0] == 'secrets':
            raise ValueError('Refusing private or unsafe path in public export')
        if mode not in {'100644', '100755'} or stage != '0':
            raise ValueError('Refusing symlink or unresolved index in public export')
        output = target / name
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(command(['git', 'show', ':' + name], cwd=source))
        output.chmod(0o755 if mode == '100755' else 0o644)


def commit(repo: Path, message: str) -> str:
    command(['git', 'add', '.'], cwd=repo)
    command(['git', '-c', 'user.name=Infra CI', '-c', 'user.email=ci@example.invalid',
             'commit', '-m', message], cwd=repo)
    return command(['git', 'rev-parse', 'HEAD'], cwd=repo).decode().strip()


@dataclass
class FixtureSet:
    work: Path
    infra_path: Path
    secrets_path: Path
    probe_path: Path
    config_path: Path
    identity: bytes
    repositories: dict[str, tuple[Path, str, str]]
    infra_url: str
    secrets_url: str
    infra_revision: str = ''
    secrets_revision: str = ''

    @classmethod
    def create(cls, source: Path, work: Path, base_url: str):
        work.chmod(0o700)
        infra, private = work / 'infra', work / 'secrets'
        # Validate the complete export before generating keys or fixture Git history.
        infra.mkdir()
        export_public(source, infra)
        private.mkdir()
        for repo in (infra, private):
            command(['git', 'init', '--initial-branch=main', str(repo)])
        identity = command(['age-keygen'])
        recipient = command(['age-keygen', '-y'], data=identity).decode().strip()
        config = work / 'fixture-sops.yaml'
        config.write_text(yaml.safe_dump({'creation_rules': [{
            'path_regex': r'.*\.sops\.yaml$', 'encrypted_regex': '^data$', 'age': recipient}]}))
        config.chmod(0o600)
        repositories = {name + '.git': (work / (name + '.git'), name, secrets.token_urlsafe(32))
                        for name in ('infra', 'secrets')}
        fixture = cls(work, infra, private, private / 'probe.sops.yaml', config, identity,
                      repositories, base_url + '/infra.git', base_url + '/secrets.git')
        source_path = infra / 'tests/isolated/secrets-source.yaml'
        obj = yaml.safe_load(source_path.read_text())
        obj['spec'].update(url=fixture.secrets_url, ref={'branch': 'main'}, recurseSubmodules=False)
        source_path.write_text(yaml.safe_dump(obj, sort_keys=False))
        # Test the preparation locks through Flux without starting real applications.
        isolated_root = infra / 'tests/isolated/kustomization.yaml'
        isolated = yaml.safe_load(isolated_root.read_text())
        for contract in sorted((infra / 'tests/migrations').glob('*.yaml')):
            name = contract.stem
            migration = yaml.safe_load((infra / 'clusters/home' / f'{name}.yaml').read_text())
            if migration['spec'].get('suspend') is not True:
                raise ValueError('Refusing an active migration in disposable preparation tests')
            destination = f'migration-{name}.yaml'
            (isolated_root.parent / destination).write_text(yaml.safe_dump(migration, sort_keys=False))
            isolated['resources'].append(destination)
        isolated_root.write_text(yaml.safe_dump(isolated, sort_keys=False))
        (private / 'kustomization.yaml').write_text(yaml.safe_dump({
            'apiVersion': 'kustomize.config.k8s.io/v1beta1', 'kind': 'Kustomization',
            'resources': ['probe.sops.yaml']}))
        fixture.write_probe(b'isolated-value-one')
        fixture.infra_revision = commit(infra, 'Public infrastructure CI fixture')
        fixture.secrets_revision = commit(private, 'Disposable encrypted CI probe')
        for name, repo in (('infra', infra), ('secrets', private)):
            bare = repositories[name + '.git'][0]
            command(['git', 'clone', '--bare', str(repo), str(bare)])
            command(['git', '--git-dir', str(bare), 'config', 'http.receivepack', 'false'])
        return fixture

    def write_probe(self, value: bytes):
        obj = {'apiVersion': 'v1', 'kind': 'Secret', 'type': 'Opaque',
               'metadata': {'name': 'isolated-probe', 'namespace': 'demo'},
               'data': {'probe': base64.b64encode(value).decode()}}
        encrypted = command(['sops', '--config', str(self.config_path), '--encrypt',
                             '--filename-override', str(self.probe_path),
                             '--input-type', 'yaml', '--output-type', 'yaml', '/dev/stdin'],
                            data=yaml.safe_dump(obj).encode())
        self.probe_path.write_bytes(encrypted)
        self.probe_path.chmod(0o600)

    def update_probe(self, value: bytes) -> str:
        self.write_probe(value)
        self.secrets_revision = commit(self.secrets_path, 'Update disposable encrypted CI probe')
        command(['git', 'push', str(self.repositories['secrets.git'][0]), 'HEAD:main'], cwd=self.secrets_path)
        return self.secrets_revision

    def bootstrap_objects(self) -> list[dict]:
        objects = []
        for alias, name in (('infra.git', 'flux-system'), ('secrets.git', 'infra-secrets')):
            _, username, password = self.repositories[alias]
            objects.append({'apiVersion': 'v1', 'kind': 'Secret', 'type': 'Opaque',
                            'metadata': {'name': name, 'namespace': 'flux-system'},
                            'stringData': {'username': username, 'password': password}})
        objects.append({'apiVersion': 'v1', 'kind': 'Secret', 'type': 'Opaque',
                        'metadata': {'name': 'sops-age', 'namespace': 'flux-system'},
                        'data': {'identity.agekey': base64.b64encode(self.identity).decode()}})
        objects.append({'apiVersion': 'source.toolkit.fluxcd.io/v1', 'kind': 'GitRepository',
                        'metadata': {'name': 'flux-system', 'namespace': 'flux-system'},
                        'spec': {'interval': '30s', 'url': self.infra_url, 'ref': {'branch': 'main'},
                                 'secretRef': {'name': 'flux-system'}, 'recurseSubmodules': False}})
        objects.append({'apiVersion': 'kustomize.toolkit.fluxcd.io/v1', 'kind': 'Kustomization',
                        'metadata': {'name': 'flux-system', 'namespace': 'flux-system'},
                        'spec': {'interval': '30s', 'timeout': '5m', 'path': './tests/isolated', 'prune': True,
                                 'sourceRef': {'kind': 'GitRepository', 'name': 'flux-system'}}})
        return objects
