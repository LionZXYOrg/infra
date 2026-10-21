"""Run the documented commands with generated archives and a faulty transport."""
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import textwrap
import tarfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class LocalContentShareCopyCommandTests(unittest.TestCase):
    @unittest.skipUnless((ROOT / 'apps/localcontentshare/MIGRATION.md').exists(), 'Migration completed')
    def test_only_a_complete_verified_archive_can_be_extracted(self):
        text = (ROOT / 'apps/localcontentshare/MIGRATION.md').read_text()
        commands = re.findall(r'```bash\n(.*?)\n```', text, re.S)
        self.assertEqual(len(commands), 1)
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            binary = work / 'bin'
            binary.mkdir()
            archive = work / 'fixture.tar'
            with tarfile.open(archive, 'w') as output:
                item = tarfile.TarInfo('fixture.txt')
                content = b'Generated restore fixture'
                item.size = len(content)
                output.addfile(item, io.BytesIO(content))
            ssh = binary / 'ssh'
            ssh.write_text('#!/usr/bin/env python3\n' + textwrap.dedent('''
                import os,sys
                from pathlib import Path
                if 'sudo -n tar ' in sys.argv[-1]:
                    data=Path(os.environ['FIXTURE_ARCHIVE']).read_bytes()
                    if os.environ['FIXTURE_FAULT']=='source-empty': data=b''
                    sys.stdout.buffer.write(data)
            '''))
            kubectl = binary / 'kubectl'
            kubectl.write_text('#!/usr/bin/env python3\n' + textwrap.dedent('''
                import hashlib,json,os,sys
                from pathlib import Path
                args=sys.argv[1:]
                work=Path(os.environ['FIXTURE_WORK'])
                with (work/'calls.jsonl').open('a') as output: output.write(json.dumps(args)+'\\n')
                if 'cp' in args:
                    data=Path(args[args.index('cp')+1]).read_bytes()
                    if os.environ['FIXTURE_FAULT']=='empty': data=b''
                    if os.environ['FIXTURE_FAULT']=='truncated': data=data[:512]
                    (work/'pod.tar').write_bytes(data)
                elif 'sha256sum' in args:
                    print(hashlib.sha256((work/'pod.tar').read_bytes()).hexdigest()+'  /tmp/localcontentshare-data.tar')
                elif '-xpf' in args:
                    (work/'extracted').touch()
            '''))
            for path in (ssh, kubectl):
                path.chmod(0o755)
            for fault in ('none', 'empty', 'truncated', 'source-empty'):
                with self.subTest(fault=fault):
                    for name in ('calls.jsonl', 'pod.tar', 'extracted'):
                        (work / name).unlink(missing_ok=True)
                    environment = {**os.environ, 'PATH': str(binary) + os.pathsep + os.environ['PATH'],
                                   'KUBECONFIG': str(work / 'fixture-kubeconfig'), 'FIXTURE_WORK': str(work),
                                   'FIXTURE_ARCHIVE': str(archive), 'FIXTURE_FAULT': fault}
                    result = subprocess.run(['bash', '-c', commands[0]], cwd=ROOT,
                                            env=environment, capture_output=True, timeout=20)
                    if fault == 'none':
                        self.assertEqual(result.returncode, 0, 'Complete archive fixture failed')
                        self.assertTrue((work / 'extracted').exists())
                    else:
                        self.assertNotEqual(result.returncode, 0, 'Incomplete archive was accepted')
                        self.assertFalse((work / 'extracted').exists())
                    if fault == 'source-empty':
                        self.assertFalse((work / 'calls.jsonl').exists())
