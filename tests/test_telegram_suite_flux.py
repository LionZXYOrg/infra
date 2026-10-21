"""Validate cluster values and the pinned external chart without application code."""
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

import yaml

from ci import deployment
from ci.fixtures import include_suspended_application

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'apps/telegram-bot-suite'


def read(path):
    return yaml.safe_load(path.read_text())


class TelegramSuiteFluxTests(unittest.TestCase):
    def setUp(self):
        self.flux = read(ROOT / 'clusters/home/telegram-bot-suite.yaml')
        self.release = read(APP / 'helmrelease.yaml')
        self.source = read(APP / 'source.yaml')
        self.values = self.release['spec']['values']

    def test_preparation_has_three_activation_guards(self):
        self.assertIs(self.flux['spec']['suspend'], True)
        self.assertIs(self.release['spec']['suspend'], True)
        self.assertEqual(self.values['replicas'], 0)
        self.assertEqual(self.flux['spec']['targetNamespace'], 'acorn-care-bot')
        self.assertEqual(self.flux['spec']['deletionPolicy'], 'Orphan')
        self.assertEqual({v['name'] for v in self.flux['spec']['dependsOn']},
                         {'infra-namespaces', 'cluster-secrets'})

    def test_only_flux_and_instance_values_remain_in_infra(self):
        self.assertEqual(read(APP / 'kustomization.yaml')['resources'],
                         ['source.yaml', 'helmrelease.yaml'])
        self.assertEqual({p.name for p in APP.iterdir()},
                         {'source.yaml', 'helmrelease.yaml', 'kustomization.yaml'})
        self.assertEqual(self.release['kind'], 'HelmRelease')
        self.assertEqual(self.source['kind'], 'GitRepository')
        self.assertNotIn('secretRef', self.source['spec'])
        self.assertNotIn('valuesFrom', self.release['spec'])

    def test_chart_commit_and_image_digest_are_immutable(self):
        source = self.source['spec']
        self.assertEqual(source['url'], 'https://github.com/LionZXYOrg/acorn-care-bot')
        self.assertEqual(set(source['ref']), {'commit'})
        self.assertRegex(source['ref']['commit'], r'^[0-9a-f]{40}$')
        self.assertEqual(source['ignore'], '/*\n!/charts\n')
        self.assertIn(':sha-' + source['ref']['commit'] + '@', self.values['image'])
        spec = self.release['spec']['chart']['spec']
        self.assertEqual(spec['chart'], './charts/telegram-bot-suite')
        self.assertEqual(spec['reconcileStrategy'], 'Revision')
        self.assertEqual(spec['sourceRef'], {'kind': 'GitRepository', 'name': 'telegram-bot-suite'})
        self.assertRegex(self.values['image'],
                         r'^ghcr\.io/lionzxyorg/acorn-care-bot:sha-[0-9a-f]{40}@sha256:[0-9a-f]{64}$')
        self.assertEqual(self.release['spec']['install']['remediation']['retries'], 0)
        self.assertEqual(self.release['spec']['upgrade']['remediation']['retries'], 0)

    def test_existing_care_claim_plan_and_permissions_are_preserved(self):
        workers = {v['id']: v for v in self.values['workers']}
        self.assertEqual(set(workers), {'acorn', 'greenwich'})
        acorn = workers['acorn']
        self.assertEqual(acorn['persistence'], {'existingClaim': 'acorn-care-bot-state'})
        self.assertEqual(json.loads(acorn['config']['config.json']),
                         json.loads((ROOT / 'apps/acorn-care-bot/config.json').read_text()))
        original = dict(line.split('=', 1) for line in
                        (ROOT / 'apps/acorn-care-bot/runtime.properties').read_text().splitlines()
                        if line and not line.startswith('#'))
        route = acorn['route']
        self.assertEqual(route['chat_id'], int(original['TELEGRAM_CHAT_ID']))
        self.assertEqual(route['topic_id'], int(original['TELEGRAM_TOPIC_ID']))
        self.assertEqual(route['allowed_user_ids'],
                         [int(v) for v in original['TELEGRAM_ALLOWED_USER_IDS'].split(',')])
        self.assertEqual(acorn['env']['ACORN_FRESH_START'], original['ACORN_FRESH_START'])
        self.assertEqual(self.values['legacyAcorn'], {'workerId': 'acorn'})
        self.assertEqual((acorn['uid'], workers['greenwich']['uid']), (10001, 10002))
        self.assertEqual(workers['greenwich']['route']['topic_id'], 45)
        self.assertFalse(workers['greenwich']['route']['receives_updates'])
        self.assertNotIn('getUpdates', workers['greenwich']['route']['methods'])

    def test_secrets_are_references_and_greenwich_code_comes_from_the_image(self):
        self.assertEqual(self.values['gateway']['tokenSecret'],
                         {'name': 'acorn-care-bot', 'key': 'bot-token'})
        bins = next(w for w in self.values['workers'] if w['id'] == 'greenwich')
        self.assertEqual(bins['command'], ['python3', '/app/greenwich/runner.py'])
        self.assertEqual(bins['secretMounts'], [{
            'secretName': 'greenwich-bin-bot-property', 'mountPath': '/etc/bins',
            'items': [{'key': 'property.json', 'path': 'property.json'}]}])
        for worker in self.values['workers']:
            self.assertFalse(any('TOKEN' in name for name in worker['env']))
            self.assertNotIn('secretKeyRef', json.dumps(worker))
        self.assertEqual(self.values['gateway']['persistence']['storageClassName'], 'local-path')

    def test_fixture_includes_suspended_object_but_omits_active_object(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'infra/clusters/home'
            source.mkdir(parents=True)
            isolated_root = root / 'isolated/kustomization.yaml'
            isolated_root.parent.mkdir()
            data = self.flux
            path = source / 'telegram-bot-suite.yaml'
            path.write_text(yaml.safe_dump(data))
            isolated = {'resources': []}
            include_suspended_application(root / 'infra', isolated_root, isolated, 'telegram-bot-suite')
            self.assertEqual(isolated['resources'], ['telegram-bot-suite.yaml'])
            data['spec']['suspend'] = False
            path.write_text(yaml.safe_dump(data))
            isolated = {'resources': []}
            include_suspended_application(root / 'infra', isolated_root, isolated, 'telegram-bot-suite')
            self.assertEqual(isolated['resources'], [])

    def test_disposable_deployment_rejects_workloads_and_helm_releases(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / 'clusters/home'
            path.mkdir(parents=True)
            shutil.copyfile(ROOT / 'clusters/home/telegram-bot-suite.yaml', path / 'telegram-bot-suite.yaml')
            fixture = Mock(infra_path=root)
            runner = deployment.Deployment(root / 'kubeconfig', '192.0.2.10', root / 'report')
            replies = [json.dumps({'spec': {'suspend': True}}), json.dumps({'items': []})]
            with patch.object(runner, 'kube', side_effect=replies) as kube:
                runner.verify_suspended_application(fixture, 'telegram-bot-suite')
            self.assertEqual(kube.call_args.args[0], [
                '-n', 'acorn-care-bot', 'get',
                'pods,deployments,statefulsets,jobs,cronjobs,helmreleases', '-o', 'json'])
            replies[1] = json.dumps({'items': [{'kind': 'HelmRelease', 'metadata': {'name': 'unexpected'}}]})
            with patch.object(runner, 'kube', side_effect=replies), self.assertRaisesRegex(RuntimeError, 'started workloads'):
                runner.verify_suspended_application(fixture, 'telegram-bot-suite')


if __name__ == '__main__':
    unittest.main()
