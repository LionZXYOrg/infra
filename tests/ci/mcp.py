"""Exercise the real HTTPS MCP endpoint with disposable credentials only."""
import base64
import json
import ssl
from urllib.error import HTTPError
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener


class MCPFailure(RuntimeError):
    def __init__(self, assertion, status=None):
        super().__init__('MCP assertion failed; response contents suppressed')
        self.assertion = assertion
        self.status = status


class Client:
    def __init__(self, address, ca, password):
        context = ssl.create_default_context(cadata=ca.decode())
        self.opener = build_opener(ProxyHandler({}), HTTPSHandler(context=context))
        self.url = f'https://{address}:8443/mcp'
        self.authorization = 'Basic ' + base64.b64encode(('ci-mcp:' + password).encode()).decode()
        self.session = ''

    def request(self, method, params=None, *, authorization=True, origin=None, request_id=1):
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream'}
        if authorization:
            headers['Authorization'] = self.authorization if authorization is True else authorization
        if origin:
            headers['Origin'] = origin
        if self.session:
            headers['Mcp-Session-Id'] = self.session
            headers['MCP-Protocol-Version'] = '2025-06-18'
        body = {'jsonrpc': '2.0', 'method': method, 'params': params or {}}
        if request_id is not None:
            body['id'] = request_id
        request = Request(self.url, data=json.dumps(body).encode(), headers=headers)
        try:
            with self.opener.open(request, timeout=30) as response:
                self.session = response.headers.get('Mcp-Session-Id', self.session)
                raw = response.read()
                status = response.status
        except HTTPError as error:
            return error.code, {}
        if not raw:
            return status, {}
        if raw.startswith(b'event:') or raw.startswith(b'data:'):
            messages = [line[5:].strip() for line in raw.splitlines() if line.startswith(b'data:')]
            raw = next((message for message in messages if json.loads(message).get('id') == request_id), b'{}')
        return status, json.loads(raw)


def assert_result(status, response, assertion):
    if status != 200 or 'result' not in response or response['result'].get('isError'):
        raise MCPFailure(assertion, status)
    return response['result']


def verify_endpoint(runner, fixture):
    root = json.loads(runner.kube(['-n', 'k3s-mcp', 'get', 'secret', 'mcp-ca', '-o', 'json']))
    client = Client(runner.node_ip, base64.b64decode(root['data']['tls.crt']), fixture.mcp_password)
    initialize = {'protocolVersion': '2025-06-18', 'capabilities': {},
                  'clientInfo': {'name': 'infra-ci', 'version': '1'}}
    for authorization, origin, expected in ((False, None, 401), ('Basic aW52YWxpZDppbnZhbGlk', None, 401),
                                           (True, 'https://untrusted.invalid', 403)):
        status, _ = client.request('initialize', initialize, authorization=authorization, origin=origin)
        if status != expected:
            raise MCPFailure('unauthorized-request', status)
    runner.passed_check('mcp-rejects-unauthorized-requests-and-origins')
    assert_result(*client.request('initialize', initialize, origin=f'https://{runner.node_ip}:8443'), 'initialize')
    status, _ = client.request('notifications/initialized', request_id=None)
    if status != 202:
        raise MCPFailure('initialized-notification', status)
    runner.passed_check('mcp-initialization')
    status, _ = client.request('tools/list', authorization=False)
    if status != 401:
        raise MCPFailure('session-authentication', status)
    tools = assert_result(*client.request('tools/list'), 'tools-list')['tools']
    if not tools or any(tool.get('annotations', {}).get('readOnlyHint') is not True for tool in tools):
        raise MCPFailure('tools-read-only')
    runner.passed_check('mcp-read-only-tools')
    pods = assert_result(*client.request('tools/call', {
        'name': 'pods_list_in_namespace', 'arguments': {'namespace': 'demo'}}), 'pod-list')
    if 'demo' not in json.dumps(pods):
        raise MCPFailure('pod-list-result')
    runner.passed_check('mcp-pod-read')
    for name, arguments in (
        ('resources_get', {'apiVersion': 'v1', 'kind': 'Secret', 'namespace': 'demo', 'name': 'isolated-probe'}),
        ('resources_delete', {'apiVersion': 'v1', 'kind': 'ConfigMap', 'namespace': 'demo', 'name': 'demo-page'}),
    ):
        status, response = client.request('tools/call', {'name': name, 'arguments': arguments})
        if status != 200 or not ('error' in response or response.get('result', {}).get('isError') is True):
            raise MCPFailure('denied-tool-' + name, status)
    runner.passed_check('mcp-secret-and-write-tool-denial')
    # Check authorization in Kubernetes independently of the server's tool filter.
    for verb, resource, subresource, namespace, allowed in (
        ('list', 'pods', None, 'demo', True), ('get', 'pods', 'log', 'demo', True),
        ('list', 'nodes', None, None, True), ('get', 'secrets', None, 'demo', False),
        ('create', 'pods', 'exec', 'demo', False), ('create', 'pods', None, 'demo', False),
        ('patch', 'deployments', None, 'demo', False), ('delete', 'configmaps', None, 'demo', False),
    ):
        attributes = {'verb': verb, 'resource': resource, 'group': 'apps' if resource == 'deployments' else ''}
        if subresource:
            attributes['subresource'] = subresource
        if namespace:
            attributes['namespace'] = namespace
        review = {'apiVersion': 'authorization.k8s.io/v1', 'kind': 'SelfSubjectAccessReview',
                  'spec': {'resourceAttributes': attributes}}
        response = json.loads(runner.kube([
            '--as=system:serviceaccount:k3s-mcp:k3s-mcp', '--as-group=system:authenticated',
            'create', '-f', '-', '-o', 'json'], data=json.dumps(review).encode()))
        if response.get('status', {}).get('allowed') is not allowed:
            raise MCPFailure('rbac-' + verb + '-' + resource)
