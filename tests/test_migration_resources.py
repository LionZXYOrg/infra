"""Check resource policy on real pinned chart output and storage helpers."""
from pathlib import Path
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import migration_check as migration


def containers(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {'containers', 'initContainers', 'ephemeralContainers'}:
                yield from child
            else:
                yield from containers(child)
    elif isinstance(value, list):
        for child in value:
            yield from containers(child)


class MigrationResourceTests(unittest.TestCase):
    def test_rendered_applications_and_restore_helpers_have_no_compute_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            for name, contract in migration.contracts():
                with self.subTest(service=name):
                    objects = migration.definitions(ROOT, name)
                    rendered = migration.render(ROOT, name, contract, objects, Path(directory))
                    migration.check_workloads(name, contract, rendered)
                    for helper in sorted((ROOT / 'apps' / name).glob('restore*.yaml')):
                        rendered.extend(doc for doc in yaml.safe_load_all(helper.read_text()) if doc)
                    workloads = list(containers(rendered))
                    self.assertTrue(workloads, 'No rendered containers were inspected')
                    for container in workloads:
                        with self.subTest(container=container['name']):
                            limits = container.get('resources', {}).get('limits') or {}
                            self.assertFalse({'cpu', 'memory'} & set(limits),
                                             'A chart or storage helper adds compute limits')
