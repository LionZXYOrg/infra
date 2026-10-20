from pathlib import Path
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]


class BirthdayImagePublicationTests(unittest.TestCase):
    def setUp(self):
        self.text = (ROOT / '.github/workflows/birthday-reminder-image.yaml').read_text()
        self.workflow = yaml.safe_load(self.text)

    def test_only_a_manual_run_of_trusted_master_can_publish(self):
        triggers = self.workflow.get('on', self.workflow.get(True))
        self.assertEqual(triggers, {'workflow_dispatch': {}})
        job = self.workflow['jobs']['publish']
        self.assertEqual(job['if'], "github.repository == 'LionZXYOrg/infra' && github.ref == 'refs/heads/master'")
        self.assertEqual(self.workflow['permissions'], {'contents': 'read'})
        self.assertEqual(job['permissions'], {'contents': 'read', 'packages': 'write'})
        checkout = job['steps'][0]['with']
        self.assertEqual(checkout['ref'], '${{ github.sha }}')
        self.assertFalse(checkout['persist-credentials'])
        self.assertFalse(checkout['submodules'])

    def test_image_tests_run_before_the_temporary_registry_login(self):
        steps = self.workflow['jobs']['publish']['steps']
        test_step = next(index for index, step in enumerate(steps) if step.get('name') == 'Build and test the public source')
        publish_step = next(index for index, step in enumerate(steps) if step.get('name') == 'Publish the tested image')
        self.assertLess(test_step, publish_step)
        self.assertIn('--network none --entrypoint python', steps[test_step]['run'])
        self.assertIn('-m unittest discover -s tests -v', steps[test_step]['run'])
        self.assertIn('--platform linux/amd64', steps[test_step]['run'])
        self.assertNotIn('env', steps[test_step])
        self.assertEqual(steps[publish_step]['env'], {'GH_TOKEN': '${{ github.token }}'})
        self.assertIn('--password-stdin', steps[publish_step]['run'])
        self.assertIn('docker logout ghcr.io', steps[publish_step]['run'])
        self.assertNotIn('secrets.', self.text)
        for step in steps:
            if 'uses' in step:
                self.assertRegex(step['uses'], r'^[\w/-]+@[0-9a-f]{40}$')
            for prohibited in ['kubectl', 'kubeconfig', 'ssh ', 'secretctl']:
                self.assertNotIn(prohibited, step.get('run', '').lower())
