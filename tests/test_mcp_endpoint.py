"""Render the pinned upstream chart and enforce the endpoint trust boundaries."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import migration_check

CHART = {'chart': 'kubernetes-mcp-server', 'version': '0.1.0',
         'url': 'oci://ghcr.io/containers/charts/kubernetes-mcp-server',
         'sha256': '2b9e0371b6a54ace8bfd9c100d61c766f0f8cec45de46040d28ea2e9f8c31484'}


@unittest.skipUnless(shutil.which('helm') and shutil.which('kubectl'), 'Helm and kubectl required')
class EndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raw = subprocess.check_output(['kubectl', 'kustomize', 'infrastructure/mcp-endpoint'], cwd=ROOT)
        # Match Flux substitution without changing Nginx's unbraced variables.
        raw = raw.decode().replace('${SERVER_IP}', '192.168.1.150').encode()
        objects = [obj for obj in yaml.safe_load_all(raw) if obj]
        source = next(obj['spec'] for obj in objects if obj['kind'] == 'OCIRepository')
        lock = {**CHART, 'url': source['url'] + '@' + source['ref']['digest']}
        with tempfile.TemporaryDirectory() as work:
            cache = Path(work)
            source = os.environ.get('MCP_CHART_CACHE')
            if source:
                (cache / (CHART['sha256'] + '.tgz')).write_bytes(Path(source).read_bytes())
            else:
                subprocess.run(['helm', 'pull', lock['url'], '--destination', str(cache)],
                               capture_output=True, check=True, timeout=60)
                archives = list(cache.glob('*.tgz'))
                if len(archives) != 1:
                    raise RuntimeError('Expected one pinned MCP chart archive')
                archives[0].replace(cache / (CHART['sha256'] + '.tgz'))
            cls.objects = migration_check.render(ROOT, 'mcp-endpoint',
                {'namespace': 'k3s-mcp', 'charts': {'k3s-mcp': lock}}, objects, cache)
        cls.pod = cls.object('Deployment', 'k3s-mcp')['spec']['template']['spec']

    @classmethod
    def object(cls, kind, name):
        return next(obj for obj in cls.objects if obj['kind'] == kind and obj['metadata']['name'] == name)

    def test_chart_cannot_add_rbac_secrets_or_public_ingress(self):
        forbidden = {'Secret', 'ServiceAccount', 'Role', 'ClusterRole', 'RoleBinding',
                     'ClusterRoleBinding', 'Ingress', 'IngressRoute', 'HTTPRoute'}
        self.assertFalse(forbidden.intersection(obj['kind'] for obj in self.objects))
        self.assertEqual(self.pod['serviceAccountName'], 'k3s-mcp')
        self.assertIs(self.pod['automountServiceAccountToken'], False)

    def test_only_gateway_is_exposed_at_server_ip(self):
        service = self.object('Service', 'k3s-mcp')['spec']
        self.assertEqual(service['type'], 'ClusterIP')
        self.assertEqual(service['externalIPs'], ['192.168.1.150'])
        self.assertEqual(service['ports'], [{'name': 'http', 'port': 8443, 'protocol': 'TCP', 'targetPort': 'https'}])
        policy = self.object('NetworkPolicy', 'mcp-gateway-only')['spec']
        self.assertEqual(policy['ingress'], [{'ports': [{'protocol': 'TCP', 'port': 8443}]}])
        self.assertEqual(policy['podSelector']['matchLabels'], self.pod_labels())

    def pod_labels(self):
        return self.object('Deployment', 'k3s-mcp')['spec']['selector']['matchLabels']

    def test_server_is_read_only_and_cannot_accept_external_identity(self):
        configuration = tomllib.loads(self.object('ConfigMap', 'k3s-mcp')['data']['config.toml'])
        self.assertIs(configuration['read_only'], True)
        self.assertEqual(configuration['cluster_auth_mode'], 'kubeconfig')
        self.assertEqual(configuration['cluster_provider_strategy'], 'in-cluster')
        self.assertEqual(configuration['bind_address'], '127.0.0.1')
        self.assertEqual(configuration['port'], '8080')
        self.assertEqual(configuration['toolsets'], ['core'])
        self.assertIn({'group': '', 'version': 'v1', 'kind': 'Secret'}, configuration['denied_resources'])

    def test_identity_and_authentication_files_are_separate(self):
        server, gateway = self.pod['containers']
        self.assertEqual({mount['name'] for mount in server['volumeMounts']}, {'tmp', 'config', 'api-identity'})
        gateway_mounts = {mount['name']: mount for mount in gateway['volumeMounts']}
        self.assertNotIn('api-identity', gateway_mounts)
        self.assertTrue(all(gateway_mounts[name]['readOnly'] for name in ('gateway-auth', 'gateway-tls')))
        auth = next(volume for volume in self.pod['volumes'] if volume['name'] == 'gateway-auth')['secret']
        self.assertEqual(auth['secretName'], 'mcp-http-auth')
        self.assertEqual(auth['items'], [{'key': 'users', 'path': 'users'}])
        self.assertNotIn('optional', auth)

    def test_workloads_are_pinned_unprivileged_without_resource_caps(self):
        for container in self.pod['containers']:
            self.assertRegex(container['image'], r'@sha256:[a-f0-9]{64}$')
            self.assertNotIn('limits', container['resources'])
            security = container['securityContext']
            self.assertIs(security['allowPrivilegeEscalation'], False)
            self.assertIs(security['readOnlyRootFilesystem'], True)
            self.assertGreater(security['runAsUser'], 0)
            self.assertEqual(security['capabilities']['drop'], ['ALL'])
        for probe in ('livenessProbe', 'readinessProbe'):
            self.assertEqual(self.pod['containers'][0][probe]['httpGet'],
                             {'path': '/healthz', 'port': 8443, 'scheme': 'HTTPS'})

    def test_gateway_checks_credentials_origin_and_strips_external_identity(self):
        configuration = self.object('ConfigMap', 'mcp-gateway')['data']['nginx.conf']
        for expected in ('auth_basic_user_file /etc/auth/users;', 'listen 8443 ssl;',
                         'proxy_set_header Authorization "";', 'proxy_set_header Origin "";',
                         'map $http_origin $origin_allowed', 'default 0;',
                         '"https://192.168.1.150:8443" 1;', 'if ($origin_allowed = 0) { return 403; }',
                         'ssl_certificate $tls_directory/tls.crt;', 'access_log off;'):
            self.assertIn(expected, configuration)
        self.assertNotIn('$$', configuration)

    def test_ip_certificate_and_dependency_order(self):
        self.assertEqual(self.object('Certificate', 'mcp-lan')['spec']['ipAddresses'], ['192.168.1.150'])
        flux = yaml.safe_load((ROOT / 'clusters/home/mcp-endpoint.yaml').read_text())['spec']
        self.assertEqual({item['name'] for item in flux['dependsOn']},
                         {'cluster-mcp-rbac', 'infra-controllers', 'cluster-secrets'})
        self.assertEqual(flux['postBuild']['substituteFrom'],
                         [{'kind': 'ConfigMap', 'name': 'cluster-settings', 'optional': False}])
        self.assertIs(flux['wait'], True)


if __name__ == '__main__':
    unittest.main()
