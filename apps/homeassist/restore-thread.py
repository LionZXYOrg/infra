#!/usr/bin/env python3
"""Join the preferred saved Thread network without generating or replacing keys."""
import argparse
import json
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener


ATTACHED = {'child', 'router', 'leader'}
STATES = ATTACHED | {'disabled', 'detached'}
READY = Path('/tmp/thread-network-ready')


class RecoveryError(RuntimeError):
    pass


class Unavailable(RecoveryError):
    pass


class APIError(RecoveryError):
    def __init__(self, status):
        super().__init__(f'The local border router rejected a request ({status}). Response contents are suppressed.')
        self.status = status


def parse_dataset(tlv):
    if not isinstance(tlv, str) or not re.fullmatch(r'(?:[0-9a-fA-F]{2}){1,254}', tlv):
        raise RecoveryError('The saved Thread network is invalid. Contents are suppressed.')
    raw = bytes.fromhex(tlv)
    fields = {}
    offset = 0
    while offset < len(raw):
        if offset + 2 > len(raw):
            raise RecoveryError('The saved Thread network is incomplete.')
        tag, length = raw[offset:offset + 2]
        offset += 2
        if tag in fields or offset + length > len(raw):
            raise RecoveryError('The saved Thread network is incomplete.')
        fields[tag] = raw[offset:offset + length]
        offset += length
    for tag, length in {0: 3, 1: 2, 2: 8, 5: 16, 7: 8, 14: 8}.items():
        if len(fields.get(tag, b'')) != length:
            raise RecoveryError('The saved Thread network is incomplete.')
    if not 1 <= len(fields.get(3, b'')) <= 16 or len(fields.get(12, b'')) not in {3, 4}:
        raise RecoveryError('The saved Thread network is incomplete.')
    return fields


def network_identity(tlv):
    fields = parse_dataset(tlv)
    # A live router can carry newer timestamps/channel data. Never roll them back.
    return tuple(fields[tag] for tag in (2, 5, 7))


def preferred_network(path):
    try:
        data = json.loads(path.read_text())['data']
        entries = [d for d in data['datasets'] if d['id'] == data['preferred_dataset']]
        if len(entries) != 1:
            raise ValueError
        tlv = entries[0]['tlv']
    except (OSError, ValueError, KeyError, TypeError):
        raise RecoveryError('The preferred saved Thread network is unavailable. Import its credentials in Home Assistant first.') from None
    parse_dataset(tlv)
    return tlv


class Router:
    def __init__(self, port=8081):
        if not 1 <= port <= 65535:
            raise RecoveryError('Invalid local border router port.')
        self.url = f'http://127.0.0.1:{port}'
        self.opener = build_opener(ProxyHandler({}))

    def request(self, path, *, data=None, text=False, headers=None):
        content_type = 'text/plain' if text else 'application/json'
        request = Request(self.url + path, data=data, headers={
            'Accept': content_type, 'Content-Type': content_type, **(headers or {})})
        if data is not None:
            request.method = 'PUT'
        try:
            with self.opener.open(request, timeout=10) as response:
                return response.status, response.read(8192)
        except HTTPError as error:
            status = error.code
            error.close()
            raise APIError(status) from None
        except (OSError, URLError):
            raise Unavailable('The local border router is unavailable.') from None

    def dataset(self):
        status, raw = self.request('/node/dataset/active', text=True)
        if status == 204:
            return None
        try:
            tlv = raw.decode().strip()
        except UnicodeError:
            raise RecoveryError('The local border router returned an invalid saved network.') from None
        parse_dataset(tlv)
        return tlv

    def state(self):
        _, raw = self.request('/node/state')
        try:
            state = json.loads(raw)
            if state not in STATES:
                raise ValueError
            return state
        except (ValueError, TypeError):
            raise RecoveryError('The local border router returned an invalid state.') from None


def restore_network(path, api):
    saved = preferred_network(path)
    current = api.dataset()
    if current is None:
        try:
            api.request('/node/dataset/active', data=saved.encode(), text=True,
                        headers={'If-None-Match': '*'})
        except APIError as error:
            if error.status not in {409, 412}:
                raise
        current = api.dataset()
    if current is None or network_identity(current) != network_identity(saved):
        raise RecoveryError('The border router uses a different saved network. No network was replaced.')
    if api.state() == 'disabled':
        api.request('/node/state', data=json.dumps('enable').encode())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--store', type=Path, default=Path('/restore/thread.datasets'))
    parser.add_argument('--port', type=int, default=8081)
    probes = parser.add_mutually_exclusive_group()
    probes.add_argument('--ready', action='store_true')
    probes.add_argument('--alive', action='store_true')
    args = parser.parse_args()
    api = Router(args.port)
    if args.ready or args.alive:
        state = api.state()
        return 0 if args.alive or (READY.is_file() and state in ATTACHED) else 1
    READY.unlink(missing_ok=True)
    preferred_network(args.store)  # Refuse to create a new network when recovery data is missing.
    deadline = time.monotonic() + 180
    announced = False
    while True:
        try:
            restore_network(args.store, api)
            state = api.state()
            if state in ATTACHED:
                READY.touch(mode=0o600)
                if not announced:
                    print('The preferred saved Thread network is active.', flush=True)
                    announced = True
            else:
                READY.unlink(missing_ok=True)
                if not announced and time.monotonic() >= deadline:
                    raise RecoveryError('The border router did not join the saved Thread network.')
        except Unavailable:
            READY.unlink(missing_ok=True)
            if time.monotonic() >= deadline:
                raise
        time.sleep(10)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except RecoveryError as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
