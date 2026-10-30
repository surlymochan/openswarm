"""Exercise isolated Pi lane read-only work without reusing the writable child."""
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
CLI = ROOT / 'bin' / 'openswarm'


@unittest.skipUnless(sys.platform.startswith('linux') and shutil.which('bwrap') and shutil.which('socat'),
                     'requires Linux, bubblewrap and socat')
class PiLaneReadOnlyTests(unittest.TestCase):
    def test_readonly_assignment_uses_separate_readonly_child(self):
        with tempfile.TemporaryDirectory(prefix='pi-lane-ro-', dir='/tmp') as temp:
            root = pathlib.Path(temp)
            repo, home, runtime = root / 'repo', root / 'state', root / 'runtime'
            repo.mkdir()
            def git(*args):
                return subprocess.run(['git', *args], cwd=repo, capture_output=True, text=True, check=True).stdout
            git('init', '-q')
            git('config', 'user.name', 'Test')
            git('config', 'user.email', 'test@example.invalid')
            (repo / 'sample.py').write_text('print(1)\n')
            git('add', 'sample.py')
            git('commit', '-qm', 'base')
            binary = runtime / 'node_modules/.bin/pi'
            binary.parent.mkdir(parents=True)
            binary.write_text('''#!/usr/bin/python3
import json, sys
def send(value):
 print(json.dumps(value), flush=True)
for line in sys.stdin:
 request = json.loads(line)
 kind = request.get("type")
 if kind == "new_session":
  send({"id":request.get("id"),"type":"response","command":kind,"success":True})
 elif kind == "prompt":
  send({"id":request.get("id"),"type":"response","command":kind,"success":True})
  try:
   with open("unexpected-write", "w") as stream: stream.write("bad")
   blocked = False
  except OSError: blocked = True
  result = {"status":"done", "summary":"write blocked" if blocked else "write succeeded",
            "files_changed":[], "tests":[], "risks":[]}
  message = {"role":"assistant", "content":[{"type":"text", "text":json.dumps(result)}],
             "usage":{"input":1,"output":1}}
  send({"type":"message_end", "message":message})
  send({"type":"agent_end", "messages":[message]})
''')
            binary.chmod(0o755)
            broker = socket.socket(socket.AF_UNIX)
            broker.bind(str(root / 'broker.sock'))
            broker.listen()
            home.mkdir()
            (home / 'lanes.json').write_text(json.dumps({'lanes': [{
                'name': 'R1', 'backend': 'pi', 'model': 'MiniMax-M3.1-Flash',
                'provider': 'minimax-cn', 'repo': str(repo),
            }]}))
            env = dict(os.environ, OPENSWARM_HOME=str(home), OPENSWARM_PI_RUNTIME=str(runtime),
                       OPENSWARM_PI_BROKER_SOCKET=str(root / 'broker.sock'))
            def cli(*args, check=True):
                r = subprocess.run([sys.executable, str(CLI), *args], env=env, capture_output=True,
                                   text=True, timeout=30)
                if check and r.returncode:
                    self.fail('CLI failed: ' + r.stderr[-500:])
                return r
            try:
                cli('lane', 'up', 'R1')
                deadline = time.monotonic() + 30
                state_path = home / 'lanes/R1/state.json'
                while time.monotonic() < deadline:
                    if state_path.exists() and json.loads(state_path.read_text()).get('state') == 'idle':
                        break
                    time.sleep(.1)
                else:
                    self.fail('Pi lane did not start')
                resident = json.loads(state_path.read_text())['child_pid']
                result = json.loads(cli('lane', 'assign', 'R1', '--read-only', '--prompt', 'Review sample.py').stdout)
                run = home / 'runs' / result['id']
                deadline = time.monotonic() + 45
                while time.monotonic() < deadline:
                    path = run / 'result.json'
                    if path.exists():
                        outcome = json.loads(path.read_text())
                        break
                    time.sleep(.1)
                else:
                    self.fail('read-only Pi run did not finish')
                self.assertEqual(outcome['status'], 'done')
                self.assertEqual(outcome['result']['summary'], 'write blocked')
                self.assertFalse((home / 'lanes/R1/wt/unexpected-write').exists())
                temporary = json.loads((run / 'status.json').read_text()).get('executor_pid')
                self.assertNotEqual(temporary, resident)
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    lane_state = json.loads(state_path.read_text())
                    if lane_state.get('state') == 'idle':
                        break
                    time.sleep(.1)
                else:
                    self.fail('Pi lane did not return to idle')
                self.assertEqual(lane_state['child_pid'], resident)
                with self.assertRaises(ProcessLookupError):
                    os.kill(temporary, 0)
            finally:
                cli('lane', 'down', 'R1', check=False)
                broker.close()


if __name__ == '__main__':
    unittest.main()
