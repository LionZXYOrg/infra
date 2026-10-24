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
            fixture_key = work / 'fixture-host-key'
            subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(fixture_key)],
                           capture_output=True, check=True, timeout=10)
            public_key = fixture_key.with_suffix('.pub').read_text().split()[1]
            pin = next(line for line in commands[0].splitlines() if line.startswith("printf '%s"))
            commands[0] = commands[0].replace(pin, "printf '%s\\n' '192.168.1.109 ssh-ed25519 "
                                             + public_key + "' > \"$KNOWN_HOSTS\"")
            with tarfile.open(archive, 'w') as output:
                item = tarfile.TarInfo('fixture.txt')
                content = b'Generated restore fixture'
                item.size = len(content)
                output.addfile(item, io.BytesIO(content))
            ssh = binary / 'ssh'
            ssh.write_text('#!/usr/bin/env python3\n' + textwrap.dedent('''
                import os,sys
                from pathlib import Path
                work=Path(os.environ['FIXTURE_WORK'])
                with (work/'ssh-calls').open('a') as output: output.write('ssh\\n')
                assert sys.argv[-2]=='root@192.168.1.109'
                known=next(arg.split('=',1)[1] for arg in sys.argv if arg.startswith('UserKnownHostsFile='))
                assert Path(known).read_text().startswith('192.168.1.109 ssh-ed25519 ')
                if os.environ['FIXTURE_FAULT']=='ssh-error': sys.exit(255)
                if 'tar -C ' in sys.argv[-1]:
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
            for fault in ('none', 'empty', 'truncated', 'source-empty', 'ssh-error'):
                for parent in ('plain', 'conditional', 'unexported'):
                    with self.subTest(fault=fault, parent=parent):
                        for name in ('calls.jsonl', 'pod.tar', 'extracted', 'ssh-calls'):
                            (work / name).unlink(missing_ok=True)
                        environment = {**os.environ, 'PATH': str(binary) + os.pathsep + os.environ['PATH'],
                                       'KUBECONFIG': str(work / 'fixture-kubeconfig'), 'FIXTURE_WORK': str(work),
                                       'FIXTURE_ARCHIVE': str(archive), 'FIXTURE_FAULT': fault}
                        script = commands[0]
                        if parent == 'conditional':
                            script = 'set +e\nif ' + script + '\nthen\n :\nelse\n exit 1\nfi\n'
                        if parent == 'unexported':
                            script = 'export -n KUBECONFIG\n' + script
                        result = subprocess.run(['bash', '-c', script], cwd=ROOT,
                                                env=environment, capture_output=True, timeout=20)
                        if fault == 'none':
                            self.assertEqual(result.returncode, 0, 'Complete archive fixture failed')
                            self.assertTrue((work / 'extracted').exists())
                        else:
                            self.assertNotEqual(result.returncode, 0, 'Incomplete archive was accepted')
                            self.assertFalse((work / 'extracted').exists())
                        if fault in ('source-empty', 'ssh-error'):
                            self.assertFalse((work / 'calls.jsonl').exists())
                        if fault == 'ssh-error':
                            self.assertEqual((work / 'ssh-calls').read_text(), 'ssh\n')
