"""Check the isolated Pi wrapper against a synthetic credential and host file."""
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest

WRAPPER = pathlib.Path(__file__).resolve().parents[1] / 'bin' / 'pi-isolated'


@unittest.skipUnless(sys.platform.startswith('linux') and shutil.which('bwrap') and shutil.which('socat'),
                     'requires Linux, bubblewrap and socat')
class PiIsolationTests(unittest.TestCase):
    def test_host_secrets_and_network_are_not_visible(self):
        with tempfile.TemporaryDirectory() as root:
            root = pathlib.Path(root)
            work = root / 'work'
            work.mkdir()
            secret = root / 'outside-canary'
            secret.write_text('synthetic credential')
            deps = root / 'base'
            (deps / 'node_modules').mkdir(parents=True)
            (deps / 'node_modules' / 'marker').write_text('node-dep')
            (deps / '.venv').mkdir()
            (deps / '.venv' / 'marker').write_text('python-dep')
            (work / 'node_modules').symlink_to(deps / 'node_modules')
            (work / '.venv').symlink_to(deps / '.venv')
            runtime = root / 'runtime'
            binary = runtime / 'node_modules' / '.bin' / 'pi'
            binary.parent.mkdir(parents=True)
            binary.write_text('#!/usr/bin/python3\nimport json,os,socket\n'
                              's=socket.socket();s.settimeout(1)\n'
                              'try:s.connect(("1.1.1.1",443));network=True\n'
                              'except OSError:network=False\n'
                              'print(json.dumps({"environment": "OS_CANARY" in os.environ,'
                              '"host_file":os.path.exists(' + repr(str(secret)) + '),'
                              '"network":network,"writable":os.access(os.getcwd(),os.W_OK),'
                              '"node_dep":open("node_modules/marker").read(),'
                              '"python_dep":open(".venv/marker").read()}))\n')
            binary.chmod(0o755)
            sock = socket.socket(socket.AF_UNIX)
            try:
                path = str(root / 'broker.sock')
                sock.bind(path)
                sock.listen()
                env = dict(os.environ, OS_CANARY='synthetic', OPENSWARM_PI_RUNTIME=str(runtime),
                           OPENSWARM_PI_BROKER_SOCKET=path, OPENSWARM_PI_DEPS_ROOT=str(deps))
                command = [str(WRAPPER), '-p', '--mode', 'json', '--provider', 'minimax-cn', '--model',
                           'MiniMax-M3.1-Flash', '--tools', 'read,bash,edit,write,grep,find,ls', 'synthetic']
                result = subprocess.run(command, cwd=work, env=env, capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout),
                                 {'environment': False, 'host_file': False, 'network': False, 'writable': True,
                                  'node_dep': 'node-dep', 'python_dep': 'python-dep'})
                command[command.index('MiniMax-M3.1-Flash')] = 'wrong-model'
                result = subprocess.run(command, cwd=work, env=env, capture_output=True, text=True, timeout=15)
                self.assertNotEqual(result.returncode, 0)
            finally:
                sock.close()


if __name__ == '__main__':
    unittest.main()
