"""Offline regression coverage for monitoring wiring, safety, and assertion failures."""
import json
from pathlib import Path
import re
import unittest
from unittest.mock import Mock, patch

import yaml
from ci import monitoring

ROOT = Path(__file__).resolve().parents[1]
STACK = ROOT / 'infrastructure/monitoring/stack'
CONFIG = ROOT / 'infrastructure/monitoring/config'


def load(path):
    return yaml.safe_load(path.read_text())


def vector(value='1'):
    return {'status': 'success', 'data': {'resultType': 'vector',
                                        'result': [{'metric': {}, 'value': [0, value]}]}}


class MonitoringConfigurationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.values = load(STACK / 'values.yaml')
        cls.release = load(STACK / 'helmrelease.yaml')['spec']
        cls.layers = {obj['metadata']['name']: obj['spec'] for obj in
                      yaml.safe_load_all((ROOT / 'clusters/home/monitoring.yaml').read_text())}

    def test_exact_chart_pin_and_crd_upgrades(self):
        self.assertEqual(self.release['chart']['spec']['chart'], 'kube-prometheus-stack')
        self.assertRegex(self.release['chart']['spec']['version'], r'^\d+\.\d+\.\d+$')
        for operation in ('install', 'upgrade'):
            self.assertEqual(self.release[operation]['crds'], 'CreateReplace')
        self.assertEqual(self.release['driftDetection']['mode'], 'enabled')

    def test_monitoring_requests_do_not_cap_container_memory(self):
        def inspect(value):
            if isinstance(value, dict):
                if isinstance(value.get('resources'), dict):
                    resources = value['resources']
                    self.assertFalse({'cpu', 'memory'} & set(resources.get('limits') or {}))
                for child in value.values():
                    inspect(child)
            elif isinstance(value, list):
                for child in value:
                    inspect(child)
        inspect(self.values)

    def test_private_values_are_optional_and_merged_last(self):
        refs = self.release['valuesFrom']
        self.assertEqual(refs[0]['kind'], 'ConfigMap')
        self.assertEqual(refs[-1], {'kind': 'Secret', 'name': 'monitoring-private-values',
                                   'valuesKey': 'values.yaml', 'optional': True})
        self.assertNotIn('values', self.release)

    def test_configmap_changes_trigger_reconciliation(self):
        spec = load(STACK / 'kustomization.yaml')
        self.assertEqual(spec['generatorOptions']['labels']['reconcile.fluxcd.io/watch'], 'Enabled')
        self.assertTrue(spec['generatorOptions']['disableNameSuffixHash'])
        self.assertEqual(spec['configMapGenerator'][0]['name'], self.release['valuesFrom'][0]['name'])

    def test_operator_precedes_custom_resources_without_tls_or_app_dependency(self):
        stack, config = self.layers['infra-monitoring'], self.layers['monitoring-config']
        self.assertEqual(stack['dependsOn'], [{'name': 'cluster-secrets'}])
        self.assertEqual(config['dependsOn'], [{'name': 'infra-monitoring'}])
        self.assertEqual(stack['healthChecks'][0]['name'], 'monitoring')
        self.assertFalse(stack['prune'])
        self.assertEqual(stack['deletionPolicy'], 'Orphan')
        self.assertTrue(config['prune'])
        for spec in self.layers.values():
            self.assertTrue((ROOT / spec['path'] / 'kustomization.yaml').exists())

    def test_production_and_isolated_layers_match(self):
        self.assertEqual((ROOT / 'clusters/home/monitoring.yaml').read_text(),
                         (ROOT / 'tests/isolated/monitoring.yaml').read_text())
        for folder in ('clusters/home', 'tests/isolated', 'infrastructure/namespaces'):
            self.assertIn('monitoring.yaml', load(ROOT / folder / 'kustomization.yaml')['resources'])

    def test_all_services_are_private_and_grafana_requires_login(self):
        for app in ('prometheus', 'grafana', 'alertmanager'):
            self.assertFalse(self.values[app]['ingress']['enabled'])
            self.assertEqual(self.values[app]['service']['type'], 'ClusterIP')
        grafana = self.values['grafana']
        self.assertEqual(grafana['adminPassword'], '')
        self.assertFalse(grafana['grafana.ini']['auth.anonymous']['enabled'])
        self.assertFalse(grafana['grafana.ini']['users']['allow_sign_up'])
        self.assertFalse(self.values['prometheus-node-exporter']['hostNetwork'])
        self.assertFalse(self.values['prometheus']['prometheusSpec']['enableAdminAPI'])

    def test_grafana_sidecars_are_namespace_and_configmap_scoped(self):
        self.assertTrue(self.values['grafana']['rbac']['namespaced'])
        for name in ('dashboards', 'datasources'):
            spec = self.values['grafana']['sidecar'][name]
            self.assertEqual(spec['searchNamespace'], 'monitoring')
            self.assertEqual(spec['resource'], 'configmap')

    def test_notification_default_does_not_claim_or_send_external_alerts(self):
        config = self.values['alertmanager']['config']
        self.assertEqual(config['route']['receiver'], 'null')
        self.assertEqual(config['receivers'], [{'name': 'null'}])

    def test_single_node_bounded_persistent_storage(self):
        p = self.values['prometheus']['prometheusSpec']
        self.assertEqual((p['replicas'], p['shards'], p['retention'], p['retentionSize']), (1, 1, '7d', '8GB'))
        self.assertEqual((p['scrapeInterval'], p['evaluationInterval']), ('60s', '60s'))
        for spec, size in [(p['storageSpec'], '10Gi'),
                           (self.values['alertmanager']['alertmanagerSpec']['storage'], '1Gi')]:
            pvc = spec['volumeClaimTemplate']['spec']
            self.assertEqual(pvc['storageClassName'], 'local-path')
            self.assertEqual(pvc['resources']['requests']['storage'], size)
            self.assertEqual(pvc['accessModes'], ['ReadWriteOnce'])
        self.assertTrue(self.values['grafana']['persistence']['enabled'])
        self.assertEqual(p['persistentVolumeClaimRetentionPolicy']['whenDeleted'], 'Retain')

    def test_k3s_inapplicable_targets_and_rules_disabled_without_disabling_tls(self):
        for key in ('kubeEtcd', 'kubeControllerManager', 'kubeScheduler', 'kubeProxy'):
            self.assertFalse(self.values[key]['enabled'])
        for key in ('etcd', 'kubeControllerManager', 'kubeSchedulerAlerting', 'kubeSchedulerRecording', 'kubeProxy'):
            self.assertFalse(self.values['defaultRules']['rules'][key])
        self.assertFalse(self.values['kubelet']['serviceMonitor']['insecureSkipVerify'])
        self.assertTrue(self.values['kubeApiServer']['enabled'])
        self.assertTrue(self.values['kubelet']['enabled'])

    def test_custom_monitors_and_rules_match_prometheus_selectors(self):
        p = self.values['prometheus']['prometheusSpec']
        for selector in ('podMonitorSelector', 'serviceMonitorSelector', 'ruleSelector'):
            self.assertEqual(p[selector]['matchLabels'], {'release': 'monitoring'})
        for filename in ('podmonitors.yaml', 'rules.yaml'):
            for obj in yaml.safe_load_all((CONFIG / filename).read_text()):
                self.assertEqual(obj['metadata']['labels']['release'], 'monitoring')
                self.assertEqual(obj['metadata']['namespace'], 'monitoring')

    def test_cert_manager_preserves_resource_namespace_labels(self):
        monitors = {obj['metadata']['name']: obj for obj in yaml.safe_load_all((CONFIG / 'podmonitors.yaml').read_text())}
        self.assertEqual(monitors['flux']['spec']['podMetricsEndpoints'][0]['port'], 'http-prom')
        self.assertTrue(monitors['cert-manager']['spec']['podMetricsEndpoints'][0]['honorLabels'])
        self.assertEqual(monitors['traefik']['spec']['podMetricsEndpoints'][0]['port'], 'metrics')

    def test_flux_resource_collection_preserves_core_collectors(self):
        ksm = self.values['kube-state-metrics']
        self.assertNotIn('collectors', ksm)
        self.assertNotIn('--custom-resource-state-only=true', ksm.get('extraArgs', []))
        resources = ksm['customResourceState']['config']['spec']['resources']
        self.assertEqual({obj['groupVersionKind']['kind'] for obj in resources},
                         {'Kustomization', 'HelmRelease', 'GitRepository', 'HelmRepository', 'HelmChart'})
        self.assertTrue(all(obj['metricNamePrefix'] == 'gotk' for obj in resources))
        rules = ksm['rbac']['extraRules']
        for rule in rules:
            self.assertEqual(set(rule['verbs']), {'list', 'watch'})
            self.assertNotIn('*', rule['resources'])
            self.assertNotIn('secrets', rule['resources'])
        for obj in resources:
            gvk = obj['groupVersionKind']
            self.assertTrue(any(gvk['group'] in rule['apiGroups'] and
                                {'Kustomization': 'kustomizations', 'HelmRelease': 'helmreleases',
                                 'GitRepository': 'gitrepositories', 'HelmRepository': 'helmrepositories',
                                 'HelmChart': 'helmcharts'}[gvk['kind']] in rule['resources'] for rule in rules))

    def test_alerts_have_delay_severity_and_synthetic_cases(self):
        rules = {r['alert']: r for group in load(CONFIG / 'rules.yaml')['spec']['groups'] for r in group['rules']}
        tests = load(ROOT / 'tests/monitoring/rules.test.yaml')
        self.assertEqual(tests['rule_files'], ['rules.yaml'])
        covered = {case['alertname'] for test in tests['tests'] for case in test['alert_rule_test']}
        self.assertEqual(covered, set(rules))
        self.assertGreaterEqual(len(tests['tests']), 9)
        for rule in rules.values():
            self.assertEqual(rule['for'], '15m')
            self.assertIn(rule['labels']['severity'], {'warning', 'critical'})
        self.assertIn('suspended!="true"', rules['FluxReconciliationFailed']['expr'])
        self.assertNotIn('gotk_reconcile_condition', (CONFIG / 'rules.yaml').read_text())

    def test_dashboard_is_valid_and_all_panels_use_a_datasource(self):
        d = json.loads((CONFIG / 'home-overview.json').read_text())
        self.assertEqual(d['uid'], 'home-k3s-overview')
        self.assertGreaterEqual(len(d['panels']), 8)
        self.assertEqual(len({p['id'] for p in d['panels']}), len(d['panels']))
        for panel in d['panels']:
            self.assertEqual(panel['datasource']['type'], 'prometheus')
            self.assertTrue(panel['targets'][0]['expr'])
        generator = load(CONFIG / 'kustomization.yaml')['configMapGenerator'][0]
        self.assertEqual(generator['options']['labels']['grafana_dashboard'], '1')

    def test_public_files_contain_no_secret_objects(self):
        for folder in (STACK, CONFIG):
            for path in folder.glob('*.yaml'):
                for obj in yaml.safe_load_all(path.read_text()):
                    if isinstance(obj, dict):
                        self.assertNotEqual(obj.get('kind'), 'Secret')

    def test_resource_requests_are_explicit(self):
        objects = [self.values['prometheus']['prometheusSpec'],
                   self.values['alertmanager']['alertmanagerSpec'], self.values['grafana'],
                   self.values['prometheusOperator'], self.values['kube-state-metrics'],
                   self.values['prometheus-node-exporter']]
        for obj in objects:
            self.assertRegex(obj['resources']['requests']['cpu'], r'^\d+m$')
            self.assertRegex(obj['resources']['requests']['memory'], r'^\d+(Mi|Gi)$')


class MonitoringAssertionTests(unittest.TestCase):
    def test_positive_vectors_only(self):
        self.assertTrue(monitoring.positive_vector(vector()))
        for value in ('0', '-1', 'NaN', '+Inf', '-Inf', 'not-a-number'):
            with self.subTest(value=value):
                self.assertFalse(monitoring.positive_vector(vector(value)))

    def test_empty_errors_and_malformed_results_fail(self):
        for response in ({}, {'status': 'error'}, {'status': 'success', 'data': {'result': []}},
                         {'status': 'success', 'data': {'resultType': 'vector', 'result': [{}]}}):
            self.assertFalse(monitoring.positive_vector(response))

    def test_missing_or_unhealthy_rules_fail(self):
        def response(rules): return {'status': 'success', 'data': {'groups': [{'rules': rules}]}}
        self.assertTrue(monitoring.healthy_rules(response([{'name': 'A', 'health': 'ok'}]), {'A'}))
        for rules in ([], [{'name': 'A', 'health': 'err'}], [{'name': 'A', 'health': 'ok', 'lastError': 'x'}]):
            self.assertFalse(monitoring.healthy_rules(response(rules), {'A'}))

    def test_external_services_are_rejected(self):
        self.assertTrue(monitoring.private_services([{'spec': {'type': 'ClusterIP'}}]))
        for spec in ({'type': 'LoadBalancer'}, {'type': 'NodePort'}, {'type': 'ClusterIP', 'externalIPs': ['192.0.2.1']}):
            self.assertFalse(monitoring.private_services([{'spec': spec}]))
        self.assertFalse(monitoring.private_services([]))

    def test_retry_handles_transient_failure(self):
        check = Mock(side_effect=[RuntimeError('sentinel'), False, True])
        with patch.object(monitoring.time, 'sleep') as sleep:
            monitoring.wait_for(check, 'safe label', timeout=10)
        self.assertEqual(check.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_timeout_redacts_underlying_error(self):
        check = Mock(side_effect=RuntimeError('sensitive sentinel'))
        with self.assertRaisesRegex(RuntimeError, 'safe label') as caught:
            monitoring.wait_for(check, 'safe label', timeout=0)
        self.assertNotIn('sentinel', str(caught.exception))

    def test_query_is_urlencoded_and_has_request_timeout(self):
        runner = Mock()
        runner.kube.return_value = json.dumps(vector()).encode()
        monitoring.wait_query(runner, 'a{label="x"} > 0', 'safe label')
        args = runner.kube.call_args.args[0]
        self.assertIn('--request-timeout=15s', args)
        self.assertIn('query=a%7Blabel%3D%22x%22%7D+%3E+0', args[2])

    def test_rule_job_uses_deployed_image_and_no_service_account_token(self):
        runner = Mock()
        runner.kube.side_effect = [
            b'{"items":[{"metadata":{"name":"ci-infra"}}]}',
            b'{"items":[{"spec":{"containers":[{"name":"prometheus","image":"example.invalid/prometheus:test"}]}}]}',
            b'', b'', b'',
        ]
        monitoring.verify_rule_tests(runner)
        config, job = list(yaml.safe_load_all(runner.kube.call_args_list[2].kwargs['data']))
        spec = job['spec']['template']['spec']
        self.assertFalse(spec['automountServiceAccountToken'])
        self.assertEqual(spec['containers'][0]['image'], 'example.invalid/prometheus:test')
        self.assertEqual(spec['containers'][0]['command'],
                         ['/bin/promtool', 'test', 'rules', '/tests/rules.test.yaml'])
        self.assertTrue(spec['containers'][0]['securityContext']['readOnlyRootFilesystem'])
        # Promtool creates temporary TSDB data even though test inputs are read-only.
        self.assertIn({'name': 'scratch', 'mountPath': '/tmp'}, spec['containers'][0]['volumeMounts'])
        self.assertIn({'name': 'scratch', 'emptyDir': {'sizeLimit': '256Mi'}}, spec['volumes'])
        self.assertEqual(spec['securityContext']['fsGroup'], 65534)
        self.assertEqual(config['kind'], 'ConfigMap')
        self.assertIn('rules.test.yaml', config['data'])
        self.assertIn('delete', runner.kube.call_args.args[0])

    def test_rule_job_failure_is_cleaned_up_without_fetching_logs(self):
        runner = Mock()
        runner.kube.side_effect = [
            b'{"items":[{"metadata":{"name":"ci-infra"}}]}',
            b'{"items":[{"spec":{"containers":[{"name":"prometheus","image":"example.invalid/prometheus:test"}]}}]}',
            b'', RuntimeError('Job did not complete'), b'',
        ]
        with self.assertRaisesRegex(RuntimeError, 'Job did not complete'):
            monitoring.verify_rule_tests(runner)
        self.assertIn('delete', runner.kube.call_args.args[0])
        self.assertFalse(any('logs' in call.args[0] for call in runner.kube.call_args_list))

    def test_rule_job_refuses_a_non_disposable_cluster(self):
        runner = Mock()
        runner.kube.return_value = b'{"items":[{"metadata":{"name":"home-1"}}]}'
        with self.assertRaisesRegex(RuntimeError, 'Refusing'):
            monitoring.verify_rule_tests(runner)
        self.assertEqual(runner.kube.call_count, 1)


if __name__ == '__main__':
    unittest.main()
