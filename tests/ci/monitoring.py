"""Monitoring assertions used only inside the guarded disposable deployment."""
import json
import math
from pathlib import Path
import time
from urllib.parse import urlencode

import yaml


PROMETHEUS_PROXY = '/api/v1/namespaces/monitoring/services/http:monitoring-prometheus:9090/proxy'
GRAFANA_PROXY = '/api/v1/namespaces/monitoring/services/http:grafana:80/proxy'


def positive_vector(response: dict) -> bool:
    """Reject API errors, empty series and non-finite samples instead of passing silently."""
    if response.get('status') != 'success':
        return False
    data = response.get('data', {})
    if data.get('resultType') != 'vector' or not data.get('result'):
        return False
    try:
        values = [float(item['value'][1]) for item in data['result']]
        return all(math.isfinite(value) and value > 0 for value in values)
    except (KeyError, TypeError, ValueError, IndexError):
        return False


def wait_for(check, description: str, timeout: float = 240) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            if check():
                return
        except (RuntimeError, KeyError, ValueError, TypeError):
            pass  # Readiness may lag reconciliation. Never log raw HTTP or Kubernetes data.
        if time.monotonic() >= deadline:
            raise RuntimeError(f'Monitoring assertion failed: {description}; output suppressed')
        time.sleep(5)


def get_json(runner, path: str) -> dict:
    return json.loads(runner.kube(['get', '--raw', path, '--request-timeout=15s']))


def wait_query(runner, query: str, description: str) -> None:
    path = PROMETHEUS_PROXY + '/api/v1/query?' + urlencode({'query': query})
    wait_for(lambda: positive_vector(get_json(runner, path)), description)


def healthy_rules(response: dict, expected: set[str]) -> bool:
    if response.get('status') != 'success':
        return False
    rules = [rule for group in response.get('data', {}).get('groups', [])
             for rule in group.get('rules', [])]
    selected = [rule for rule in rules if rule.get('name') in expected]
    return ({rule['name'] for rule in selected} == expected
            and all(rule.get('health') == 'ok' and not rule.get('lastError') for rule in selected))


def private_services(objects: list[dict]) -> bool:
    return bool(objects) and all(obj.get('spec', {}).get('type') == 'ClusterIP'
                                 and not obj.get('spec', {}).get('externalIPs') for obj in objects)


def verify_monitoring(runner) -> None:
    """Wait for real data, not merely Running Pods or Ready HelmReleases."""
    runner.wait('helmrelease', 'monitoring', 'monitoring', '900s')
    runner.kube(['-n', 'monitoring', 'rollout', 'status', 'deployment/grafana', '--timeout=300s'])
    queries = {
        'node exporter': 'count(node_uname_info) > 0',
        'Kubernetes object state': 'count(kube_node_info) > 0',
        'cAdvisor container metrics': 'count(container_memory_working_set_bytes{container!="",container!="POD"}) > 0',
        'four Flux controller scrape targets': 'count(up{namespace="flux-system"} == 1) >= 4',
        'Flux resource-state metrics': 'count(gotk_resource_info{customresource_kind="Kustomization",exported_namespace="flux-system",name="flux-system",ready="True"}) > 0',
        'Traefik metrics': 'count(traefik_config_reloads_total) > 0',
        'certificate metrics': 'count(certmanager_certificate_expiration_timestamp_seconds) > 0',
    }
    for description, query in queries.items():
        wait_query(runner, query, description)
    # A discovered but broken scrape must not be hidden by the presence of some healthy data.
    def all_targets_healthy():
        response = get_json(runner, PROMETHEUS_PROXY + '/api/v1/targets')
        targets = response.get('data', {}).get('activeTargets', [])
        return (response.get('status') == 'success' and bool(targets)
                and all(target.get('health') == 'up' for target in targets))
    wait_for(all_targets_healthy, 'all discovered scrape targets are healthy')
    wait_for(lambda: get_json(runner, GRAFANA_PROXY + '/api/health').get('database') == 'ok',
             'Grafana database health')
    root = Path(__file__).resolve().parents[2]
    rule_doc = yaml.safe_load((root / 'infrastructure/monitoring/config/rules.yaml').read_text())
    expected = {rule['alert'] for group in rule_doc['spec']['groups'] for rule in group['rules']}
    wait_for(lambda: healthy_rules(get_json(runner, PROMETHEUS_PROXY + '/api/v1/rules'), expected),
             'all custom rules are loaded and evaluate successfully')
    claims = json.loads(runner.kube(['-n', 'monitoring', 'get', 'pvc', '-o', 'json']))['items']
    if len(claims) < 3 or any(item.get('status', {}).get('phase') != 'Bound' for item in claims):
        raise RuntimeError('Monitoring persistent volumes are not all Bound')
    services = json.loads(runner.kube(['-n', 'monitoring', 'get', 'services', '-o', 'json']))['items']
    if not private_services(services):
        raise RuntimeError('Monitoring unexpectedly exposes an external Service')
    routes = json.loads(runner.kube(['-n', 'monitoring', 'get',
                                   'ingresses.networking.k8s.io,ingressroutes.traefik.io',
                                   '-o', 'json']))['items']
    if routes:
        raise RuntimeError('Monitoring unexpectedly exposes an ingress route')
    dashboards = json.loads(runner.kube(['-n', 'monitoring', 'get', 'configmaps',
                                        '-l', 'grafana_dashboard=1', '-o', 'json']))['items']
    actual = [json.loads(item['data']['home-overview.json']) for item in dashboards
              if 'home-overview.json' in item.get('data', {})]
    expected_dashboard = json.loads((root / 'infrastructure/monitoring/config/home-overview.json').read_text())
    if actual != [expected_dashboard]:
        raise RuntimeError('Home dashboard ConfigMap was not reconciled correctly')


def verify_rule_tests(runner) -> None:
    """Run synthetic alert timelines with the deployed Prometheus version, without installing tools."""
    # Defence in depth: never create the test Job on the production cluster.
    nodes = json.loads(runner.kube(['get', 'nodes', '-o', 'json']))['items']
    if len(nodes) != 1 or nodes[0].get('metadata', {}).get('name') != 'ci-infra':
        raise RuntimeError('Refusing monitoring test Job outside the disposable ci-infra cluster')
    pods = json.loads(runner.kube(['-n', 'monitoring', 'get', 'pods',
                                 '-l', 'app.kubernetes.io/name=prometheus', '-o', 'json']))['items']
    images = {container['image'] for pod in pods for container in pod['spec']['containers']
              if container['name'] == 'prometheus'}
    if len(images) != 1:
        raise RuntimeError('Cannot identify the deployed Prometheus image for rule tests')
    root = Path(__file__).resolve().parents[2]
    rules = yaml.safe_load((root / 'infrastructure/monitoring/config/rules.yaml').read_text())['spec']
    name = 'monitoring-rule-tests'
    config = {'apiVersion': 'v1', 'kind': 'ConfigMap',
              'metadata': {'name': name, 'namespace': 'monitoring'},
              'data': {'rules.yaml': yaml.safe_dump(rules),
                       'rules.test.yaml': (root / 'tests/monitoring/rules.test.yaml').read_text()}}
    job = {'apiVersion': 'batch/v1', 'kind': 'Job',
           'metadata': {'name': name, 'namespace': 'monitoring'},
           'spec': {'backoffLimit': 0, 'activeDeadlineSeconds': 120,
                    'template': {'spec': {'restartPolicy': 'Never', 'automountServiceAccountToken': False,
                                         'securityContext': {'runAsNonRoot': True, 'runAsUser': 65534,
                                                             'fsGroup': 65534,
                                                             'seccompProfile': {'type': 'RuntimeDefault'}},
                                         'containers': [{'name': 'promtool', 'image': images.pop(),
                                                         'command': ['/bin/promtool', 'test', 'rules', '/tests/rules.test.yaml'],
                                                         'workingDir': '/tests',
                                                         'securityContext': {'allowPrivilegeEscalation': False,
                                                                             'readOnlyRootFilesystem': True,
                                                                             'capabilities': {'drop': ['ALL']}},
                                                         'resources': {'requests': {'cpu': '50m', 'memory': '64Mi'},
                                                                       'limits': {'memory': '256Mi'}},
                                                         'volumeMounts': [{'name': 'tests', 'mountPath': '/tests', 'readOnly': True},
                                                                          {'name': 'scratch', 'mountPath': '/tmp'}]}],
                                         'volumes': [{'name': 'tests', 'configMap': {'name': name}},
                                                     {'name': 'scratch', 'emptyDir': {'sizeLimit': '256Mi'}}]}}}}
    try:
        runner.kube(['apply', '-f', '-'], data=yaml.safe_dump_all([config, job]).encode())
        runner.kube(['-n', 'monitoring', 'wait', f'job/{name}', '--for=condition=Complete', '--timeout=150s'])
    finally:
        runner.kube(['-n', 'monitoring', 'delete', 'job,configmap', name,
                     '--ignore-not-found', '--wait=true', '--timeout=60s'])
