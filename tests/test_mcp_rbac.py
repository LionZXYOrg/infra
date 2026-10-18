"""Keep the endpoint Kubernetes identity read-only."""
from pathlib import Path
import shutil
import subprocess
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
SERVICE_ACCOUNT = {'kind': 'ServiceAccount', 'name': 'k3s-mcp', 'namespace': 'k3s-mcp'}


class MCPRBACContractTests(unittest.TestCase):
    def setUp(self):
        self.objects = list(yaml.safe_load_all((ROOT / 'infrastructure/mcp/rbac.yaml').read_text()))

    def test_service_account_is_not_automounted(self):
        account = next(obj for obj in self.objects if obj['kind'] == 'ServiceAccount')
        self.assertEqual(account['metadata']['name'], SERVICE_ACCOUNT['name'])
        self.assertEqual(account['metadata']['namespace'], SERVICE_ACCOUNT['namespace'])
        self.assertIs(account['automountServiceAccountToken'], False)
        self.assertNotIn('Secret', [obj['kind'] for obj in self.objects])

    def test_only_read_verbs_and_explicit_nonsecret_resources(self):
        roles = [obj for obj in self.objects if obj['kind'] in ('Role', 'ClusterRole')]
        self.assertEqual({obj['metadata']['name'] for obj in roles},
                         {'k3s-mcp-node-reader', 'k3s-mcp-custom-reader'})
        for role in roles:
            for rule in role['rules']:
                self.assertTrue(set(rule['verbs']) <= {'get', 'list', 'watch'})
                self.assertNotIn('secrets', rule['resources'])
                self.assertNotIn('*', rule['resources'])
                self.assertNotIn('*', rule['verbs'])
        nodes = next(role for role in roles if role['metadata']['name'] == 'k3s-mcp-node-reader')
        self.assertEqual(nodes['rules'][0]['resources'], ['nodes'])

    def test_bindings_only_use_view_or_read_only_roles(self):
        bindings = [obj for obj in self.objects if obj['kind'] in ('RoleBinding', 'ClusterRoleBinding')]
        self.assertEqual({obj['roleRef']['name'] for obj in bindings},
                         {'view', 'k3s-mcp-node-reader', 'k3s-mcp-custom-reader'})
        self.assertTrue(all(obj['kind'] == 'ClusterRoleBinding' for obj in bindings))
        for binding in bindings:
            self.assertEqual(binding['subjects'], [SERVICE_ACCOUNT])
            self.assertEqual(binding['roleRef']['kind'], 'ClusterRole')

    @unittest.skipUnless(shutil.which('kubectl'), 'kubectl needed for Kustomize rendering')
    def test_kustomize_renders_every_role_and_binding(self):
        output = subprocess.check_output(['kubectl', 'kustomize', 'infrastructure/mcp'],
                                         cwd=ROOT, timeout=30)
        rendered = list(yaml.safe_load_all(output))
        self.assertEqual({(obj['kind'], obj['metadata']['name']) for obj in rendered},
                         {(obj['kind'], obj['metadata']['name']) for obj in self.objects})

    def test_flux_and_namespace_indices_reconcile_mcp(self):
        namespace = yaml.safe_load((ROOT / 'infrastructure/namespaces/k3s-mcp.yaml').read_text())
        self.assertEqual(namespace['metadata']['name'], 'k3s-mcp')
        namespace_index = yaml.safe_load((ROOT / 'infrastructure/namespaces/kustomization.yaml').read_text())
        self.assertIn('k3s-mcp.yaml', namespace_index['resources'])
        rb_index = yaml.safe_load((ROOT / 'infrastructure/mcp/kustomization.yaml').read_text())
        self.assertEqual(rb_index['resources'], ['rbac.yaml'])
        cluster_index = yaml.safe_load((ROOT / 'clusters/home/kustomization.yaml').read_text())
        self.assertIn('mcp.yaml', cluster_index['resources'])
        mcp_flux = yaml.safe_load((ROOT / 'clusters/home/mcp.yaml').read_text())
        self.assertEqual(mcp_flux['metadata']['name'], 'cluster-mcp-rbac')
        self.assertEqual(mcp_flux['spec']['path'], './infrastructure/mcp')
        self.assertIn({'name': 'infra-namespaces'}, mcp_flux['spec']['dependsOn'])
        self.assertIs(mcp_flux['spec']['prune'], True)


if __name__ == '__main__':
    unittest.main()
