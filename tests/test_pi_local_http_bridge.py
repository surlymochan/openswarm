import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


class PiLocalBridgeTests(unittest.TestCase):
    def test_stream_auth_and_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            token = root / 'token'
            token.write_text('test-only-secret')
            token.chmod(0o600)
            capture = root / 'captured'
            ssh = root / 'fake-ssh'
            ssh.write_text('#!' + sys.executable + '\nimport os,sys\n'
                           'data=b""\n'
                           'while not data.endswith(b"\\r\\n\\r\\n"): data+=sys.stdin.buffer.read(1)\n'
                           'size=int(next(x.split(b":",1)[1] for x in data.split(b"\\r\\n") if x.lower().startswith(b"content-length:")))\n'
                           'data+=sys.stdin.buffer.read(size)\n'
                           'open(os.environ["FAKE_CAPTURE"],"wb").write(data)\n'
                           'sys.stdout.buffer.write(b"HTTP/1.1 200 OK\\r\\nContent-Type: text/event-stream\\r\\nContent-Length: 18\\r\\nConnection: close\\r\\n\\r\\ndata: accepted\\n\\n  ")\n')
            ssh.chmod(0o700)
            for _ in range(2):
                process = subprocess.Popen([sys.executable, str(ROOT / 'bin/pi-local-http-bridge.py'),
                                            '--port', '0', '--token-file', str(token), '--ssh', str(ssh)],
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                           env={**os.environ, 'FAKE_CAPTURE': str(capture)})
                try:
                    line = process.stdout.readline().strip()
                    self.assertTrue(line.startswith('READY '), line)
                    port = int(line.split()[1])
                    url = f'http://127.0.0.1:{port}/v1/messages?beta=true'
                    body = json.dumps({'model': 'MiniMax-M3.1-Flash', 'messages': []}).encode()
                    bad = urllib.request.Request(url, data=body, headers={'x-api-key': 'wrong'})
                    with self.assertRaises(urllib.error.HTTPError) as refused:
                        urllib.request.urlopen(bad, timeout=5)
                    self.assertEqual(refused.exception.code, 403)
                    refused.exception.close()
                    request = urllib.request.Request(url, data=body, headers={'x-api-key': 'test-only-secret',
                                                                             'anthropic-beta': 'interleaved-thinking-2025-05-14'})
                    with urllib.request.urlopen(request, timeout=5) as response:
                        self.assertIn(b'data: accepted', response.read())
                    captured = capture.read_bytes()
                    self.assertIn(b'POST /v1/messages?beta=true HTTP/1.1', captured)
                    self.assertIn(b'anthropic-beta: interleaved-thinking-2025-05-14', captured)
                    self.assertNotIn(b'test-only-secret', captured)
                finally:
                    process.terminate()
                    process.wait(timeout=5)
                    process.stdout.close()
                    process.stderr.close()
