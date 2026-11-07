"""Smoke tests for bin/pi-broker-service: socket guard + node exec.

Runs the script against a fake HOME and a fake node binary, so the live
broker (a LaunchAgent service on gocc) is never touched: the script only
ever execs $HOME/.local/node-v24.14.1/bin/node, which the tests replace
with a recorder that exits 0.
"""
import os
import pathlib
import socket
import stat
import subprocess
import sys
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / 'bin' / 'pi-broker-service'
CACHE_REL = pathlib.Path('Library') / 'Caches' / 'openswarm-pi-broker'
NODE_REL = pathlib.Path('.local') / 'node-v24.14.1' / 'bin' / 'node'
AUTH_REL = pathlib.Path('.local/node-v24.14.1/lib/node_modules/@mariozechner'
                        '/pi-coding-agent/dist/core/auth-storage.js')

FAKE_NODE = ("#!/usr/bin/env python3\n"
             "import os\n"
             "import sys\n"
             "with open(os.environ['FAKE_NODE_ARGV'], 'w') as handle:\n"
             "    handle.write('\\n'.join(sys.argv))\n")


def _temp_home():
    """Short temp HOME so the broker socket path stays under the AF_UNIX
    limit (104 chars on macOS): the default macOS temp dir is too long
    once Library/Caches/openswarm-pi-broker/broker.sock is appended."""
    if os.name == 'nt':
        return tempfile.TemporaryDirectory(prefix='os-')
    return tempfile.TemporaryDirectory(prefix='os-', dir='/tmp')


def _install_fake_node(home):
    node = pathlib.Path(home) / NODE_REL
    node.parent.mkdir(parents=True)
    node.write_text(FAKE_NODE)
    node.chmod(0o755)
    return node


@unittest.skipIf(os.name == "nt",
                 "pi-broker-service guards a '/'-rooted unix-domain socket by design; no Windows transport")
class PiBrokerServiceTests(unittest.TestCase):
    def _run(self, home, argv_path):
        env = dict(os.environ, HOME=str(home),
                   FAKE_NODE_ARGV=str(argv_path))
        return subprocess.run([sys.executable, str(SCRIPT)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              env=env, timeout=15)

    def _expected_argv(self, home, node):
        return [str(node),
                str(SCRIPT.parent / 'pi-minimax-broker.mjs'),
                str(pathlib.Path(home) / CACHE_REL / 'broker.sock'),
                str(pathlib.Path(home) / AUTH_REL),
                'MiniMax-M3.1-Flash']

    def test_fresh_home_execs_node_with_expected_argv(self):
        with _temp_home() as home:
            node = _install_fake_node(home)
            argv_path = pathlib.Path(home) / 'node-argv.txt'
            proc = self._run(home, argv_path)
            self.assertEqual(proc.returncode, 0, proc.stderr.decode())
            self.assertEqual(argv_path.read_text().split('\n'),
                             self._expected_argv(home, node))
            root = pathlib.Path(home) / CACHE_REL
            self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)

    def test_stale_socket_removed_then_execs_node(self):
        # Bind a socket and close it without unlinking: the path remains
        # but connect() is refused, so the script must unlink it and exec.
        with _temp_home() as home:
            node = _install_fake_node(home)
            argv_path = pathlib.Path(home) / 'node-argv.txt'
            path = pathlib.Path(home) / CACHE_REL / 'broker.sock'
            path.parent.mkdir(parents=True)
            stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            stale.bind(str(path))
            stale.listen(1)
            stale.close()
            proc = self._run(home, argv_path)
            self.assertEqual(proc.returncode, 0, proc.stderr.decode())
            self.assertEqual(argv_path.read_text().split('\n'),
                             self._expected_argv(home, node))
            self.assertFalse(path.exists())

    def test_live_socket_blocks_exec(self):
        with _temp_home() as home:
            _install_fake_node(home)
            argv_path = pathlib.Path(home) / 'node-argv.txt'
            path = pathlib.Path(home) / CACHE_REL / 'broker.sock'
            path.parent.mkdir(parents=True)
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(str(path))
            server.listen(1)
            try:
                proc = self._run(home, argv_path)
            finally:
                server.close()
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn(b'broker already running', proc.stderr)
            self.assertFalse(argv_path.exists())

    def test_regular_file_at_socket_path_rejected(self):
        with _temp_home() as home:
            _install_fake_node(home)
            argv_path = pathlib.Path(home) / 'node-argv.txt'
            path = pathlib.Path(home) / CACHE_REL / 'broker.sock'
            path.parent.mkdir(parents=True)
            path.write_text('not a socket')
            proc = self._run(home, argv_path)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn(b'invalid broker socket', proc.stderr)
            self.assertFalse(argv_path.exists())

    def test_symlink_at_socket_path_rejected(self):
        with _temp_home() as home:
            _install_fake_node(home)
            argv_path = pathlib.Path(home) / 'node-argv.txt'
            path = pathlib.Path(home) / CACHE_REL / 'broker.sock'
            path.parent.mkdir(parents=True)
            target = pathlib.Path(home) / 'target.txt'
            target.write_text('not a socket')
            path.symlink_to(target)
            proc = self._run(home, argv_path)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn(b'invalid broker socket', proc.stderr)
            self.assertFalse(argv_path.exists())

    def test_non_directory_cache_root_rejected(self):
        with _temp_home() as home:
            _install_fake_node(home)
            argv_path = pathlib.Path(home) / 'node-argv.txt'
            root = pathlib.Path(home) / CACHE_REL
            root.parent.mkdir(parents=True)
            root.write_text('not a directory')
            proc = self._run(home, argv_path)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn(b'FileExistsError', proc.stderr)
            self.assertFalse(argv_path.exists())
