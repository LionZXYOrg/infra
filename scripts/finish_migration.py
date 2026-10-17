#!/usr/bin/env python3
"""Prepare activation and remove completed restore files; never contact a cluster."""
import argparse
from pathlib import Path
import re

import yaml

import migration_check as migration

ROOT = Path(__file__).resolve().parents[1]


def finish(root, name):
    migration.require(re.fullmatch(r'[a-z0-9][a-z0-9-]*', name), 'Invalid service name')
    application = root / 'apps' / name
    contract_path = root / 'tests/migrations' / f'{name}.yaml'
    contract = migration.load(contract_path)
    migration.require(contract.get('phase', 'prepared') == 'prepared', 'Migration is already complete')
    objects = migration.definitions(root, name)
    migration.check_preparation(root, name, contract, objects)
    releases = sorted(application.glob('helmrelease-*.yaml'))
    for pending in contract.get('pendingImages', []):
        tag = pending.rsplit(':', 1)[-1]
        migration.require(not any(tag in path.read_text() for path in releases),
                          'Publish the application image and replace its pending tag with a digest first')
    helpers = sorted(application.glob('restore*.yaml'))
    index = migration.load(application / 'kustomization.yaml')
    migration.require(not {path.name for path in helpers} & set(index['resources']),
                      'A restore helper is still registered in the application')
    cleanup = [application / 'MIGRATION.md', *helpers]
    paths = [contract_path, root / 'clusters/home' / f'{name}.yaml', *releases, *cleanup]
    migration.require(all(not path.is_symlink() for path in paths), 'Refusing a symlink during cleanup')
    updates = {}
    contract['phase'] = 'active'
    contract.pop('pendingImages', None)
    updates[contract_path] = yaml.safe_dump(contract, sort_keys=False)
    for path in [root / 'clusters/home' / f'{name}.yaml', *releases]:
        document = migration.load(path)
        document['spec']['suspend'] = False
        updates[path] = yaml.safe_dump(document, sort_keys=False)
    for path, text in updates.items():
        path.write_text(text)
    for path in cleanup:
        path.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('service')
    args = parser.parse_args()
    finish(ROOT, args.service)
    print('Prepared activation and removed temporary restore files. Review and commit the Git changes.')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError) as error:
        raise SystemExit(str(error)) from None
