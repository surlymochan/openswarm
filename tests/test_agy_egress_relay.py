import json
import importlib.util
import os
import pathlib
import socket
import subprocess
import tempfile
import threading
import time
import unittest


WRAPPER = pathlib.Path(__file__).resolve().parents[1] / "bin" / "agy-via-relay"
RELAY = WRAPPER.with_name("agy-egress-relay.py")


class AgyRelayTests(unittest.TestCase):
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
            agent.write_text('#!/bin/sh\nprintf "{\\"proxy\\":\\"%s\\"}\\n" "$HTTPS_PROXY"\n')
            agent.chmod(0o755)
            env = dict(os.environ, PATH=root, OPENSWARM_AGY_REAL_BIN=str(agent),
                       OPENSWARM_AGY_RELAY_TOKEN_FILE=str(token_file), HTTPS_PROXY="http://inherited:9999")
            for available, expected in ((True, "http://openbot:synthetic-token@127.0.0.1:18744"),
                                        (False, "http://inherited:9999")):
                probe.write_text("#!/bin/sh\nexit %d\n" % (0 if available else 1))
                probe.chmod(0o755)
                result = subprocess.run([str(WRAPPER)], env=env, capture_output=True, text=True, check=True)
                self.assertEqual(json.loads(result.stdout)["proxy"], expected)
                self.assertEqual("relay unavailable" in result.stderr, not available)


if __name__ == "__main__":
    unittest.main()
