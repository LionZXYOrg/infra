"""Simlock gateway uses authenticated public TLS ingress without emulator privileges."""
from pathlib import Path
import re
import shutil
import subprocess
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]


class SimlockGatewayTests(unittest.TestCase):
    def load(self, path):
        return [d for d in yaml.safe_load_all((ROOT / path).read_text()) if d]

    def test_gateway_and_worker_install_the_same_pinned_release(self):
        pod = self.load('apps/simlock/deployment.yaml')[0]['spec']['template']['spec']
        install = pod['initContainers'][0]['args'][0]
        package = install.split()[-1]
        self.assertRegex(package, r'^simlock@\d+\.\d+\.\d+$')
        instructions = (ROOT / 'README.md').read_text()
        self.assertEqual(re.findall(r'^npm install -g (simlock@\S+)$', instructions, re.M), [package])

    @unittest.skipUnless(shutil.which('docker'), 'Docker required for the gateway upgrade fixture')
    def test_gateway_upgrade_serves_its_health_probe_and_keeps_the_worker_token(self):
        pod = self.load('apps/simlock/deployment.yaml')[0]['spec']['template']['spec']
        installer, gateway = pod['initContainers'][0], pod['containers'][0]
        self.assertEqual(installer['image'], gateway['image'])
        version = installer['args'][0].split()[-1].split('@')[1]
        startup = self.load('apps/simlock/configmap.yaml')[0]['data']['start.sh']
        probe = gateway['readinessProbe']['httpGet']
        port = next(p['containerPort'] for p in gateway['ports'] if p['name'] == probe['port'])
        script = '''
            npm install --prefix /previous --cache /previous/cache --no-audit --no-fund --omit=dev simlock@0.6.0 >/dev/null
            old=/previous/node_modules/.bin/simlock
            "$old" --gateway daemon start >/dev/null
            "$old" --gateway token create --role worker --label upgrade-fixture >/dev/null
            "$old" --gateway daemon stop >/dev/null
            sh -ec "$1" >/dev/null
            sh -ec "$2" >/tmp/gateway.log 2>&1 &
            node --input-type=module -e '
                for (let i = 0; i < 60; i++) {
                    try {
                        const r = await fetch(process.argv[1], {signal: AbortSignal.timeout(1000)})
                        if (r.ok && (await r.json()).ok === true) process.exit(0)
                    } catch {}
                    await new Promise(resolve => setTimeout(resolve, 1000))
                }
                process.exit(1)' "$3"
            simlock --gateway daemon status --json | node -e '
                const {daemon} = JSON.parse(require("node:fs").readFileSync(0, "utf8"))
                process.exit(daemon.mode === "gateway" && daemon.version === process.argv[1] ? 0 : 1)' "$4"
            simlock --gateway token list | node -e '
                const {tokens} = JSON.parse(require("node:fs").readFileSync(0, "utf8"))
                process.exit(tokens.some(t => t.label === "upgrade-fixture" && t.role === "worker") ? 0 : 1)'
        '''
        result = subprocess.run([
            'docker', 'run', '--rm', '--read-only', '--user', '10001:10001',
            '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
            '--tmpfs', '/previous:exec,uid=10001,gid=10001', '--tmpfs', '/data:uid=10001,gid=10001',
            '--tmpfs', '/opt/simlock:exec,size=256m,uid=10001,gid=10001', '--tmpfs', '/tmp:size=128m,mode=1777',
            '--env', 'HOME=/data', '--env', 'SIMLOCK_HOME=/data', '--env', 'npm_config_cache=/tmp/npm-cache',
            '--env', 'PATH=/opt/simlock/node_modules/.bin:/usr/local/bin:/usr/bin:/bin',
            gateway['image'], 'sh', '-ec', script, 'upgrade-fixture', installer['args'][0], startup,
            f'http://127.0.0.1:{port}{probe["path"]}', version,
        ], capture_output=True, text=True, timeout=300)
        self.assertEqual(result.returncode, 0, 'Disposable Simlock gateway upgrade failed')

    def test_gateway_has_persistent_state_without_emulator_devices(self):
        deployment = self.load('apps/simlock/deployment.yaml')[0]
        self.assertEqual(deployment['spec']['replicas'], 1)
        pod = deployment['spec']['template']['spec']
        self.assertFalse(pod['automountServiceAccountToken'])
        self.assertTrue(pod['securityContext']['runAsNonRoot'])
        self.assertNotIn('hostNetwork', pod)
        self.assertNotIn('nodeSelector', pod)
        self.assertEqual({v['name'] for v in pod['volumes']},
                         {'state', 'package', 'configuration', 'tmp'})
        self.assertEqual(next(v for v in pod['volumes'] if v['name'] == 'state')['persistentVolumeClaim']['claimName'],
                         'simlock-gateway-state')
        container = pod['containers'][0]
        self.assertFalse(container['securityContext']['allowPrivilegeEscalation'])
        self.assertTrue(container['securityContext']['readOnlyRootFilesystem'])
        self.assertIn('SIMLOCK_HOME', {e['name'] for e in container['env']})
        self.assertEqual(container['ports'][0]['containerPort'], 4700)
        for workload in pod['initContainers'] + pod['containers']:
            requests = workload['resources']['requests']
            self.assertEqual(requests['cpu'], '50m')
            self.assertNotIn('memory', requests)

    def test_public_https_and_worker_outbound_uplink(self):
        route = self.load('apps/simlock/ingressroute.yaml')[0]
        self.assertEqual(route['kind'], 'IngressRoute')
        self.assertEqual(route['spec']['tls'], {})
        self.assertNotIn('middleware.yaml',
                         self.load('apps/simlock/kustomization.yaml')[0]['resources'])
        self.assertFalse((ROOT / 'apps/simlock/middleware.yaml').exists())
        self.assertEqual(route['spec']['entryPoints'], ['websecure'])
        self.assertEqual(route['spec']['routes'][0]['match'],
                         'Host(`simlock.kulikov.uk`)')
        self.assertNotIn('middlewares', route['spec']['routes'][0])
        self.assertEqual(route['spec']['routes'][0]['services'][0]['port'], 4700)
        self.assertEqual(self.load('apps/simlock/service.yaml')[0]['spec']['type'],
                         'ClusterIP')

    def test_flux_and_namespace_are_registered(self):
        root = self.load('clusters/home/kustomization.yaml')[0]['resources']
        nsroot = self.load('infrastructure/namespaces/kustomization.yaml')[0]['resources']
        self.assertIn('simlock.yaml', root)
        self.assertIn('simlock.yaml', nsroot)
        flux = self.load('clusters/home/simlock.yaml')[0]['spec']
        self.assertEqual(flux['path'], './apps/simlock')
        self.assertTrue({'infra-namespaces', 'infra-tls'}.issubset(
            {x['name'] for x in flux['dependsOn']}))
