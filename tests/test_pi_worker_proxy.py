"""Smoke tests for bin/pi-worker-proxy: private-socket relay guards.

pi-worker-proxy is the only bin script with no test coverage at all
(every other script is referenced by at least one test file). These
tests exercise the startup guards and the stale-socket reclaim path
without a real broker: the fake ssh config points the relay at
127.0.0.1:1, so the spawned ssh fails immediately instead of
resolving a real host.
"""
import os
import pathlib
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / 'bin' / 'pi-worker-proxy'


def _temp_dir():
    """Short temp dir so the socket path stays under the AF_UNIX limit."""
    if os.name == 'nt':
        return tempfile.TemporaryDirectory(prefix='os-')
    return tempfile.TemporaryDirectory(prefix='os-', dir='/tmp')


def _install_config(home):
    """Fake ssh config: the relay's ssh spawn fails fast (port 1
    refuses immediately) instead of resolving a real broker host."""
    cfg = pathlib.Path(home) / '.ssh' / 'openswarm-pi' / 'config'
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text('Host openswarm-pi-broker\n'
                   '  HostName 127.0.0.1\n'
                   '  Port 1\n'
                   '  ConnectTimeout 2\n')
    return cfg


@unittest.skipIf(os.name == "nt",
                 "pi-worker-proxy relays a unix-domain socket by design; no Windows transport")
class PiWorkerProxyTests(unittest.TestCase):
    def _env(self, home, sock_path):
        return dict(os.environ, HOME=str(home),
                    OPENSWARM_PI_WORKER_SOCKET=str(sock_path))

    def _run(self, home, sock_path, **kwargs):
        return subprocess.run([sys.executable, str(SCRIPT)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              env=self._env(home, sock_path), timeout=15,
                              **kwargs)

    def test_missing_config_exits_nonzero(self):
        # No ~/.ssh/openswarm-pi/config: the proxy must refuse to start
        # instead of binding a socket nobody can reach.
        with _temp_dir() as home:
            sock_path = pathlib.Path(home) / 'broker.sock'
            proc = self._run(home, sock_path)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn(b'worker proxy configuration unavailable',
                          proc.stderr)

    def test_missing_socket_parent_exits_nonzero(self):
        # The socket parent dir does not exist: same guard, same message.
        with _temp_dir() as home:
            _install_config(home)
            sock_path = pathlib.Path(home) / 'no-such-dir' / 'broker.sock'
            proc = self._run(home, sock_path)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn(b'worker proxy configuration unavailable',
                          proc.stderr)

    def test_non_socket_path_exits_nonzero(self):
        # Something that is not a socket already sits at the path.
        with _temp_dir() as home:
            _install_config(home)
            sock_path = pathlib.Path(home) / 'broker.sock'
            sock_path.write_text('not a socket')
            proc = self._run(home, sock_path)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn(b'invalid worker socket', proc.stderr)

    def test_already_running_exits_nonzero(self):
        # A live socket owned by this user is already listening: the
        # second proxy must exit instead of stealing the path.
        with _temp_dir() as home:
            _install_config(home)
            sock_path = pathlib.Path(home) / 'broker.sock'
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                listener.bind(str(sock_path))
                listener.listen(1)
                proc = self._run(home, sock_path)
            finally:
                listener.close()
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn(b'worker proxy already running', proc.stderr)

    def test_stale_socket_reclaimed_and_serves(self):
        # A socket file whose listener is gone must be unlinked and the
        # proxy must come up, accept a client, and remove its socket
        # again on SIGTERM.
        with _temp_dir() as home:
            _install_config(home)
            sock_path = pathlib.Path(home) / 'broker.sock'
            stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            stale.bind(str(sock_path))
            stale.close()  # listener gone, socket file left behind
            proc = subprocess.Popen(
                [sys.executable, str(SCRIPT)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env=self._env(home, sock_path))
            try:
                deadline = time.time() + 10
                connected = False
                while time.time() < deadline:
                    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    try:
                        client.settimeout(2)
                        client.connect(str(sock_path))
                        connected = True
                        break
                    except OSError:
                        time.sleep(0.2)
                    finally:
                        client.close()
                self.assertTrue(connected,
                                'proxy did not come up on the reclaimed socket')
                self.assertIsNone(proc.poll(), 'proxy exited early')
            finally:
                if proc.poll() is None:
                    proc.send_signal(signal.SIGTERM)
                proc.wait(timeout=15)
                proc.stdout.close()
                proc.stderr.close()
            self.assertEqual(proc.returncode, 0)
            self.assertFalse(sock_path.exists())


if __name__ == '__main__':
    unittest.main()
