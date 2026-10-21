"""Activate one public-image bot and keep production workloads out of CI."""
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

    def test_activation_keeps_one_replica_and_existing_dependencies(self):
        flux = read(ROOT / 'clusters/home/acorn-care-bot.yaml')['spec']
        self.assertIs(flux['suspend'], False)
        self.assertEqual(self.spec['replicas'], 1)
        self.assertEqual({x['name'] for x in flux['dependsOn']}, {'infra-namespaces', 'cluster-secrets'})
        self.assertEqual(flux['path'], './apps/acorn-care-bot')
        self.assertEqual(flux['deletionPolicy'], 'Orphan')
        self.assertIs(flux['wait'], True)

    def test_only_token_mount_uses_the_application_secret(self):
        env = {item['name']: item for item in self.container['env']}
        self.assertEqual(env['TELEGRAM_BOT_TOKEN_FILE']['value'], '/var/run/acorn/token')
        self.assertNotIn('TELEGRAM_BOT_TOKEN', env)
        mount = next(v for v in self.container['volumeMounts'] if v['name'] == 'token')
        self.assertEqual(mount, {'name': 'token', 'mountPath': '/var/run/acorn', 'readOnly': True})
        secret = next(v['secret'] for v in self.pod['volumes'] if v['name'] == 'token')
        self.assertEqual(secret['secretName'], 'acorn-care-bot')
        self.assertEqual(secret['items'], [{'key': 'bot-token', 'path': 'token'}])
        self.assertNotIn('optional', secret)
        self.assertEqual(len([v for v in self.pod['volumes'] if 'secret' in v]), 1)
        for path in APP.glob('*.yaml'):
            self.assertFalse(any(obj and obj.get('kind') == 'Secret' for obj in yaml.safe_load_all(path.read_text())))

    def test_nonsecret_environment_uses_generated_config_map(self):
        env = {item['name']: item for item in self.container['env']}
        self.assertEqual(set(env), {'TELEGRAM_BOT_TOKEN_FILE', 'ACORN_CONFIG_PATH', 'ACORN_STATE_PATH',
                                   'TELEGRAM_CHAT_ID', 'TELEGRAM_TOPIC_ID', 'TELEGRAM_ALLOWED_USER_IDS',
                                   'ACORN_FRESH_START'})
        for name in ('TELEGRAM_CHAT_ID', 'TELEGRAM_TOPIC_ID', 'TELEGRAM_ALLOWED_USER_IDS', 'ACORN_FRESH_START'):
            self.assertEqual(env[name]['valueFrom'], {'configMapKeyRef': {
                'name': 'acorn-care-bot-config', 'key': name}})
        self.assertEqual(env['ACORN_CONFIG_PATH']['value'], '/etc/acorn/config.json')
        mount = next(v for v in self.container['volumeMounts'] if v['name'] == 'configuration')
        self.assertEqual(mount, {'name': 'configuration', 'mountPath': '/etc/acorn', 'readOnly': True})
        volume = next(v['configMap'] for v in self.pod['volumes'] if v['name'] == 'configuration')
        self.assertEqual(volume['name'], 'acorn-care-bot-config')
        self.assertEqual(volume['items'], [{'key': 'config.json', 'path': 'config.json'}])
        self.assertNotIn('optional', volume)
        generator = read(APP / 'kustomization.yaml')['configMapGenerator']
        self.assertEqual(generator, [{'name': 'acorn-care-bot-config',
                                     'envs': ['runtime.properties'], 'files': ['config.json']}])
        self.assertFalse(read(APP / 'kustomization.yaml').get('generatorOptions', {}).get('disableNameSuffixHash', False))

    def test_public_settings_preserve_approved_plan_without_old_records(self):
        values = dict(line.split('=', 1) for line in (APP / 'runtime.properties').read_text().splitlines()
                      if line and not line.startswith('#'))
        self.assertEqual(values, {'TELEGRAM_CHAT_ID': '-1004424243504', 'TELEGRAM_TOPIC_ID': '210',
                                 'TELEGRAM_ALLOWED_USER_IDS': '474744725,1235433892', 'ACORN_FRESH_START': '1'})
        config = json.loads((APP / 'config.json').read_text())
        self.assertEqual(config['name'], 'Acorn')
        self.assertEqual(config['mention'], '@palmette @LionZXY')
        self.assertEqual(config['timezone'], 'Europe/London')
        self.assertEqual(config['quiet_end'], '07:00')
        self.assertEqual([slot['time'] for slot in config['slots']], ['07:00', '14:00', '19:00'])
        self.assertEqual(config['food_transfer_policy'], 'add_full')
        self.assertEqual(config['start_date'], '2026-10-19')
        self.assertEqual([stage['start'] for stage in config['stages']],
                         ['2026-10-19', '2026-11-02', '2026-11-09', '2026-11-16'])
        self.assertEqual([stage['wet'] for stage in config['stages']],
                         [[49, 49, 97], [33, 32, 65], [0, 0, 65], [0, 0, 0]])
        self.assertEqual([stage['dry'] for stage in config['stages']],
                         [[10, 10, 10], [15, 15, 16], [21, 20, 21], [26, 26, 26]])
        for stage in config['stages']:
            self.assertEqual(sum(stage['wet']), stage['daily_wet'])
            self.assertEqual(sum(stage['dry']), stage['daily_dry'])
        self.assertEqual(config['wet_can_grams'], 195)
        self.assertEqual(config['slots'][2]['cleaning_tasks'], ['litter'])
        for key in ('tasks', 'checklists', 'carryovers', 'audit', 'standalone', 'inline_panels',
                    'emissions', 'progression_hold', 'proposals', 'created_by', 'source_message', 'destination'):
            self.assertNotIn(key, config)
        self.assertFalse(any('token' in key.lower() or 'password' in key.lower() for key in values))

    def test_automatic_repeats_are_disabled_without_pausing_scheduled_care(self):
        config = json.loads((APP / 'config.json').read_text())
        self.assertIs(config['automatic_repeats'], False)
        self.assertEqual(config['repeat_minutes'], 30)
        self.assertEqual([slot['time'] for slot in config['slots']], ['07:00', '14:00', '19:00'])
        self.assertEqual(self.spec['replicas'], 1)
        self.assertEqual(self.spec['strategy'], {'type': 'Recreate'})

    def test_bowls_and_wiping_are_morning_only_without_changing_other_care(self):
        config = json.loads((APP / 'config.json').read_text())
        self.assertIs(config['bowls_morning_only'], True)
        defaults = ['litter', 'bowls', 'water']
        cleaning = {slot['key']: slot.get('cleaning_tasks', defaults) if slot['cleaning'] else []
                    for slot in config['slots']}
        self.assertEqual(cleaning, {'breakfast': ['litter', 'bowls', 'water'],
                                   'lunch': [], 'dinner': ['litter']})
        self.assertEqual(config['food_transfer_policy'], 'add_full')
        self.assertIs(config['automatic_repeats'], False)
        self.assertEqual([slot['time'] for slot in config['slots']], ['07:00', '14:00', '19:00'])
        self.assertEqual(self.spec['replicas'], 1)
        self.assertEqual(self.spec['strategy'], {'type': 'Recreate'})

    def test_single_writer_with_retained_state_and_public_pinned_image(self):
        self.assertEqual(self.spec['strategy'], {'type': 'Recreate'})
        self.assertNotIn('imagePullSecrets', self.pod)
        self.assertEqual(self.container['image'],
                         'ghcr.io/lionzxyorg/acorn-care-bot:sha-839fa74c1513e41634e5bde685b4f6183fdd5508'
                         '@sha256:f3dbbaed96afa65de2e3ab952c40fd61ff9a27beeb5e48b19b7ea42f3ba811c7')
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
        for name in ('psybot', 'replacevowelbot'):
            with self.subTest(namespace=name):
                self.assertIn(f'{name}.yaml', namespaces)
                namespace = read(ROOT / f'infrastructure/namespaces/{name}.yaml')
                self.assertEqual(namespace, {'apiVersion': 'v1', 'kind': 'Namespace',
                                             'metadata': {'name': name}})
                self.assertNotIn(f'{name}.yaml', applications)

    def test_cluster_keeps_home_assistant_and_the_bot(self):
        applications = read(ROOT / 'clusters/home/kustomization.yaml')['resources']
        self.assertIn('homeassist.yaml', applications)
        self.assertIn('acorn-care-bot.yaml', applications)
        self.assertNotIn('apps.yaml', applications)
        self.assertFalse((ROOT / 'apps/demo').exists())

    @unittest.skipUnless(shutil.which('kubectl'), 'kubectl required')
    def test_rendered_resources_are_namespaced_and_do_not_include_credentials(self):
        raw = subprocess.check_output(['kubectl', 'kustomize', str(APP)])
        objects = [obj for obj in yaml.safe_load_all(raw) if obj]
        self.assertEqual({obj['kind'] for obj in objects}, {'ConfigMap', 'Deployment', 'PersistentVolumeClaim'})
        self.assertTrue(all(obj['metadata']['namespace'] == 'acorn-care-bot' for obj in objects))
        config = next(obj for obj in objects if obj['kind'] == 'ConfigMap')
        self.assertEqual(set(config['data']), {'TELEGRAM_CHAT_ID', 'TELEGRAM_TOPIC_ID',
                                             'TELEGRAM_ALLOWED_USER_IDS', 'ACORN_FRESH_START', 'config.json'})
        self.assertEqual(json.loads(config['data']['config.json']), json.loads((APP / 'config.json').read_text()))
        name = config['metadata']['name']
        self.assertRegex(name, r'^acorn-care-bot-config-[a-z0-9]+$')
        deployment = next(obj for obj in objects if obj['kind'] == 'Deployment')
        pod = deployment['spec']['template']['spec']
        for item in pod['containers'][0]['env']:
            if 'valueFrom' in item:
                self.assertEqual(item['valueFrom']['configMapKeyRef']['name'], name)
        self.assertEqual(next(v['configMap']['name'] for v in pod['volumes'] if 'configMap' in v), name)

    @unittest.skipUnless(shutil.which('kubectl'), 'kubectl required')
    def test_configuration_edits_change_the_generated_name_and_pod_references(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / 'application'
            shutil.copytree(APP, target)
            def render_name():
                objects = list(yaml.safe_load_all(subprocess.check_output(['kubectl', 'kustomize', str(target)])))
                config = next(obj for obj in objects if obj['kind'] == 'ConfigMap')
                pod = next(obj for obj in objects if obj['kind'] == 'Deployment')['spec']['template']['spec']
                name = config['metadata']['name']
                self.assertEqual(next(v['configMap']['name'] for v in pod['volumes'] if 'configMap' in v), name)
                for item in pod['containers'][0]['env']:
                    if 'valueFrom' in item:
                        self.assertEqual(item['valueFrom']['configMapKeyRef']['name'], name)
                return name
            before = render_name()
            settings = target / 'runtime.properties'
            settings.write_text(settings.read_text().replace('TELEGRAM_TOPIC_ID=210', 'TELEGRAM_TOPIC_ID=211'))
            after_environment = render_name()
            self.assertNotEqual(before, after_environment)
            plan = target / 'config.json'
            value = json.loads(plan.read_text())
            value['repeat_minutes'] += 1
            plan.write_text(json.dumps(value))
            after_plan = render_name()
            self.assertNotEqual(after_environment, after_plan)
            value['automatic_repeats'] = True
            plan.write_text(json.dumps(value))
            self.assertNotEqual(after_plan, render_name())


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

    def test_fixture_excludes_active_production_application(self):
        self.source.write_text('spec: {suspend: false}\n')
        configuration = {'resources': ['foundation.yaml']}
        include_suspended_application(self.work, self.isolated, configuration, 'acorn-care-bot')
        self.assertEqual(configuration['resources'], ['foundation.yaml'])
        self.assertFalse((self.isolated.parent / 'acorn-care-bot.yaml').exists())
        self.assertFalse((self.isolated.parent / 'apps').exists())

    def test_fixture_rejects_ambiguous_suspension(self):
        for value in ('null', '"false"', '0'):
            self.source.write_text('spec: {suspend: ' + value + '}\n')
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'boolean suspension'):
                include_suspended_application(self.work, self.isolated, {'resources': []}, 'acorn-care-bot')

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

    def test_active_production_application_and_workloads_must_stay_absent(self):
        self.source.write_text('spec: {suspend: false}\n')
        runner = deployment.Deployment(self.work / 'kubeconfig', '192.0.2.10', self.work / 'report')
        for objects, workloads in [([], []), ([{'kind': 'Kustomization'}], []),
                                   ([], [{'kind': 'Pod'}]), ([], [{'kind': 'Deployment'}]),
                                   ([], [{'kind': 'StatefulSet'}]), ([], [{'kind': 'Job'}])]:
            responses = [json.dumps({'items': objects}).encode(), json.dumps({'items': workloads}).encode()]
            with self.subTest(objects=objects, workloads=workloads), patch.object(runner, 'kube', side_effect=responses):
                if objects or workloads:
                    with self.assertRaises(RuntimeError):
                        runner.verify_suspended_application(Mock(infra_path=self.work), 'acorn-care-bot')
                else:
                    runner.verify_suspended_application(Mock(infra_path=self.work), 'acorn-care-bot')


if __name__ == '__main__':
    unittest.main()
