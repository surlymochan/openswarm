#!/usr/bin/env python3
"""Loopback HTTP CONNECT relay for Antigravity's Google endpoints."""
import argparse
import select
import socket
import socketserver


class Relay(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(10)
        data = b""
        while b"\r\n\r\n" not in data and len(data) < 16384:
            part = self.request.recv(4096)
            if not part:
                return
            data += part
        if b"\r\n\r\n" not in data:
            return
        try:
            method, target, _version = data.split(b"\r\n", 1)[0].decode("ascii").split()
            host, separator, port = target.rpartition(":")
            host = host.lower()
            if method != "CONNECT" or not separator or port != "443":
                return
            if not (host.endswith(".googleapis.com") or host == "accounts.google.com"):
                return
            with socket.create_connection((host, 443), timeout=10) as upstream:
                self.request.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                remainder = data.split(b"\r\n\r\n", 1)[1]
                if remainder:
                    upstream.sendall(remainder)
                self.request.settimeout(None)
                upstream.settimeout(None)
                while True:
                    ready, _, _ = select.select([self.request, upstream], [], [], 60)
                    if not ready:
                        return
                    for source in ready:
                        chunk = source.recv(65536)
                        if not chunk:
                            return
                        (upstream if source is self.request else self.request).sendall(chunk)
        except (OSError, UnicodeError, ValueError):
            return


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18743)
    args = parser.parse_args()
    with Server(("127.0.0.1", args.port), Relay) as server:
        print("Antigravity relay listening on loopback port %d" % server.server_address[1], flush=True)
        server.serve_forever()
