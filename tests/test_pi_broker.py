"""Verify that the credential broker rejects off-target requests before networking."""
import http.client
import json
import os
import pathlib
import shutil
import socket
import subprocess
import tempfile
import time
import unittest

BROKER = pathlib.Path(__file__).resolve().parents[1] / 'bin' / 'pi-minimax-broker.mjs'


class UnixHTTP(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__('localhost', timeout=3)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(3)
        self.sock.connect(self.path)


@unittest.skipUnless(shutil.which('node'), 'requires node')
class PiBrokerTests(unittest.TestCase):
    def test_beta_request_preserves_upstream_opt_in(self):
        with tempfile.TemporaryDirectory() as root:
            module = pathlib.Path(root, 'auth.mjs')
            module.write_text('export class AuthStorage { static create() { return { getApiKey: async () => "fake-key" }; } }')
            capture = pathlib.Path(root, 'request.json')
            preload = pathlib.Path(root, 'fake.cjs')
            preload.write_text('''
const https = require('node:https');
const fs = require('node:fs');
const {EventEmitter} = require('node:events');
const {PassThrough} = require('node:stream');
https.request = (options, callback) => {
  fs.writeFileSync(process.env.REQUEST_CAPTURE, JSON.stringify({path: options.path, beta: options.headers['anthropic-beta']}));
  const req = new EventEmitter(); req.destroy = () => {};
  req.end = () => { const upstream = new PassThrough(); upstream.statusCode = 200; upstream.headers = {}; upstream.complete = true; callback(upstream); upstream.end('ok'); };
  return req;
};
''')
            path = os.path.join(root, 'broker.sock')
            env = dict(os.environ, NODE_OPTIONS='--require=' + str(preload), REQUEST_CAPTURE=str(capture))
            proc = subprocess.Popen(['node', str(BROKER), path, str(module), 'allowed-model'],
                                    env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                deadline = time.monotonic() + 5
                while not os.path.exists(path) and proc.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(os.path.exists(path))
                client = UnixHTTP(path)
                client.request('POST', '/v1/messages?beta=true', json.dumps({'model': 'allowed-model', 'messages': []}),
                               {'anthropic-beta': 'interleaved-thinking-2025-05-14'})
                response = client.getresponse()
                self.assertEqual(response.status, 200)
                response.read()
                client.close()
                self.assertEqual(json.loads(capture.read_text()), {'path': '/anthropic/v1/messages?beta=true',
                    'beta': 'interleaved-thinking-2025-05-14'})
            finally:
                proc.terminate()
                proc.wait(timeout=5)

    def test_aborted_upstream_releases_busy_slot(self):
        with tempfile.TemporaryDirectory() as root:
            module = pathlib.Path(root, 'auth.mjs')
            module.write_text('export class AuthStorage { static create() { return { getApiKey: async () => "fake-key" }; } }')
            preload = pathlib.Path(root, 'fake.cjs')
            preload.write_text('''
const https = require('node:https');
const {EventEmitter} = require('node:events');
const {PassThrough} = require('node:stream');
https.request = (_options, callback) => {
  const req = new EventEmitter();
  req.destroy = () => {};
  req.end = () => {
    const upstream = new PassThrough();
    upstream.statusCode = 200; upstream.headers = {}; upstream.complete = false;
    callback(upstream); upstream.write('partial');
    setTimeout(() => upstream.destroy(), 300);
  };
  return req;
};
''')
            path = os.path.join(root, 'broker.sock')
            env = dict(os.environ, NODE_OPTIONS='--require=' + str(preload))
            proc = subprocess.Popen(['node', str(BROKER), path, str(module), 'allowed-model'],
                                    env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                deadline = time.monotonic() + 5
                while not os.path.exists(path) and proc.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(os.path.exists(path))
                client = UnixHTTP(path)
                client.request('POST', '/v1/messages?beta=true', json.dumps({'model': 'allowed-model', 'messages': []}))
                response = client.getresponse()
                busy_client = UnixHTTP(path)
                busy_client.request('POST', '/other', '{}')
                self.assertEqual(busy_client.getresponse().status, 503)
                busy_client.close()
                try:
                    response.read()
                except (http.client.IncompleteRead, ConnectionError):
                    pass
                client.close()
                time.sleep(0.1)
                client = UnixHTTP(path)
                client.request('POST', '/other', '{}')
                self.assertEqual(client.getresponse().status, 403)
                client.close()
            finally:
                proc.terminate()
                proc.wait(timeout=5)

    def test_rejects_other_paths_and_models(self):
        with tempfile.TemporaryDirectory() as root:
            module = pathlib.Path(root, 'auth.mjs')
            module.write_text('export class AuthStorage { static create() { return { getApiKey: async () => "fake-key" }; } }')
            path = os.path.join(root, 'broker.sock')
            proc = subprocess.Popen(['node', str(BROKER), path, str(module), 'allowed-model'],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                deadline = time.monotonic() + 5
                while not os.path.exists(path) and proc.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(os.path.exists(path), 'broker did not start')
                for url, body in [('/other', {'model': 'allowed-model', 'messages': []}),
                                  ('/v1/messages?beta=false', {'model': 'allowed-model', 'messages': []}),
                                  ('/v1/messages?beta=true', {'model': 'wrong-model', 'messages': []}),
                                  ('/v1/messages', {'model': 'wrong-model', 'messages': []})]:
                    client = UnixHTTP(path)
                    client.request('POST', url, json.dumps(body), {'content-type': 'application/json'})
                    response = client.getresponse()
                    self.assertEqual(response.status, 403)
                    self.assertNotIn(b'fake-key', response.read())
                    client.close()
            finally:
                proc.terminate()
                proc.wait(timeout=5)


if __name__ == '__main__':
    unittest.main()
