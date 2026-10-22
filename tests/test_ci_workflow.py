"""Keep the PR workflow runnable without private access or write credentials."""
import copy
from pathlib import Path
import re
import unittest
import yaml


class WorkflowLoader(yaml.SafeLoader):
    yaml_implicit_resolvers = copy.deepcopy(yaml.SafeLoader.yaml_implicit_resolvers)


for key, resolvers in WorkflowLoader.yaml_implicit_resolvers.items():
    WorkflowLoader.yaml_implicit_resolvers[key] = [(tag, regex) for tag, regex in resolvers
                                                  if tag != 'tag:yaml.org,2002:bool']
WorkflowLoader.add_implicit_resolver('tag:yaml.org,2002:bool', re.compile(r'^(?:true|false)$'), ['t', 'f'])


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.text = (root / '.github/workflows/pr-checks.yaml').read_text()
        self.workflow = yaml.load(self.text, Loader=WorkflowLoader)

    def test_pr_push_and_dispatch_have_no_path_filter_or_privileged_trigger(self):
        triggers = self.workflow['on']
        self.assertEqual(set(triggers), {'pull_request', 'push', 'workflow_dispatch'})
        self.assertEqual(triggers['push']['branches'], ['master'])
        self.assertFalse(triggers['pull_request'])
        self.assertNotIn('pull_request_target', self.text)

    def test_permissions_runners_and_dependencies(self):
        self.assertEqual(self.workflow['permissions'], {'contents': 'read'})
        self.assertEqual(set(self.workflow['jobs']), {'validate', 'deployment'})
        self.assertEqual(self.workflow['jobs']['deployment']['needs'], 'validate')
        self.assertEqual(self.workflow['jobs']['validate']['name'], 'Validate')
        self.assertEqual(self.workflow['jobs']['deployment']['name'], 'Deployment')
        for job in self.workflow['jobs'].values():
            self.assertEqual(job['runs-on'], 'ubuntu-26.04')
            self.assertLessEqual(job['timeout-minutes'], 40)
        self.assertNotRegex(self.text, r'\$\{\{\s*secrets[.\[]')

    def test_public_checkout_has_no_persistent_credentials_or_submodules(self):
        for job in self.workflow['jobs'].values():
            checkout = next(step for step in job['steps']
                            if step.get('uses', '').startswith('actions/checkout@'))
            self.assertIs(checkout['with']['persist-credentials'], False)
            self.assertIs(checkout['with']['submodules'], False)
            self.assertEqual(checkout['with']['fetch-depth'], 0)
            self.assertNotIn('ref', checkout['with'])
            for step in job['steps']:
                if 'uses' in step:
                    self.assertRegex(step['uses'], r'^actions/[a-z-]+@[0-9a-f]{40}$')

    def test_validation_and_full_deployment_are_required_steps(self):
        validation = '\n'.join(step.get('run', '') for step in self.workflow['jobs']['validate']['steps'])
        for operation in ('unittest discover', 'scripts/check.sh', 'gitleaks git', '--redact=100'):
            self.assertIn(operation, validation)
        steps = self.workflow['jobs']['deployment']['steps']
        setup = '\n'.join(step.get('run', '') for step in steps)
        self.assertIn('sudo swapoff -a', setup)
        self.assertIn('tests/ci/run.sh --disposable', setup)
        upload = next(step for step in steps if step.get('uses', '').startswith('actions/upload-artifact@'))
        self.assertEqual(upload['if'], 'always()')
        self.assertEqual(upload['with']['path'], '${{ runner.temp }}/infra-ci-report')
        self.assertLessEqual(upload['with']['retention-days'], 7)


if __name__ == '__main__':
    unittest.main()
