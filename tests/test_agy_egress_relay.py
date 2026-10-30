import json
import importlib.util
import os
import pathlib
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock


WRAPPER = pathlib.Path(__file__).resolve().parents[1] / "bin" / "agy-via-relay"
RELAY = WRAPPER.with_name("agy-egress-relay.py")


class AgyRelayTests(unittest.TestCase):
    def test_untrusted_proxy_cannot_receive_credentials(self):
        with tempfile.TemporaryDirectory() as root:
            directory = pathlib.Path(root)
            config = directory / "openssl.cnf"
            config.write_text("[req]\ndistinguished_name=dn\nx509_extensions=ext\nprompt=no\n"
                              "[dn]\nCN=127.0.0.1\n[ext]\nsubjectAltName=IP:127.0.0.1\n")
            for name in ("trusted", "attacker"):
                subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                                "-keyout", str(directory / (name + ".key")), "-out",
                                str(directory / (name + ".crt")), "-days", "1", "-config", str(config)],
                               check=True, capture_output=True)
            token = directory / "token"
            token.write_text("synthetic-secret")
            agent = directory / "agent"
            agent.write_text('#!/bin/sh\n[ "$HTTPS_PROXY" = "http://inherited:9999" ]\n')
            agent.chmod(0o700)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(str(directory / "attacker.crt"), str(directory / "attacker.key"))
            listener = socket.socket()
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            listener.settimeout(5)
            received = []
            def fake_proxy():
                try:
                    client, _ = listener.accept()
                    client.settimeout(5)
                    try:
                        with context.wrap_socket(client, server_side=True) as tls:
                            received.append(tls.recv(4096))
                    except ssl.SSLError:
                        received.append(b"")
                except OSError:
                    pass
            worker = threading.Thread(target=fake_proxy, daemon=True)
            worker.start()
            try:
                env = dict(os.environ, HTTPS_PROXY="http://inherited:9999",
                           OPENSWARM_AGY_REAL_BIN=str(agent), OPENSWARM_AGY_RELAY_TOKEN_FILE=str(token),
                           OPENSWARM_AGY_RELAY_CERT_FILE=str(directory / "trusted.crt"),
                           OPENSWARM_AGY_RELAY_CA_BUNDLE=str(directory / "trusted.crt"),
                           OPENSWARM_AGY_RELAY_PORT=str(listener.getsockname()[1]))
                result = subprocess.run([str(WRAPPER)], env=env, capture_output=True, text=True, timeout=10)
                worker.join(timeout=6)
                self.assertEqual(result.returncode, 0)
                self.assertIn("relay unavailable", result.stderr)
                self.assertEqual(received, [b""])
            finally:
                listener.close()

    def test_delayed_response_survives_idle_period(self):
        spec = importlib.util.spec_from_file_location("agy_egress_delay", RELAY)
        relay = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(relay)
        with relay.Server(("127.0.0.1", 0), relay.Relay, b"synthetic-token") as defaults:
            self.assertGreaterEqual(defaults.idle_timeout, 900)
        upstream, peer = socket.socketpair()
        server = relay.Server(("127.0.0.1", 0), relay.Relay, b"synthetic-token", idle_timeout=1)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with mock.patch.object(relay.socket, "create_connection", return_value=upstream):
                with socket.socket() as client:
                    client.settimeout(2)
                    client.connect(server.server_address)
                    client.sendall(b"CONNECT www.googleapis.com:443 HTTP/1.1\r\n"
                                   b"Proxy-Authorization: Basic b3BlbmJvdDpzeW50aGV0aWMtdG9rZW4=\r\n\r\n")
                    self.assertIn(b"200", client.recv(1024))
                    time.sleep(0.2)
                    peer.sendall(b"delayed response")
                    self.assertEqual(client.recv(1024), b"delayed response")
        finally:
            peer.close()
            server.shutdown()
            server.server_close()
            worker.join(timeout=3)

    def test_authentication_and_connection_bound(self):
        spec = importlib.util.spec_from_file_location("agy_egress_relay", RELAY)
        relay = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(relay)
        server = relay.Server(("127.0.0.1", 0), relay.Relay, b"synthetic-token", limit=2)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        sockets = []
        try:
            address = server.server_address
            for header in (b"", b"Proxy-Authorization: Basic d3Jvbmc6dG9rZW4=\r\n"):
                with socket.create_connection(address, timeout=2) as client:
                    client.sendall(b"CONNECT www.googleapis.com:443 HTTP/1.1\r\n" + header + b"\r\n")
                    self.assertIn(b"407", client.recv(1024))
            for _ in range(2):
                client = socket.create_connection(address, timeout=2)
                client.sendall(b"C")
                sockets.append(client)
            deadline = time.monotonic() + 3
            while server.capacity.acquire(blocking=False):
                server.capacity.release()
                if time.monotonic() > deadline:
                    self.fail("relay did not reach its connection limit")
                time.sleep(0.01)
            with socket.create_connection(address, timeout=2) as excess:
                try:
                    excess.sendall(b"C")
                    self.assertEqual(excess.recv(1), b"")
                except ConnectionResetError:
                    pass
        finally:
            for client in sockets:
                client.close()
            server.shutdown()
            server.server_close()
            worker.join(timeout=3)

    def test_relay_scope_and_inherited_fallback(self):
        with tempfile.TemporaryDirectory() as root:
            directory = pathlib.Path(root)
            probe = directory / "curl"
            agent = directory / "agent"
            token_file = directory / "token"
            token_file.write_text("synthetic-token")
            cert_file = directory / "cert.pem"
            cert_file.write_text("synthetic-certificate")
            agent.write_text('#!/bin/sh\nprintf "{\\"proxy\\":\\"%s\\"}\\n" "$HTTPS_PROXY"\n')
            agent.chmod(0o755)
            env = dict(os.environ, PATH=root, OPENSWARM_AGY_REAL_BIN=str(agent),
                       OPENSWARM_AGY_RELAY_TOKEN_FILE=str(token_file),
                       OPENSWARM_AGY_RELAY_CERT_FILE=str(cert_file),
                       OPENSWARM_AGY_RELAY_CA_BUNDLE=str(cert_file), HTTPS_PROXY="http://inherited:9999")
            for available, expected in ((True, "https://openbot:synthetic-token@127.0.0.1:18744"),
                                        (False, "http://inherited:9999")):
                probe.write_text("#!/bin/sh\nexit %d\n" % (0 if available else 1))
                probe.chmod(0o755)
                result = subprocess.run([str(WRAPPER)], env=env, capture_output=True, text=True, check=True)
                self.assertEqual(json.loads(result.stdout)["proxy"], expected)
                self.assertEqual("relay unavailable" in result.stderr, not available)


if __name__ == "__main__":
    unittest.main()
