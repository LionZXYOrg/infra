#!/usr/bin/env python3
"""Validate inactive migration definitions; optionally render checksum-pinned charts.
No Kubernetes API calls, production credentials, or private module are needed.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import urllib.request
import yaml

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {'pihole', 'n8n', 'litnetbot', 'imgur', 'samba', 'switchstatic', 'tabby',
            'aiobashim', 'qbittorrentbot'}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def load(path):
    try:
        return yaml.safe_load(path.read_text())
    except yaml.YAMLError:
        raise ValueError(f'Invalid YAML: {path.name}; contents suppressed') from None


def contracts(root=ROOT):
    return [(path.stem, load(path)) for path in sorted((root / 'tests/migrations').glob('*.yaml'))]


def definitions(root, name):
    raw = subprocess.check_output(['kubectl', 'kustomize', str(root / 'apps' / name)])
    return [d for d in yaml.safe_load_all(raw) if d]


def check_preparation(root, name, contract, objects):
    require(name not in EXCLUDED, f'{name}: explicitly excluded service')
    require(contract['source']['commit'] == 'ccfc5ecd5433fa0d7fe3844f167612b8ed6e2297',
            f'{name}: snapshot provenance changed; review inventory')
    flux = load(root / 'clusters/home' / f'{name}.yaml')
    require(flux['spec'].get('suspend') is True, f'{name}: preparation must stay suspended')
    require(flux['spec']['path'] == f'./apps/{name}', f'{name}: wrong Flux path')
    require({'infra-tls', 'cluster-secrets'}.issubset({x['name'] for x in flux['spec']['dependsOn']}),
            f'{name}: missing production dependencies')
    require(f'{name}.yaml' in load(root / 'clusters/home/kustomization.yaml')['resources'],
            f'{name}: Flux registration missing')
    require(f'{name}.yaml' in load(root / 'infrastructure/namespaces/kustomization.yaml')['resources'],
            f'{name}: namespace registration missing')
    hrs = [o for o in objects if o['kind'] == 'HelmRelease']
    require(bool(hrs), f'{name}: no Helm release')
    require(all(h['spec'].get('suspend') is True for h in hrs), f'{name}: release is not suspended')
    require({h['metadata']['name'] for h in hrs} == set(contract['charts']), f'{name}: chart lock mismatch')
    health = {(x['kind'], x['name']) for x in flux['spec']['healthChecks']}
    require({('HelmRelease', h['metadata']['name']) for h in hrs} == health, f'{name}: incomplete Flux health checks')
    for hr in hrs:
        lock = contract['charts'][hr['metadata']['name']]
        require(re.fullmatch('[a-f0-9]{64}', lock['sha256']) is not None, f'{name}: chart checksum missing')
        spec = hr['spec']['chart']['spec']
        require(spec['version'] == lock['version'] and spec['chart'] == lock['chart'], f'{name}: chart pin mismatch')
    for o in objects:
        require(o['kind'] != 'Secret', f'{name}: Secret must live in private module')
        if o['kind'] == 'PersistentVolumeClaim':
            require(o['metadata'].get('annotations', {}).get('kustomize.toolkit.fluxcd.io/prune') == 'disabled',
                    f'{name}: PVC is not protected from Flux pruning')
        if o['kind'] == 'IngressRoute':
            require(o['spec']['entryPoints'] == ['websecure'] and 'tls' in o['spec'], f'{name}: route lacks TLS')
            require(f"Host(`{name}.test.kulikov.uk`)" == o['spec']['routes'][0]['match'],
                    f'{name}: preparation must use the staging host')
    require((root / 'apps' / name / 'README.md').is_file(), f'{name}: migration instructions missing')


def render(root, name, contract, objects, cache):
    rendered = [o for o in objects if o['kind'] not in {'HelmRelease', 'HelmRepository'}]
    for hr in (o for o in objects if o['kind'] == 'HelmRelease'):
        lock = contract['charts'][hr['metadata']['name']]
        archive = cache / (lock['sha256'] + '.tgz')
        if not archive.exists():
            require(lock['url'].startswith('https://'), 'Only HTTPS chart archives are allowed')
            with urllib.request.urlopen(lock['url'], timeout=60) as response:
                archive.write_bytes(response.read())
        require(hashlib.sha256(archive.read_bytes()).hexdigest() == lock['sha256'], f'{name}: chart checksum mismatch')
        with tempfile.TemporaryDirectory() as work:
            values = Path(work) / 'values.yaml'
            values.write_text(yaml.safe_dump(hr['spec'].get('values', {})))
            result = subprocess.run(['helm', 'template', hr['spec']['releaseName'], str(archive),
                                     '--namespace', name, '--kube-version', '1.35.9', '-f', str(values)],
                                    capture_output=True, timeout=60)
            require(result.returncode == 0, f'{name}/{hr["metadata"]["name"]}: Helm render failed: ' + result.stderr.decode())
            rendered.extend(o for o in yaml.safe_load_all(result.stdout) if o)
    return rendered


def workload_objects(objects):
    return [o for o in objects if o['kind'] in {'Deployment', 'StatefulSet'}]


def check_workloads(name, contract, objects):
    require(not any(o['kind'] == 'Secret' for o in objects), f'{name}: chart generated an unmanaged Secret')
    workloads = workload_objects(objects)
    require(len(workloads) == contract['workloads'], f'{name}: missing or extra workloads')
    pvcs = {o['metadata']['name'] for o in objects if o['kind'] == 'PersistentVolumeClaim'}
    secrets = set()
    mounts = set()
    images = set()
    for obj in workloads:
        spec = obj['spec']
        require(spec.get('replicas') == 1, f'{name}: single-writer workload must have one replica')
        if obj['kind'] == 'Deployment':
            require(spec.get('strategy', {}).get('type') == 'Recreate', f'{name}: overlapping writers on update')
        pod = spec['template']['spec']
        require(not pod.get('hostNetwork') and not pod.get('hostPID'), f'{name}: host isolation removed')
        for vol in pod.get('volumes', []):
            require('hostPath' not in vol, f'{name}: legacy hostPath is not portable')
            if 'secret' in vol:
                secrets.add(vol['secret']['secretName'])
            if 'persistentVolumeClaim' in vol:
                require(vol['persistentVolumeClaim']['claimName'] in pvcs, f'{name}: dangling PVC reference')
        for c in pod.get('containers', []) + pod.get('initContainers', []):
            image = c['image']
            images.add(image)
            require('@sha256:' in image or image in contract.get('pendingImages', []), f'{name}: image is not immutable')
            require(bool(c.get('resources', {}).get('requests')), f'{name}: resource requests missing')
            for env in c.get('env', []):
                if 'secretKeyRef' in env.get('valueFrom', {}):
                    secrets.add(env['valueFrom']['secretKeyRef']['name'])
                if any(k in env['name'].upper() for k in ('PASSWORD', 'TOKEN', 'SECRET')):
                    require('value' not in env, f'{name}: credential inline in workload environment')
            for env in c.get('envFrom', []):
                if 'secretRef' in env:
                    secrets.add(env['secretRef']['name'])
            for mount in c.get('volumeMounts', []):
                mounts.add(mount['mountPath'].rstrip('/'))
            if c in pod.get('containers', []):
                require('readinessProbe' in c and 'livenessProbe' in c, f'{name}: health probes missing')
    require(secrets == set(contract['secrets']), f'{name}: unexpected or missing Secret references')
    require(set(contract['mounts']).issubset(mounts), f'{name}: legacy data mount changed')
    require(set(contract.get('pendingImages', [])).issubset(images), f'{name}: invalid pending image declaration')
    for svc in (o for o in objects if o['kind'] == 'Service' and o.get('spec', {}).get('selector')):
        matches = [w for w in workloads if all(w['spec']['template']['metadata']['labels'].get(k) == v
                   for k, v in svc['spec']['selector'].items())]
        require(bool(matches), f'{name}: Service selector does not select a workload')
        for port in svc['spec']['ports']:
            target = port.get('targetPort', port['port'])
            require(any(target in {p.get('name'), p.get('containerPort')} for w in matches
                        for c in w['spec']['template']['spec']['containers'] for p in c.get('ports', [])),
                    f'{name}: Service target port missing')
    for route in (o for o in objects if o['kind'] == 'IngressRoute'):
        for dest in route['spec']['routes'][0]['services']:
            require(any(s['kind'] == 'Service' and s['metadata']['name'] == dest['name'] and
                        any(p['port'] == dest['port'] for p in s['spec']['ports']) for s in objects),
                    f'{name}: TLS route destination missing')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--render', action='store_true')
    parser.add_argument('--output', type=Path, help='Write public rendered resources for inspection')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as work:
        cache = Path(work)
        for name, contract in contracts():
            objects = definitions(ROOT, name)
            check_preparation(ROOT, name, contract, objects)
            if args.render:
                objects = render(ROOT, name, contract, objects, cache)
                check_workloads(name, contract, objects)
                if args.output:
                    args.output.mkdir(parents=True, exist_ok=True)
                    (args.output / f'{name}.yaml').write_text(yaml.safe_dump_all(objects, sort_keys=False))
            print(f'PASS: {name} preparation' + (' and rendered workload contract' if args.render else ''))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        raise SystemExit(str(error)) from None
