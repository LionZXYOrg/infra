"""Deployment assertion failures and disposable-host safety boundaries."""
import base64
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from ci import deployment


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.work = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.runner = deployment.Deployment(self.work / 'kubeconfig', '192.0.2.10', self.work / 'report')

    def test_container_diagnostics_keep_reasons_without_sensitive_fields(self):
        pod = {'kind': 'Pod', 'metadata': {'name': 'grafana', 'namespace': 'monitoring',
                'annotations': {'private': 'sentinel-annotation'}},
               'spec': {'containers': [{'name': 'sidecar', 'env': [{'value': 'sentinel-environment'}]}]},
               'status': {'containerStatuses': [{'name': 'sidecar', 'ready': False, 'restartCount': 3,
                   'state': {'waiting': {'reason': 'CrashLoopBackOff', 'message': 'sentinel-message'}},
                   'lastState': {'terminated': {'reason': 'OOMKilled', 'exitCode': 137,
                                                'message': 'sentinel-termination'}}}]}}
        records = deployment.safe_resource_status([pod])
        self.assertEqual(records[0]['containers'][0]['lastTerminatedReason'], 'OOMKilled')
        self.assertEqual(records[0]['containers'][0]['lastExitCode'], 137)
        self.assertNotIn('sentinel-', json.dumps(records))

    def test_every_kubernetes_command_uses_explicit_kubeconfig(self):
        with patch.object(deployment, 'command', return_value=b'') as run:
            self.runner.kube(['get', 'nodes'])
            self.runner.flux(['reconcile', 'source', 'git', 'flux-system'])
        for call in run.call_args_list:
            args = call.args[0]
            self.assertEqual(args[1:3], ['--kubeconfig', str(self.work / 'kubeconfig')])

    def test_wrong_probe_fails_without_disclosing_value(self):
        response = json.dumps({'data': {'probe': base64.b64encode(b'sentinel').decode()}}).encode()
        with patch.object(self.runner, 'kube', return_value=response):
            with self.assertRaisesRegex(RuntimeError, 'probe') as caught:
                self.runner.verify_probe(b'isolated-value-one')
        self.assertNotIn('sentinel', str(caught.exception))

    def test_https_requires_issued_certificate_and_page_marker(self):
        cert = json.dumps({'data': {'tls.crt': base64.b64encode(b'public-cert').decode()}}).encode()
        with patch.object(self.runner, 'kube', return_value=cert), \
                patch.object(deployment, 'command', return_value=b'K3s + Flux + GitHub works.') as run:
            self.runner.verify_https()
        args = run.call_args.args[0]
        self.assertIn('--cacert', args)
        self.assertIn('--resolve', args)
        self.assertNotIn('--insecure', args)
        self.assertIn('demo.infra.test:443:192.0.2.10', args)
        with patch.object(self.runner, 'kube', return_value=cert), \
                patch.object(deployment, 'command', return_value=b'wrong page'):
            with self.assertRaisesRegex(RuntimeError, 'HTTPS'):
                self.runner.verify_https()

    def test_https_command_failure_propagates(self):
        cert = json.dumps({'data': {'tls.crt': base64.b64encode(b'public-cert').decode()}}).encode()
        with patch.object(self.runner, 'kube', return_value=cert), \
                patch.object(deployment, 'command', side_effect=RuntimeError('curl failed')):
            with self.assertRaises(RuntimeError):
                self.runner.verify_https()

    def test_failed_readiness_stops_deployment(self):
        fixture = unittest.mock.Mock()
        with patch.object(self.runner, 'verify_fresh_cluster'), \
                patch.object(self.runner, 'kube', side_effect=[b'applied', RuntimeError('not ready')]), \
                patch.object(deployment, 'command', return_value=b'components'):
            with self.assertRaises(RuntimeError):
                self.runner.run(fixture)
        fixture.bootstrap_objects.assert_not_called()

    def test_deployment_checks_initial_update_and_repeat_reconciliation(self):
        fixture = unittest.mock.Mock(infra_revision='infra-revision', secrets_revision='secret-revision')
        fixture.infra_path = self.work
        fixture.bootstrap_objects.return_value = []
        with patch.object(self.runner, 'verify_fresh_cluster'), \
                patch.object(self.runner, 'kube', return_value=b''), \
                patch.object(self.runner, 'wait'), patch.object(self.runner, 'verify_ready') as ready, \
                patch.object(self.runner, 'verify_probe') as probe, \
                patch.object(self.runner, 'verify_https') as https, \
                patch.object(deployment, 'verify_endpoint') as mcp, \
                patch.object(self.runner, 'verify_no_production_resources') as isolated, \
                patch.object(self.runner, 'flux') as flux, \
                patch.object(deployment, 'command', return_value=b'components'):
            report = self.runner.run(fixture)
        self.assertEqual([call.args[0] for call in probe.call_args_list],
                         [b'isolated-value-one', b'isolated-value-two'])
        fixture.update_probe.assert_called_once_with(b'isolated-value-two')
        self.assertEqual(ready.call_count, 2)
        self.assertEqual(https.call_count, 2)
        self.assertEqual(mcp.call_count, 2)
        self.assertEqual(isolated.call_count, 2)
        self.assertEqual([call.args[0][2] for call in flux.call_args_list], ['cluster-secrets', 'flux-system'])
        self.assertIn('repeat-reconciliation', report['passed'])

    def test_prepared_migration_must_be_suspended_without_pods_or_releases(self):
        directory = self.work / 'tests/migrations'
        directory.mkdir(parents=True)
        (directory / 'example.yaml').write_text('workloads: 1\n')
        fixture = unittest.mock.Mock(infra_path=self.work)
        for suspended, pods, releases in [(True, [], []), (False, [], []),
                                         (True, [{'metadata': {'name': 'app'}}], []),
                                         (True, [], [{'metadata': {'name': 'app'}}])]:
            responses = [json.dumps({'spec': {'suspend': suspended}}).encode(),
                         json.dumps({'items': pods}).encode(), json.dumps({'items': releases}).encode()]
            with self.subTest(suspended=suspended, pods=bool(pods), releases=bool(releases)), \
                    patch.object(self.runner, 'kube', side_effect=responses):
                if suspended and not pods and not releases:
                    self.runner.verify_migrations_suspended(fixture)
                else:
                    with self.assertRaises(RuntimeError):
                        self.runner.verify_migrations_suspended(fixture)

    def test_acme_issuer_fails_isolation(self):
        issuer = {'items': [{'metadata': {'name': 'production'}, 'spec': {'acme': {'server': 'url'}}}]}
        with patch.object(self.runner, 'kube', return_value=json.dumps(issuer).encode()):
            with self.assertRaisesRegex(RuntimeError, 'production'):
                self.runner.verify_no_production_resources()

    def test_shared_namespace_is_checked_for_accidental_application_startup(self):
        directory = self.work / 'tests/migrations'
        directory.mkdir(parents=True)
        (directory / 'example.yaml').write_text('workloads: 1\nnamespace: shared\n')
        fixture = unittest.mock.Mock(infra_path=self.work)
        responses = [json.dumps({'spec': {'suspend': True}}).encode(),
                     json.dumps({'items': [{'metadata': {'name': 'app'}}]}).encode(),
                     json.dumps({'items': []}).encode()]
        with patch.object(self.runner, 'kube', side_effect=responses) as kube:
            with self.assertRaisesRegex(RuntimeError, 'started application'):
                self.runner.verify_migrations_suspended(fixture)
        self.assertEqual(kube.call_args_list[1].args[0][:2], ['-n', 'shared'])
        self.assertEqual(kube.call_args_list[2].args[0][:2], ['-n', 'shared'])

    def test_completed_application_must_not_run_in_disposable_cluster(self):
        directory = self.work / 'tests/migrations'
        directory.mkdir(parents=True)
        (directory / 'example.yaml').write_text('phase: active\n')
        fixture = unittest.mock.Mock(infra_path=self.work)
        responses = [json.dumps({'items': []}).encode(), json.dumps({'items': []}).encode()]
        with patch.object(self.runner, 'kube', side_effect=responses) as kube:
            self.runner.verify_migrations_suspended(fixture)
        self.assertFalse(any('kustomization' in call.args[0] for call in kube.call_args_list))
        responses[0] = json.dumps({'items': [{'metadata': {'name': 'production'}}]}).encode()
        with patch.object(self.runner, 'kube', side_effect=responses):
            with self.assertRaisesRegex(RuntimeError, 'started application'):
                self.runner.verify_migrations_suspended(fixture)

    def test_production_certificate_and_cloudflare_secret_fail_isolation(self):
        for certificate, secret in (({'metadata': {'name': 'wildcard', 'namespace': 'traefik'}}, None),
                                    (None, {'metadata': {'name': 'cloudflare-api-token', 'namespace': 'cert-manager'}})):
            def kube(args, **kwargs):
                if 'clusterissuers,issuers' in args:
                    return b'{"items":[]}'
                if 'certificates' in args:
                    return json.dumps({'items': [certificate] if certificate else []}).encode()
                return b'cert-manager/cloudflare-api-token\n' if secret else b''
            with self.subTest(certificate=bool(certificate)), patch.object(self.runner, 'kube', side_effect=kube):
                with self.assertRaisesRegex(RuntimeError, 'production'):
                    self.runner.verify_no_production_resources()

    def test_diagnostics_exclude_messages_annotations_and_secret_data(self):
        objects = [{'kind': 'Secret', 'data': {'token': 'sentinel'},
                    'metadata': {'name': 'probe', 'annotations': {'raw': 'sentinel'}},
                    'status': {'conditions': [{'type': 'Ready', 'status': 'False',
                                              'reason': 'Failed', 'message': 'sentinel'}]}}]
        records = deployment.safe_resource_status(objects)
        self.assertNotIn('sentinel', json.dumps(records))
        self.assertEqual(records[0]['conditions'][0]['reason'], 'Failed')

    def test_existing_cluster_or_flux_installation_is_refused(self):
        for node, namespace in (('production', b''), ('ci-infra', b'namespace/flux-system')):
            def kube(args, **kwargs):
                if 'nodes' in args:
                    return json.dumps({'items': [{'metadata': {'name': node}}]}).encode()
                return namespace
            with self.subTest(node=node), patch.object(self.runner, 'kube', side_effect=kube):
                with self.assertRaisesRegex(RuntimeError, 'Refusing'):
                    self.runner.verify_fresh_cluster()

    def test_pod_diagnostics_keep_reasons_without_messages_or_container_arguments(self):
        objects = [{'kind': 'Pod', 'metadata': {'name': 'gateway'},
                    'spec': {'containers': [{'env': [{'value': 'sentinel'}]}]},
                    'status': {'containerStatuses': [{'name': 'gateway', 'ready': False, 'restartCount': 2,
                        'state': {'waiting': {'reason': 'CrashLoopBackOff', 'message': 'sentinel'}},
                        'lastState': {'terminated': {'reason': 'Error', 'exitCode': 1, 'message': 'sentinel'}}}]}}]
        records = deployment.safe_resource_status(objects)
        self.assertNotIn('sentinel', json.dumps(records))
        self.assertEqual(records[0]['containers'][0]['waitingReason'], 'CrashLoopBackOff')
        self.assertEqual(records[0]['containers'][0]['lastExitCode'], 1)


class HostGuardTests(unittest.TestCase):
    def test_existing_k3s_refused_before_provisioning(self):
        with tempfile.TemporaryDirectory() as work:
            path = Path(work)
            for name, content in [('uname', '#!/bin/sh\necho Linux\n'),
                                  ('k3s', '#!/bin/sh\nexit 0\n')]:
                binary = path / name
                binary.write_text(content)
                binary.chmod(0o755)
            root = Path(__file__).resolve().parents[1]
            result = subprocess.run(['/bin/bash', str(root / 'tests/ci/run.sh'), '--disposable',
                                     str(path / 'report')], env={'PATH': str(path) + ':/usr/bin:/bin',
                                                                'HOME': work}, capture_output=True, timeout=5)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b'Existing K3s', result.stderr)
            self.assertFalse((path / 'report').exists())

    def test_disposable_acknowledgement_required(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(['/bin/bash', str(root / 'tests/ci/run.sh')],
                                capture_output=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'--disposable', result.stderr)


if __name__ == '__main__':
    unittest.main()
