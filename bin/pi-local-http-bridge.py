#!/usr/bin/env python3
"""Expose the private Pi broker to a local Pi client through a supervised SSH relay."""
import argparse
import hmac
import http.server
import os
from pathlib import Path
import signal
import subprocess
import threading

parser = argparse.ArgumentParser()
parser.add_argument('--port', type=int, default=8742)
parser.add_argument('--token-file', required=True, type=Path)
parser.add_argument('--broker-host', default='gocc')
parser.add_argument('--ssh', default='/usr/bin/ssh')
args = parser.parse_args()
token = args.token_file.read_text(encoding='utf-8').strip()
if not token:
    raise SystemExit('local broker token is empty')


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, format, *args):
        pass

    def do_POST(self):
        if not hmac.compare_digest(self.headers.get('x-api-key', ''), token):
            self.send_error(403)
            return
        if self.path not in ('/v1/messages', '/v1/messages?beta=true'):
            self.send_error(404)
            return
        try:
            size = int(self.headers.get('content-length', ''))
        except ValueError:
            self.send_error(400)
            return
        if size < 1 or self.headers.get('transfer-encoding'):
            self.send_error(400)
            return
        proc = subprocess.Popen([args.ssh, '-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
                                 args.broker_host, '~/tools/openswarm-executor-candidate/bin/pi-broker-stdio'],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        with self.server.children_lock:
            self.server.children.add(proc)
        self.close_connection = True

        def forward_request():
            try:
                lines = [f'POST {self.path} HTTP/1.1', 'Host: localhost', f'Content-Length: {size}',
                         'Content-Type: application/json', 'Connection: close']
                beta = self.headers.get('anthropic-beta')
                if beta:
                    lines.append(f'anthropic-beta: {beta}')
                proc.stdin.write(('\r\n'.join(lines) + '\r\n\r\n').encode('ascii'))
                remaining = size
                while remaining:
                    chunk = self.rfile.read(min(65536, remaining))
                    if not chunk:
                        proc.terminate()
                        return
                    proc.stdin.write(chunk)
                    remaining -= len(chunk)
                # Keep SSH stdin open while the broker is waiting for its upstream response.
                proc.stdin.flush()
            except (OSError, ValueError):
                pass

        sender = threading.Thread(target=forward_request, daemon=True)
        sender.start()
        try:
            while True:
                chunk = os.read(proc.stdout.fileno(), 65536)
                if not chunk:
                    break
                self.connection.sendall(chunk)
        except OSError:
            pass
        finally:
            if proc.poll() is None:
                proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            proc.stdout.close()
            proc.stdin.close()
            with self.server.children_lock:
                self.server.children.discard(proc)


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, *values):
        self.children = set()
        self.children_lock = threading.Lock()
        super().__init__(*values)


def stop(_signal, _frame):
    raise SystemExit(0)


with Server(('127.0.0.1', args.port), Handler) as server:
    signal.signal(signal.SIGTERM, stop)
    print(f'READY {server.server_address[1]}', flush=True)
    try:
        server.serve_forever()
    finally:
        with server.children_lock:
            for proc in server.children:
                if proc.poll() is None:
                    proc.terminate()
