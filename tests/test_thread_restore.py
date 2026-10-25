"""Restore only the preferred saved Thread network, using a local fake OTBR API."""
import importlib.util
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest

PATH = Path(__file__).resolve().parents[1] / 'apps/homeassist/restore-thread.py'
spec = importlib.util.spec_from_file_location('thread_restore', PATH)
restore = importlib.util.module_from_spec(spec)
spec.loader.exec_module(restore)


def dataset(key=bytes(range(16)), timestamp=1):
    fields = {0: b'\0\0\x0f', 1: b'\x12\x34', 2: bytes(range(8)), 3: b'isolated-test',
              4: bytes(range(16)), 5: key, 7: b'\xfd' + bytes(7),
              12: b'\x02\xa0\xff\xf8', 14: timestamp.to_bytes(8, 'big')}
    return b''.join(bytes([tag, len(value)]) + value for tag, value in fields.items()).hex()


class ThreadRestoreTests(unittest.TestCase):
    def setUp(self):
        self.work = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.saved = dataset()
        self.store = self.work / 'thread.datasets'
        self.store.write_text(json.dumps({'data': {'preferred_dataset': 'existing', 'datasets': [
            {'id': 'existing', 'tlv': self.saved}, {'id': 'other', 'tlv': dataset(bytes(reversed(range(16))))}]}}))
        self.current = None
        self.state = 'disabled'
        self.writes = []
        self.conflict = False
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def respond(self, code, raw=b''):
                self.send_response(code)
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                if self.path == '/node/dataset/active':
                    self.respond(204 if owner.current is None else 200,
                                 owner.current.encode() if owner.current else b'')
                elif self.path == '/node/state':
                    self.respond(200, json.dumps(owner.state).encode())
                else:
                    self.respond(404)

            def do_PUT(self):
                raw = self.rfile.read(int(self.headers['Content-Length']))
                owner.writes.append((self.path, dict(self.headers), raw))
                if self.path == '/node/dataset/active':
                    if owner.conflict:
                        owner.current = owner.saved
                        self.respond(412)
                    else:
                        owner.current = raw.decode()
                        self.respond(201)
                elif self.path == '/node/state':
                    if json.loads(raw) == 'enable':
                        owner.state = 'leader'
                        self.respond(200)
                    else:
                        self.respond(400)
                else:
                    self.respond(404)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.api = restore.Router(self.server.server_port)

    def test_empty_router_receives_exact_saved_preferred_network(self):
        restore.restore_network(self.store, self.api)
        self.assertEqual(self.current, self.saved)
        self.assertEqual(self.state, 'leader')
        path, headers, body = self.writes[0]
        self.assertEqual(path, '/node/dataset/active')
        self.assertEqual(headers['Content-Type'], 'text/plain')
        self.assertEqual(headers['If-None-Match'], '*')
        self.assertEqual(body, self.saved.encode())

    def test_existing_network_is_reused_without_changes_on_repeat(self):
        self.current = dataset(timestamp=2)
        self.state = 'router'
        restore.restore_network(self.store, self.api)
        restore.restore_network(self.store, self.api)
        self.assertEqual(self.writes, [])

    def test_different_network_is_never_overwritten_or_disabled(self):
        self.current = dataset(bytes(reversed(range(16))))
        self.state = 'router'
        with self.assertRaisesRegex(restore.RecoveryError, 'different') as caught:
            restore.restore_network(self.store, self.api)
        self.assertNotIn(self.saved, str(caught.exception))
        self.assertEqual(self.writes, [])
        self.assertEqual(self.state, 'router')

    def test_missing_preference_or_incomplete_dataset_never_changes_router(self):
        for value in ({'preferred_dataset': None, 'datasets': []},
                      {'preferred_dataset': 'existing', 'datasets': [{'id': 'existing', 'tlv': '0011'}]},
                      {'preferred_dataset': 'existing', 'datasets': [{'id': 'existing', 'tlv': 'not-hex'}]}):
            with self.subTest(value=value):
                self.store.write_text(json.dumps({'data': value}))
                with self.assertRaises(restore.RecoveryError):
                    restore.restore_network(self.store, self.api)
                self.assertEqual(self.writes, [])

    def test_create_race_reuses_matching_network(self):
        self.conflict = True
        restore.restore_network(self.store, self.api)
        self.assertEqual(self.current, self.saved)
        self.assertEqual(self.state, 'leader')

    def test_newer_timestamp_keeps_same_identity(self):
        self.assertEqual(restore.network_identity(dataset()), restore.network_identity(dataset(timestamp=3)))

    def test_reordered_fields_keep_same_identity(self):
        fields = restore.parse_dataset(dataset())
        reordered = b''.join(bytes([tag, len(value)]) + value for tag, value in reversed(fields.items())).hex()
        self.assertEqual(restore.network_identity(dataset()), restore.network_identity(reordered))

    def test_router_http_errors_never_include_response_contents(self):
        try:
            self.api.request('/does-not-exist', data=self.saved.encode(), text=True)
        except restore.APIError as error:
            self.assertEqual(error.status, 404)
            self.assertNotIn(self.saved, str(error))
        else:
            self.fail('An unsupported API request must fail')


if __name__ == '__main__':
    unittest.main()
