"""Keep authorization reviews explicit and protocol request IDs unique."""
import json
import unittest
from unittest.mock import Mock

from ci.mcp import Client, MCPFailure, verify_permissions


class ProtocolTests(unittest.TestCase):
    def test_request_ids_are_unique_and_notifications_have_no_id(self):
        # Exercise request encoding without opening a socket or using credentials.
        client = Client.__new__(Client)
        client.url = 'https://192.0.2.10:8443/mcp'
        client.authorization = 'Basic disposable-fixture'
        client.session = ''
        client.request_id = 0
        response = Mock(status=202)
        response.headers = {}
        response.read.return_value = b''
        opened = Mock()
        opened.__enter__ = Mock(return_value=response)
        opened.__exit__ = Mock(return_value=False)
        client.opener = Mock()
        client.opener.open.return_value = opened
        client.request('initialize')
        client.request('notifications/initialized', request_id=None)
        client.request('tools/list')
        messages = [json.loads(call.args[0].data) for call in client.opener.open.call_args_list]
        self.assertEqual(messages[0]['id'], 1)
        self.assertNotIn('id', messages[1])
        self.assertEqual(messages[2]['id'], 2)

    def test_authorization_reviews_use_the_exact_service_account_and_groups(self):
        runner = Mock()
        def kube(args, *, data):
            self.assertEqual(args, ['create', '--raw', '/apis/authorization.k8s.io/v1/subjectaccessreviews', '-f', '-'])
            review = json.loads(data)
            self.assertEqual(review['kind'], 'SubjectAccessReview')
            self.assertEqual(review['spec']['user'], 'system:serviceaccount:k3s-mcp:k3s-mcp')
            self.assertIn('system:serviceaccounts:k3s-mcp', review['spec']['groups'])
            attributes = review['spec']['resourceAttributes']
            allowed = attributes['verb'] in ('get', 'list') and attributes['resource'] in ('pods', 'nodes')
            return json.dumps({'status': {'allowed': allowed}}).encode()
        runner.kube.side_effect = kube
        verify_permissions(runner)
        self.assertEqual(runner.kube.call_count, 8)

    def test_unexpected_write_permission_fails_the_test(self):
        runner = Mock()
        runner.kube.return_value = b'{"status":{"allowed":true}}'
        with self.assertRaises(MCPFailure) as caught:
            verify_permissions(runner)
        self.assertEqual(caught.exception.assertion, 'rbac-get-secrets')

    def test_unavailable_authorization_api_cannot_pass_as_a_denial(self):
        runner = Mock()
        runner.kube.side_effect = RuntimeError('fixture failure')
        with self.assertRaises(MCPFailure) as caught:
            verify_permissions(runner)
        self.assertEqual(caught.exception.assertion, 'kubernetes-authorization-review')


if __name__ == '__main__':
    unittest.main()
