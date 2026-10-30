#!/usr/bin/env python3
"""Loopback HTTP CONNECT relay for Antigravity's Google endpoints."""
import argparse
import base64
import hmac
import select
import socket
import socketserver
import threading


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
            headers = data.split(b"\r\n\r\n", 1)[0].split(b"\r\n")
            credentials = [line.split(b":", 1)[1].strip() for line in headers[1:]
                           if line.partition(b":")[0].strip().lower() == b"proxy-authorization"]
            expected = b"Basic " + base64.b64encode(b"openbot:" + self.server.token)
            if len(credentials) != 1 or not hmac.compare_digest(credentials[0], expected):
                self.request.sendall(b"HTTP/1.1 407 Proxy Authentication Required\r\nContent-Length: 0\r\n\r\n")
                return
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

    def __init__(self, address, handler, token, limit=24):
        self.token = token
        self.capacity = threading.BoundedSemaphore(limit)
        super().__init__(address, handler)

    def process_request(self, request, client_address):
        if not self.capacity.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.capacity.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.capacity.release()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18743)
    parser.add_argument("--token-file", required=True)
    args = parser.parse_args()
    with open(args.token_file, "rb") as token_file:
        token = token_file.read().strip()
    if not token:
        parser.error("token file is empty")
    with Server(("127.0.0.1", args.port), Relay, token) as server:
        print("Antigravity relay listening on loopback port %d" % server.server_address[1], flush=True)
        server.serve_forever()
