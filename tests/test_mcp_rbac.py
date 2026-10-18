"""Keep the GitOps MCP identity read-only, and test local credential minting."""
from pathlib import Path
import os
import shutil
import stat
import subprocess
import tempfile
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


class MCPCredentialScriptTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        kubectl = self.home / 'kubectl'
        kubectl.write_text('''#!/bin/sh
case "$*" in
  *"get serviceaccount k3s-mcp -o name"*) echo serviceaccount/k3s-mcp ;;
  *"cluster.server}"*) printf %s https://192.0.2.10:6443 ;;
  *"certificate-authority-data}"*) printf %s "${FAKE_CA-ZmFrZS1jYQ==}" ;;
  *"create token k3s-mcp --duration=24h"*) printf %s "${FAKE_TOKEN-fake.jwt.token}" ;;
  *) echo 'unexpected kubectl command' >&2; exit 1 ;;
esac
''')
        kubectl.chmod(0o755)
        self.env = os.environ.copy()
        self.env.update({'HOME': str(self.home), 'KUBECONFIG': str(self.home / 'admin.yaml'),
                         'PATH': str(self.home) + ':' + self.env['PATH']})

    def run_script(self, env=None):
        return subprocess.run(['bash', str(ROOT / 'scripts/k3s-mcp-kubeconfig.sh')],
                              env=env or self.env, capture_output=True, text=True, timeout=15)

    def test_creates_atomic_private_kubeconfig_without_leaking_token(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        target = self.home / '.kube/k3s-mcp-readonly.yaml'
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        self.assertNotIn('fake.jwt.token', result.stdout + result.stderr)
        obj = yaml.safe_load(target.read_text())
        self.assertEqual(obj['users'][0]['user']['token'], 'fake.jwt.token')
        self.assertEqual(obj['clusters'][0]['cluster']['server'], 'https://192.0.2.10:6443')
        self.assertEqual(obj['clusters'][0]['cluster']['certificate-authority-data'], 'ZmFrZS1jYQ==')
        self.assertEqual(list((self.home / '.kube').glob('*.tmp.*')), [])

        updated_env = self.env | {'FAKE_TOKEN': 'refreshed.jwt.token'}
        result = self.run_script(updated_env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(yaml.safe_load(target.read_text())['users'][0]['user']['token'],
                         'refreshed.jwt.token')
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)

    def test_rejects_missing_admin_identity(self):
        env = {k: v for k, v in self.env.items() if k != 'KUBECONFIG'}
        result = self.run_script(env)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.home / '.kube/k3s-mcp-readonly.yaml').exists())

    def test_refuses_kubeconfig_without_certificate_authority(self):
        env = self.env | {'FAKE_CA': ''}
        result = self.run_script(env)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.home / '.kube/k3s-mcp-readonly.yaml').exists())

    def test_rejects_relative_output_path(self):
        env = self.env | {'K3S_MCP_KUBECONFIG': 'relative.yaml'}
        result = self.run_script(env)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.home / 'relative.yaml').exists())


if __name__ == '__main__':
    unittest.main()
