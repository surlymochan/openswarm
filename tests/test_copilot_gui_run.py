"""Smoke tests for bin/copilot-gui-run --worker: flag gate + control socket.

Runs the script's --worker dispatch (which runs before the macOS gate,
so it works on Linux CI) against a fake HOME and a fake copilot binary,
so no real GUI session, keychain, or copilot binary is ever touched:
the worker only ever execs
$HOME/tools/copilot-executor/node_modules/.bin/copilot, which the tests
replace with a recorder that exits with a chosen code.

The launchctl launcher half of the script is macOS-only and stays
untested on Linux CI.
"""
import json
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import time
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / 'bin' / 'copilot-gui-run'
COPILOT_REL = (pathlib.Path('tools') / 'copilot-executor' / 'node_modules'
               / '.bin' / 'copilot')
REQUIRED = ['--sandbox', '--disable-builtin-mcps', '--assisted-approval',
            '--no-ask-user']

FAKE_COPILOT = ("#!/usr/bin/env python3\n"
                "import os\n"
                "import signal\n"
                "import sys\n"
                "import time\n"
                "def _term(signum, frame):\n"
                "    with open(os.environ['FAKE_COPILOT_TERM'], 'a') as handle:\n"
                "        handle.write('SIGTERM\\n')\n"
                "    sys.exit(143)\n"
                "signal.signal(signal.SIGTERM, _term)\n"
                "with open(os.environ['FAKE_COPILOT_ARGV'], 'w') as handle:\n"
                "    handle.write('\\n'.join(sys.argv))\n"
                "with open(os.environ['FAKE_COPILOT_CWD'], 'w') as handle:\n"
                "    handle.write(os.getcwd())\n"
                "sys.stdout.write(os.environ.get('FAKE_COPILOT_STDOUT', ''))\n"
                "sys.stdout.flush()\n"
                "sys.stderr.write(os.environ.get('FAKE_COPILOT_STDERR', ''))\n"
                "sys.stderr.flush()\n"
                "time.sleep(float(os.environ.get('FAKE_COPILOT_SLEEP', '0')))\n"
                "sys.exit(int(os.environ.get('FAKE_COPILOT_EXIT', '0')))\n")


def _temp_dir():
    """Short temp dir so the control-socket path stays under the AF_UNIX
    limit (104 chars on macOS): the default macOS temp dir is too long
    once control.sock is appended."""
    if os.name == 'nt':
        return tempfile.TemporaryDirectory(prefix='os-')
    return tempfile.TemporaryDirectory(prefix='os-', dir='/tmp')


def _install_fake_copilot(home):
    binary = pathlib.Path(home) / COPILOT_REL
    binary.parent.mkdir(parents=True)
    binary.write_text(FAKE_COPILOT)
    binary.chmod(0o755)
    return binary


@unittest.skipIf(os.name == "nt",
                 "copilot-gui-run --worker kills the child process group "
                 "with os.killpg, which is Unix-only")
class CopilotGuiRunWorkerTests(unittest.TestCase):
    def _env(self, home, marker_dir):
        return dict(os.environ, HOME=str(home),
                    FAKE_COPILOT_ARGV=str(marker_dir / 'argv.txt'),
                    FAKE_COPILOT_CWD=str(marker_dir / 'cwd.txt'),
                    FAKE_COPILOT_TERM=str(marker_dir / 'term.txt'))

    def _setup(self, home, args, exit_code='0', sleep='0',
               stdout='', stderr=''):
        """Write request.json + a listening control socket; return root."""
        root = pathlib.Path(home) / 'work'
        root.mkdir(parents=True)
        workdir = pathlib.Path(home) / 'cwd'
        workdir.mkdir()
        (root / 'request.json').write_text(
            json.dumps({'cwd': str(workdir), 'args': args}))
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(root / 'control.sock'))
        server.listen(1)
        server.settimeout(10)
        env = self._env(home, root)
        env.update(FAKE_COPILOT_EXIT=exit_code, FAKE_COPILOT_SLEEP=sleep,
                   FAKE_COPILOT_STDOUT=stdout, FAKE_COPILOT_STDERR=stderr)
        return root, workdir, server, env

    def _run_worker(self, root, env, timeout=20):
        return subprocess.run([sys.executable, str(SCRIPT), '--worker',
                               str(root)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              env=env, timeout=timeout)

    def test_missing_required_flag_exits_2(self):
        # Validation runs before the control-socket connect, so no
        # listener is needed and the fake binary must never run.
        with _temp_dir() as home:
            binary = _install_fake_copilot(home)
            root = pathlib.Path(home) / 'work'
            root.mkdir(parents=True)
            (root / 'request.json').write_text(json.dumps(
                {'cwd': home, 'args': REQUIRED[:-1]}))
            proc = self._run_worker(root, self._env(home, root))
            self.assertEqual(proc.returncode, 2, proc.stderr.decode())
            self.assertFalse((root / 'argv.txt').exists())

    def test_forbidden_flag_exits_2(self):
        with _temp_dir() as home:
            binary = _install_fake_copilot(home)
            root = pathlib.Path(home) / 'work'
            root.mkdir(parents=True)
            (root / 'request.json').write_text(json.dumps(
                {'cwd': home, 'args': REQUIRED + ['--yolo']}))
            proc = self._run_worker(root, self._env(home, root))
            self.assertEqual(proc.returncode, 2, proc.stderr.decode())
            self.assertFalse((root / 'argv.txt').exists())

    def test_happy_path_exit_zero_argv_cwd_and_d_byte(self):
        with _temp_dir() as home:
            binary = _install_fake_copilot(home)
            root, workdir, server, env = self._setup(
                home, REQUIRED, stdout='hello-out', stderr='hello-err')
            proc = subprocess.Popen(
                [sys.executable, str(SCRIPT), '--worker', str(root)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
            try:
                peer, _ = server.accept()
                try:
                    self.assertEqual(peer.recv(1), b'D')
                finally:
                    peer.close()
            finally:
                server.close()
            self.assertEqual(proc.wait(timeout=15), 0,
                             proc.stderr.read().decode())
            self.assertEqual((root / 'argv.txt').read_text().split('\n'),
                             [str(binary), *REQUIRED])
            self.assertEqual((root / 'cwd.txt').read_text(), str(workdir))
            self.assertEqual((root / 'exit').read_text(), '0')
            self.assertEqual((root / 'stdout').read_text(), 'hello-out')
            self.assertEqual((root / 'stderr').read_text(), 'hello-err')

    def test_nonzero_exit_code_passes_through(self):
        with _temp_dir() as home:
            _install_fake_copilot(home)
            root, _, server, env = self._setup(home, REQUIRED, exit_code='3')
            proc = subprocess.Popen(
                [sys.executable, str(SCRIPT), '--worker', str(root)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
            try:
                peer, _ = server.accept()
                try:
                    self.assertEqual(peer.recv(1), b'D')
                finally:
                    peer.close()
            finally:
                server.close()
            self.assertEqual(proc.wait(timeout=15), 3,
                             proc.stderr.read().decode())
            self.assertEqual((root / 'exit').read_text(), '3')

    def test_extra_args_pass_through(self):
        with _temp_dir() as home:
            binary = _install_fake_copilot(home)
            args = REQUIRED + ['--model', 'gpt-5']
            root, _, server, env = self._setup(home, args)
            proc = subprocess.Popen(
                [sys.executable, str(SCRIPT), '--worker', str(root)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
            try:
                peer, _ = server.accept()
                try:
                    self.assertEqual(peer.recv(1), b'D')
                finally:
                    peer.close()
            finally:
                server.close()
            self.assertEqual(proc.wait(timeout=15), 0,
                             proc.stderr.read().decode())
            self.assertEqual((root / 'argv.txt').read_text().split('\n'),
                             [str(binary), *args])

    def test_control_socket_disconnect_kills_child_and_exits_1(self):
        with _temp_dir() as home:
            _install_fake_copilot(home)
            root, _, server, env = self._setup(home, REQUIRED, sleep='30')
            proc = subprocess.Popen(
                [sys.executable, str(SCRIPT), '--worker', str(root)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
            try:
                peer, _ = server.accept()
                # Wait until the child is up: the fake installs its
                # SIGTERM handler before writing argv.txt, so this
                # guarantees the kill below is observed, not raced.
                for _ in range(100):
                    if (root / 'argv.txt').exists():
                        break
                    time.sleep(0.1)
                self.assertTrue((root / 'argv.txt').exists(),
                                'fake copilot never started')
                # Dropping the control socket must SIGTERM the child and
                # make the worker exit 1 without writing an exit file.
                peer.close()
            finally:
                server.close()
            self.assertEqual(proc.wait(timeout=15), 1,
                             proc.stderr.read().decode())
            self.assertEqual((root / 'term.txt').read_text(), 'SIGTERM\n')
            self.assertFalse((root / 'exit').exists())
