"""Keep the separate care bot disabled until its token and state are ready."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

import yaml

from ci import deployment
from ci.fixtures import include_suspended_application

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'apps/acorn-care-bot'


def read(path):
    return yaml.safe_load(path.read_text())


class AcornManifestTests(unittest.TestCase):
    def setUp(self):
        self.deployment = read(APP / 'deployment.yaml')
        self.spec = self.deployment['spec']
        self.pod = self.spec['template']['spec']
        self.container = self.pod['containers'][0]

    def test_two_independent_activation_guards_and_dependencies(self):
        flux = read(ROOT / 'clusters/home/acorn-care-bot.yaml')['spec']
        self.assertIs(flux['suspend'], True)
        self.assertEqual(self.spec['replicas'], 0)
        self.assertEqual({x['name'] for x in flux['dependsOn']}, {'infra-namespaces', 'cluster-secrets'})
        self.assertEqual(flux['path'], './apps/acorn-care-bot')
        self.assertEqual(flux['deletionPolicy'], 'Orphan')
        self.assertIs(flux['wait'], True)

    def test_bot_configuration_stays_in_private_secrets(self):
        env = {item['name']: item for item in self.container['env']}
        self.assertEqual(set(env), {'TELEGRAM_BOT_TOKEN_FILE', 'ACORN_CONFIG_PATH', 'ACORN_STATE_PATH',
                                    'TELEGRAM_CHAT_ID', 'TELEGRAM_TOPIC_ID', 'TELEGRAM_ALLOWED_USER_IDS'})
        self.assertEqual(env['TELEGRAM_BOT_TOKEN_FILE']['value'], '/var/run/acorn/token')
        self.assertEqual(env['ACORN_CONFIG_PATH']['value'], '/var/run/acorn/config.json')
        self.assertNotIn('TELEGRAM_BOT_TOKEN', env)
        mount = next(v for v in self.container['volumeMounts'] if v['name'] == 'configuration')
        self.assertEqual(mount, {'name': 'configuration', 'mountPath': '/var/run/acorn', 'readOnly': True})
        for name, key in [('TELEGRAM_CHAT_ID', 'chat-id'), ('TELEGRAM_TOPIC_ID', 'topic-id'),
                          ('TELEGRAM_ALLOWED_USER_IDS', 'allowed-user-ids')]:
            self.assertEqual(env[name]['valueFrom']['secretKeyRef'], {'name': 'acorn-care-bot', 'key': key})
        secret = next(v['secret'] for v in self.pod['volumes'] if v['name'] == 'configuration')
        self.assertEqual(secret['secretName'], 'acorn-care-bot')
        self.assertEqual(secret['items'], [{'key': 'bot-token', 'path': 'token'},
                                           {'key': 'config.json', 'path': 'config.json'}])
        self.assertNotIn('optional', secret)
        for path in APP.glob('*.yaml'):
            self.assertFalse(any(obj and obj.get('kind') == 'Secret' for obj in yaml.safe_load_all(path.read_text())))

    def test_single_writer_with_retained_state_and_private_image(self):
        self.assertEqual(self.spec['strategy'], {'type': 'Recreate'})
        self.assertEqual(self.pod['imagePullSecrets'], [{'name': 'ghcr-auth'}])
        self.assertRegex(self.container['image'], r'^ghcr\.io/lionzxyorg/acorn-care-bot:sha-[a-f0-9]{40}$')
        volume = read(APP / 'storage.yaml')
        self.assertEqual(volume['metadata']['name'], 'acorn-care-bot-state')
        self.assertEqual(volume['spec']['accessModes'], ['ReadWriteOnce'])
        self.assertEqual(volume['metadata']['annotations']['kustomize.toolkit.fluxcd.io/prune'], 'disabled')
        claim = next(v['persistentVolumeClaim'] for v in self.pod['volumes'] if v['name'] == 'state')
        self.assertEqual(claim['claimName'], volume['metadata']['name'])
        env = {v['name']: v for v in self.container['env']}
        self.assertEqual(env['ACORN_STATE_PATH']['value'], '/data/state.json')
        self.assertTrue(any(v['mountPath'] == '/data' for v in self.container['volumeMounts']))

    def test_non_root_read_only_container_without_cluster_access(self):
        self.assertIs(self.pod['automountServiceAccountToken'], False)
        self.assertIs(self.pod['securityContext']['runAsNonRoot'], True)
        self.assertEqual(self.pod['securityContext']['runAsUser'], 10001)
        security = self.container['securityContext']
        self.assertIs(security['allowPrivilegeEscalation'], False)
        self.assertIs(security['readOnlyRootFilesystem'], True)
        self.assertEqual(security['capabilities']['drop'], ['ALL'])
        for probe, path in [('startupProbe', '/livez'), ('livenessProbe', '/livez'), ('readinessProbe', '/readyz')]:
            self.assertEqual(self.container[probe]['httpGet'], {'path': path, 'port': 'health'})
        self.assertFalse(any('ingress' in p.name or 'service.yaml' == p.name for p in APP.iterdir()))

    def test_namespace_and_flux_resource_indexes(self):
        name = 'acorn-care-bot'
        self.assertEqual(self.deployment['metadata']['name'], name)
        self.assertEqual(self.spec['selector']['matchLabels'], {'app': name})
        self.assertEqual(self.spec['template']['metadata']['labels'], {'app': name})
        flux = read(ROOT / 'clusters/home/acorn-care-bot.yaml')
        self.assertEqual(flux['metadata'], {'name': name, 'namespace': 'flux-system'})
        namespaces = read(ROOT / 'infrastructure/namespaces/kustomization.yaml')['resources']
        self.assertEqual(namespaces, sorted(namespaces))
        self.assertIn('acorn-care-bot.yaml', namespaces)
        namespace = read(ROOT / 'infrastructure/namespaces/acorn-care-bot.yaml')
        self.assertEqual(namespace['metadata']['name'], name)
        self.assertEqual(namespace['metadata']['labels']['pod-security.kubernetes.io/enforce'], 'restricted')
        self.assertIn('acorn-care-bot.yaml', read(ROOT / 'clusters/home/kustomization.yaml')['resources'])
        self.assertEqual(read(APP / 'kustomization.yaml')['namespace'], 'acorn-care-bot')

    def test_secret_namespace_dependencies_do_not_enable_other_applications(self):
        namespaces = read(ROOT / 'infrastructure/namespaces/kustomization.yaml')['resources']
        applications = read(ROOT / 'clusters/home/kustomization.yaml')['resources']
        for name in ('obsidian', 'psybot', 'replacevowelbot'):
            with self.subTest(namespace=name):
                self.assertIn(f'{name}.yaml', namespaces)
                namespace = read(ROOT / f'infrastructure/namespaces/{name}.yaml')
                self.assertEqual(namespace, {'apiVersion': 'v1', 'kind': 'Namespace',
                                             'metadata': {'name': name}})
                self.assertNotIn(f'{name}.yaml', applications)

    def test_cluster_keeps_home_assistant_and_the_suspended_bot(self):
        applications = read(ROOT / 'clusters/home/kustomization.yaml')['resources']
        self.assertIn('homeassist.yaml', applications)
        self.assertIn('acorn-care-bot.yaml', applications)
        self.assertNotIn('apps.yaml', applications)
        self.assertFalse((ROOT / 'apps/demo').exists())

    @unittest.skipUnless(shutil.which('kubectl'), 'kubectl required')
    def test_rendered_resources_are_namespaced_and_do_not_include_credentials(self):
        raw = subprocess.check_output(['kubectl', 'kustomize', str(APP)])
        objects = [obj for obj in yaml.safe_load_all(raw) if obj]
        self.assertEqual({obj['kind'] for obj in objects}, {'Deployment', 'PersistentVolumeClaim'})
        self.assertTrue(all(obj['metadata']['namespace'] == 'acorn-care-bot' for obj in objects))


class AcornIsolationTests(unittest.TestCase):
    def setUp(self):
        self.work = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.source = self.work / 'clusters/home/acorn-care-bot.yaml'
        self.source.parent.mkdir(parents=True)
        self.source.write_text('spec: {suspend: true, path: ./apps/acorn-care-bot}\n')
        self.isolated = self.work / 'tests/isolated/kustomization.yaml'
        self.isolated.parent.mkdir(parents=True)

    def test_fixture_copies_only_suspended_flux_object(self):
        configuration = {'resources': ['foundation.yaml']}
        include_suspended_application(self.work, self.isolated, configuration, 'acorn-care-bot')
        self.assertEqual(configuration['resources'], ['foundation.yaml', 'acorn-care-bot.yaml'])
        self.assertEqual((self.isolated.parent / 'acorn-care-bot.yaml').read_text(), self.source.read_text())
        self.assertFalse((self.isolated.parent / 'apps').exists())
        self.source.write_text('spec: {suspend: false}\n')
        with self.assertRaisesRegex(ValueError, 'active application'):
            include_suspended_application(self.work, self.isolated, configuration, 'acorn-care-bot')

    def test_disposable_runner_checks_the_renamed_application(self):
        runner = deployment.Deployment(self.work / 'kubeconfig', '192.0.2.10', self.work / 'report')
        fixture = Mock(infra_path=self.work)
        with patch.object(runner, 'verify_suspended_application') as verify:
            runner.verify_migrations_suspended(fixture)
        verify.assert_called_once_with(fixture, 'acorn-care-bot')

    def test_disposable_assertion_rejects_activation_and_workloads(self):
        runner = deployment.Deployment(self.work / 'kubeconfig', '192.0.2.10', self.work / 'report')
        for suspended, workloads in [(True, []), (False, []), (True, [{'kind': 'Pod'}])]:
            responses = [json.dumps({'spec': {'suspend': suspended}}).encode(),
                         json.dumps({'items': workloads}).encode()]
            with self.subTest(suspended=suspended, workloads=bool(workloads)), patch.object(runner, 'kube', side_effect=responses):
                if suspended and not workloads:
                    runner.verify_suspended_application(Mock(infra_path=self.work), 'acorn-care-bot')
                else:
                    with self.assertRaises(RuntimeError):
                        runner.verify_suspended_application(Mock(infra_path=self.work), 'acorn-care-bot')


if __name__ == '__main__':
    unittest.main()
