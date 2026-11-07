"""Smoke tests for bin/pi-broker-stdio: stdin <-> broker-socket relay."""
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import threading
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / 'bin' / 'pi-broker-stdio'
SOCKET_REL = pathlib.Path('Library') / 'Caches' / 'openswarm-pi-broker' / 'broker.sock'


def _temp_home():
    """Short temp HOME so the broker socket path stays under the AF_UNIX
    limit (104 chars on macOS): the default macOS temp dir is too long
    once Library/Caches/openswarm-pi-broker/broker.sock is appended."""
    if os.name == 'nt':
        return tempfile.TemporaryDirectory(prefix='os-')
    return tempfile.TemporaryDirectory(prefix='os-', dir='/tmp')


def _socket_path(home):
    return pathlib.Path(home) / SOCKET_REL


def _start_echo_server(path):
    """Listen on path; accept one connection, echo everything back, close on half-close."""
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    server.listen(1)
    server.settimeout(15)

    def serve():
        try:
            conn, _ = server.accept()
        except socket.timeout:
            return
        with conn:
            conn.settimeout(15)
            try:
                while True:
                    data = conn.recv(65536)
                    if not data:
                        return
                    conn.sendall(data)
            except socket.timeout:
                return

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return server, thread


def _start_greeting_server(path, greeting):
    """Listen on path; accept one connection, send greeting once, then close."""
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    server.listen(1)
    server.settimeout(15)

    def serve():
        try:
            conn, _ = server.accept()
        except socket.timeout:
            return
        with conn:
            try:
                conn.sendall(greeting)
            except OSError:
                pass

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return server, thread


@unittest.skipIf(os.name == "nt",
                 "pi-broker-stdio relays a '/'-rooted unix-domain socket by design; no Windows transport")
class PiBrokerStdioTests(unittest.TestCase):
    def _run(self, home, stdin_data):
        env = dict(os.environ, HOME=str(home))
        return subprocess.run([sys.executable, str(SCRIPT)], input=stdin_data,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              env=env, timeout=15)

    def test_relay_round_trip(self):
        with _temp_home() as home:
            path = _socket_path(home)
            path.parent.mkdir(parents=True)
            server, _thread = _start_echo_server(path)
            try:
                proc = self._run(home, b'hello broker\n')
            finally:
                server.close()
            self.assertEqual(proc.returncode, 0, proc.stderr.decode())
            self.assertEqual(proc.stdout, b'hello broker\n')

    def test_relay_large_payload(self):
        # 200 KB forces several 64 KB reads/writes through the relay loop.
        payload = (b'0123456789abcdef' * 12500)[:200000]
        with _temp_home() as home:
            path = _socket_path(home)
            path.parent.mkdir(parents=True)
            server, _thread = _start_echo_server(path)
            try:
                proc = self._run(home, payload)
            finally:
                server.close()
            self.assertEqual(proc.returncode, 0, proc.stderr.decode())
            self.assertEqual(proc.stdout, payload)

    def test_broker_close_ends_relay(self):
        # Broker speaks first and closes while stdin is still open: the relay
        # must exit 0 on socket EOF instead of waiting on stdin forever.
        with _temp_home() as home:
            path = _socket_path(home)
            path.parent.mkdir(parents=True)
            server, _thread = _start_greeting_server(path, b'goodbye\n')
            env = dict(os.environ, HOME=str(home))
            proc = subprocess.Popen([sys.executable, str(SCRIPT)],
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, env=env)
            try:
                proc.wait(timeout=15)
            finally:
                server.close()
                if proc.poll() is None:
                    proc.kill()
                proc.stdin.close()
            self.assertEqual(proc.returncode, 0, proc.stderr.read().decode())
            self.assertEqual(proc.stdout.read(), b'goodbye\n')

    def test_missing_socket_exits_nonzero(self):
        with _temp_home() as home:
            proc = self._run(home, b'hello\n')
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn(b'FileNotFoundError', proc.stderr)
