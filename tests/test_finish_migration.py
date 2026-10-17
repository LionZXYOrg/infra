"""Activation changes Git files without deleting storage or using a cluster."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import finish_migration


class FinishMigrationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for directory in ['apps/example', 'clusters/home', 'tests/migrations']:
            (self.root / directory).mkdir(parents=True)
        self.app = self.root / 'apps/example'
        (self.app / 'storage.yaml').write_text('retained storage\n')
        (self.app / 'MIGRATION.md').write_text('```bash\n:\n```\n')
        (self.app / 'restore-pod.yaml').write_text('temporary helper\n')
        (self.app / 'kustomization.yaml').write_text('resources: [storage.yaml, helmrelease-example.yaml]\n')
        (self.app / 'helmrelease-example.yaml').write_text('spec: {suspend: true}\n')
        (self.root / 'clusters/home/example.yaml').write_text('spec: {suspend: true}\n')
        self.contract = self.root / 'tests/migrations/example.yaml'
        self.contract.write_text('charts: {}\n')
        self.enterContext(patch.object(finish_migration.migration, 'definitions', return_value=[]))
        self.enterContext(patch.object(finish_migration.migration, 'check_preparation'))

    def snapshot(self):
        return {str(path.relative_to(self.root)): path.read_bytes() for path in self.root.rglob('*') if path.is_file()}

    def test_enables_both_controls_and_keeps_storage(self):
        finish_migration.finish(self.root, 'example')
        for path in [self.app / 'helmrelease-example.yaml', self.root / 'clusters/home/example.yaml']:
            self.assertIs(yaml.safe_load(path.read_text())['spec']['suspend'], False)
        self.assertEqual(yaml.safe_load(self.contract.read_text())['phase'], 'active')
        self.assertEqual((self.app / 'storage.yaml').read_text(), 'retained storage\n')
        self.assertFalse((self.app / 'restore-pod.yaml').exists())
        self.assertFalse((self.app / 'MIGRATION.md').exists())

    def test_pending_image_fails_before_any_file_changes(self):
        self.contract.write_text('pendingImages: [registry.invalid/app:pending]\n')
        (self.app / 'helmrelease-example.yaml').write_text('spec: {suspend: true, tag: pending}\n')
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'replace its pending tag'):
            finish_migration.finish(self.root, 'example')
        self.assertEqual(self.snapshot(), before)

    def test_registered_restore_helper_fails_before_changes(self):
        (self.app / 'kustomization.yaml').write_text('resources: [restore-pod.yaml]\n')
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'still registered'):
            finish_migration.finish(self.root, 'example')
        self.assertEqual(self.snapshot(), before)

    def test_path_traversal_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Invalid service'):
            finish_migration.finish(self.root, '../secrets')
