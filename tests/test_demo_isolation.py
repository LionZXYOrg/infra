"""Keep the HTTPS demo available to disposable tests only."""
from pathlib import Path
import shutil
import subprocess
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('kubectl'), 'kubectl needed for Kustomize rendering')
class DemoIsolationTests(unittest.TestCase):
    def render(self, path):
        output = subprocess.check_output(['kubectl', 'kustomize', str(path)], cwd=ROOT, timeout=30)
        return list(yaml.safe_load_all(output))

    def test_production_flux_graph_excludes_demo_and_test_paths(self):
        index = yaml.safe_load((ROOT / 'clusters/home/kustomization.yaml').read_text())
        for resource in index['resources']:
            if resource == 'flux-system':
                continue  # Flux generates this directory during bootstrap.
            for obj in yaml.safe_load_all((ROOT / 'clusters/home' / resource).read_text()):
                if obj['kind'] != 'Kustomization':
                    continue
                self.assertNotEqual(obj['metadata']['name'], 'apps')
                if obj['spec']['sourceRef']['name'] == 'flux-system':
                    path = (ROOT / obj['spec']['path']).resolve()
                    self.assertFalse(path.is_relative_to(ROOT / 'tests'))
                    self.assertNotEqual(path, ROOT / 'apps/demo')

    def test_production_manifests_exclude_demo_namespace_and_workloads(self):
        paths = [ROOT / 'infrastructure/namespaces',
                 *(path.parent for path in sorted((ROOT / 'apps').glob('*/kustomization.yaml')))]
        for path in paths:
            with self.subTest(path=path.relative_to(ROOT)):
                for obj in self.render(path):
                    metadata = obj['metadata']
                    self.assertNotEqual(metadata.get('namespace'), 'demo')
                    if obj['kind'] == 'Namespace':
                        self.assertNotEqual(metadata['name'], 'demo')

    def test_fixture_creates_demo_namespace_before_probe_secret(self):
        foundation = {obj['metadata']['name']: obj for obj in yaml.safe_load_all(
            (ROOT / 'tests/isolated/foundation.yaml').read_text())}
        namespaces = self.render(ROOT / foundation['infra-namespaces']['spec']['path'])
        self.assertIn(('Namespace', 'demo'),
                      {(obj['kind'], obj['metadata']['name']) for obj in namespaces})
        self.assertIn({'name': 'infra-namespaces'}, foundation['cluster-secrets']['spec']['dependsOn'])

    def test_fixture_keeps_healthy_demo_and_test_https_route(self):
        objects = self.render(ROOT / 'tests/isolated/demo')
        self.assertTrue(all(obj['metadata']['namespace'] == 'demo' for obj in objects))
        deployment = next(obj for obj in objects if obj['kind'] == 'Deployment')
        container = deployment['spec']['template']['spec']['containers'][0]
        for probe in ('readinessProbe', 'livenessProbe'):
            self.assertEqual(container[probe]['httpGet'], {'path': '/', 'port': 'http'})
        route = next(obj for obj in objects if obj['kind'] == 'IngressRoute')
        self.assertEqual(route['spec']['routes'][0]['match'], 'Host(`demo.infra.test`)')
        page = next(obj for obj in objects if obj['kind'] == 'ConfigMap')
        self.assertIn('K3s + Flux + GitHub works.', page['data']['index.html'])


if __name__ == '__main__':
    unittest.main()
