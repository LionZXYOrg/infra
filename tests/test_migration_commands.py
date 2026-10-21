"""Rehearse Git-only activation with public files and generated image digests."""
from pathlib import Path
import re
import shutil
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import finish_migration
import migration_check as migration


class MigrationCommandTests(unittest.TestCase):
    def test_activation_cleans_helpers_and_preserves_storage(self):
        for name, contract in migration.contracts():
            if contract.get('phase') == 'active':
                continue
            with self.subTest(service=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                application = root / 'apps' / name
                shutil.copytree(ROOT / 'apps' / name, application)
                for source in [ROOT / 'clusters/home' / f'{name}.yaml',
                               ROOT / 'clusters/home/kustomization.yaml',
                               ROOT / 'infrastructure/namespaces/kustomization.yaml',
                               ROOT / 'tests/migrations' / f'{name}.yaml']:
                    target = root / source.relative_to(ROOT)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
                text = (application / 'MIGRATION.md').read_text()
                blocks = re.findall(r'```bash\n(.*?)\n```', text, re.S)
                self.assertIn(len(blocks), (1, 2))
                self.assertNotIn('ssh lionzxy@', text)
                if len(blocks) == 2:
                    self.assertIn(f'python3 scripts/finish_migration.py {name}', blocks[1])
                    self.assertIn('gh pr checks --watch', blocks[1])
                    self.assertIn('gh pr merge --squash', blocks[1])
                    self.assertIn('reconcile kustomization', blocks[1])
                # Published image pins are a prerequisite. Only this disposable copy gets fixture digests.
                for release in application.glob('helmrelease-*.yaml'):
                    source = release.read_text()
                    for pending in contract.get('pendingImages', []):
                        source = source.replace(pending.rsplit(':', 1)[-1], 'fixture@sha256:' + 'a' * 64)
                    release.write_text(source)
                storage = {path.name: path.read_bytes() for path in application.glob('storage*.yaml')}
                finish_migration.finish(root, name)
                active = migration.load(root / 'tests/migrations' / f'{name}.yaml')
                migration.check_preparation(root, name, active, migration.definitions(root, name))
                self.assertEqual(active['phase'], 'active')
                self.assertFalse((application / 'MIGRATION.md').exists())
                self.assertFalse(list(application.glob('restore*.yaml')))
                self.assertEqual(storage, {path.name: path.read_bytes() for path in application.glob('storage*.yaml')})
