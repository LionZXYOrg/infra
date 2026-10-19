"""Public-only exports and real disposable SOPS round trips."""
import base64
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import yaml

from ci.fixtures import FixtureSet, clean_env


@unittest.skipUnless(shutil.which('sops') and shutil.which('age-keygen'), 'SOPS/age required')
class FixtureTests(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.directory)
        self.source = self.root / 'source'
        self.source.mkdir()
        self.git('init', '--initial-branch=main')
        template = self.source / 'tests/isolated/secrets-source.yaml'
        template.parent.mkdir(parents=True)
        repository = Path(__file__).resolve().parents[1]
        template.write_bytes((repository / 'tests/isolated/secrets-source.yaml').read_bytes())
        (template.parent / 'kustomization.yaml').write_text(yaml.safe_dump({
            'apiVersion': 'kustomize.config.k8s.io/v1beta1', 'kind': 'Kustomization',
            'resources': ['secrets-source.yaml']}))
        (self.source / 'public.txt').write_text('public')
        self.git('add', '.')
        self.git('-c', 'user.name=CI test', '-c', 'user.email=ci@example.invalid',
                 'commit', '-m', 'public fixture')
        revision = self.git('rev-parse', 'HEAD').decode().strip()
        self.git('update-index', '--add', '--cacheinfo', '160000', revision, 'secrets')
        (self.source / 'secrets').mkdir()
        (self.source / 'secrets/private-sentinel').write_text('do-not-copy')
        self.work = self.root / 'work'
        self.work.mkdir(mode=0o700)

    def git(self, *args):
        result = subprocess.run(['git', *args], cwd=self.source, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, 'fixture Git command failed')
        return result.stdout

    def fixture(self):
        return FixtureSet.create(self.source, self.work, 'http://127.0.0.1:8000')

    def decrypt(self, fixture):
        env = clean_env()
        env['SOPS_AGE_KEY'] = fixture.identity.decode()
        result = subprocess.run(['sops', '--decrypt', str(fixture.probe_path)],
                                env=env, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, 'fixture SOPS decryption failed')
        return yaml.safe_load(result.stdout)

    def test_public_export_excludes_private_module_and_untracked_files(self):
        fixture = self.fixture()
        self.assertFalse((fixture.infra_path / 'secrets').exists())
        self.assertFalse((fixture.infra_path / '.gitmodules').exists())
        self.assertEqual((fixture.infra_path / 'public.txt').read_text(), 'public')
        self.assertNotIn('private-sentinel', subprocess.check_output(
            ['git', 'ls-tree', '-r', '--name-only', 'HEAD'], cwd=fixture.infra_path).decode())

    def test_probe_decrypts_and_update_is_committed_to_served_repository(self):
        with patch.dict(os.environ, {'SOPS_AGE_KEY': 'invalid-ambient-key',
                                     'SOPS_AGE_KEY_FILE': '/missing-ambient-key'}):
            fixture = self.fixture()
        obj = self.decrypt(fixture)
        self.assertEqual(obj['metadata'], {'name': 'isolated-probe', 'namespace': 'demo'})
        self.assertEqual(base64.b64decode(obj['data']['probe']), b'isolated-value-one')
        self.assertNotIn(b'isolated-value-one', fixture.probe_path.read_bytes())
        old = fixture.secrets_revision
        new = fixture.update_probe(b'isolated-value-two')
        self.assertNotEqual(old, new)
        self.assertEqual(base64.b64decode(self.decrypt(fixture)['data']['probe']), b'isolated-value-two')
        self.assertEqual(subprocess.check_output(['git', '--git-dir',
            str(fixture.repositories['secrets.git'][0]), 'rev-parse', 'main']).decode().strip(), new)

    def test_bootstrap_sources_and_credentials_are_independent(self):
        fixture = self.fixture()
        objects = fixture.bootstrap_objects()
        auth = {obj['metadata']['name']: obj for obj in objects if obj['kind'] == 'Secret'}
        self.assertNotEqual(auth['flux-system']['stringData'], auth['infra-secrets']['stringData'])
        source = next(obj for obj in objects if obj['kind'] == 'GitRepository')
        self.assertEqual(source['spec']['url'], fixture.infra_url)
        self.assertIs(source['spec']['recurseSubmodules'], False)
        private = yaml.safe_load((fixture.infra_path / 'tests/isolated/secrets-source.yaml').read_text())
        self.assertEqual(private['spec']['url'], fixture.secrets_url)
        self.assertIs(private['spec']['recurseSubmodules'], False)
        self.assertEqual(private['spec']['secretRef']['name'], 'infra-secrets')
        self.assertEqual(private['spec']['ref']['branch'], 'main')

    def test_export_rejects_tracked_private_files_and_symlinks(self):
        for name, symlink in (('.sops.yaml', False), ('public-link', True)):
            with self.subTest(name=name):
                self.work = self.root / ('work-' + name.replace('.', ''))
                self.work.mkdir(mode=0o700)
                path = self.source / name
                if symlink:
                    path.symlink_to('secrets/private-sentinel')
                else:
                    path.write_text('private configuration')
                self.git('add', name)
                with self.assertRaisesRegex(ValueError, 'private|symlink'):
                    self.fixture()
                self.git('rm', '--cached', name)
                path.unlink()

    def test_acorn_fixture_includes_only_suspended_flux_reference(self):
        cluster = self.source / 'clusters/home'
        cluster.mkdir(parents=True)
        repository = Path(__file__).resolve().parents[1]
        application = cluster / 'acorn-care-bot.yaml'
        application.write_bytes((repository / 'clusters/home/acorn-care-bot.yaml').read_bytes())
        self.git('add', '.')
        fixture = self.fixture()
        isolated = fixture.infra_path / 'tests/isolated'
        configuration = yaml.safe_load((isolated / 'kustomization.yaml').read_text())
        self.assertEqual(configuration['resources'], ['secrets-source.yaml', 'acorn-care-bot.yaml'])
        copied = yaml.safe_load((isolated / 'acorn-care-bot.yaml').read_text())
        self.assertEqual(copied['metadata']['name'], 'acorn-care-bot')
        self.assertEqual(copied['spec']['path'], './apps/acorn-care-bot')
        self.assertIs(copied['spec']['suspend'], True)
        self.assertFalse((isolated / 'apps').exists())

    def test_active_acorn_bot_cannot_enter_disposable_fixture(self):
        cluster = self.source / 'clusters/home'
        cluster.mkdir(parents=True)
        (cluster / 'acorn-care-bot.yaml').write_text('spec: {suspend: false}\n')
        self.git('add', '.')
        with self.assertRaisesRegex(ValueError, 'active application'):
            self.fixture()

    def test_active_migration_cannot_enter_disposable_fixture(self):
        contracts = self.source / 'tests/migrations'
        contracts.mkdir()
        (contracts / 'example.yaml').write_text('workloads: 1\n')
        cluster = self.source / 'clusters/home'
        cluster.mkdir(parents=True)
        (cluster / 'example.yaml').write_text('spec: {suspend: false}\n')
        self.git('add', '.')
        with self.assertRaisesRegex(ValueError, 'active migration'):
            self.fixture()

    def test_completed_application_is_excluded_from_disposable_cluster(self):
        contracts = self.source / 'tests/migrations'
        contracts.mkdir()
        (contracts / 'example.yaml').write_text('phase: active\nworkloads: 1\n')
        cluster = self.source / 'clusters/home'
        cluster.mkdir(parents=True)
        (cluster / 'example.yaml').write_text('spec: {suspend: false}\n')
        self.git('add', '.')
        fixture = self.fixture()
        isolated = yaml.safe_load((fixture.infra_path / 'tests/isolated/kustomization.yaml').read_text())
        self.assertNotIn('migration-example.yaml', isolated['resources'])
        self.assertFalse((fixture.infra_path / 'tests/isolated/migration-example.yaml').exists())


class EnvironmentTests(unittest.TestCase):
    def test_ambient_secret_and_git_settings_are_removed(self):
        with patch.dict(os.environ, {'SOPS_AGE_KEY': 'sentinel', 'AGE_IDENTITIES': 'sentinel',
                                     'GIT_CONFIG_COUNT': '1', 'GIT_CONFIG_KEY_0': 'credential.helper',
                                     'GIT_CONFIG_VALUE_0': 'sentinel', 'GH_TOKEN': 'sentinel'}):
            env = clean_env()
        self.assertNotIn('sentinel', str(env))
        self.assertEqual(env['GIT_CONFIG_GLOBAL'], os.devnull)
        self.assertEqual(env['GIT_CONFIG_NOSYSTEM'], '1')
        self.assertEqual(env['GIT_TERMINAL_PROMPT'], '0')


if __name__ == '__main__':
    unittest.main()
