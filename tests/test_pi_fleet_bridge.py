"""Smoke tests for bin/pi-fleet-bridge: argparse validation only.

Runs the script with invalid arguments against a fake HOME, so no SSH
connection is ever attempted: every case below fails in argparse
validation (exit 2) before the script reaches the broker/worker hosts.

The full orchestration (fake-ssh harness driving the bridge, socket
forwarding and teardown) is explicitly out of scope here; see issue #71
§4 for that follow-up.
"""
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / 'bin' / 'pi-fleet-bridge'


def _temp_home():
    """Short temp HOME so paths stay short; on Windows there is no /tmp,
    so fall back to the platform default temp dir (same shape as the
    pi-broker-service smoke tests)."""
    if os.name == 'nt':
        return tempfile.TemporaryDirectory(prefix='os-')
    return tempfile.TemporaryDirectory(prefix='os-', dir='/tmp')


class PiFleetBridgeArgparseTests(unittest.TestCase):
    def _run(self, *args):
        with _temp_home() as home:
            env = dict(os.environ, HOME=home)
            return subprocess.run([sys.executable, str(SCRIPT), *args],
                                  stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE,
                                  env=env, timeout=15)

    def test_illegal_broker_host_char_exits_2(self):
        proc = self._run('--broker-host', 'bad;host')
        self.assertEqual(proc.returncode, 2, proc.stderr.decode())
        self.assertIn(b'host aliases and model', proc.stderr)

    def test_illegal_worker_char_exits_2(self):
        proc = self._run('--worker', 'bad host')
        self.assertEqual(proc.returncode, 2, proc.stderr.decode())
        self.assertIn(b'host aliases and model', proc.stderr)

    def test_illegal_model_char_exits_2(self):
        proc = self._run('--model', 'bad/model')
        self.assertEqual(proc.returncode, 2, proc.stderr.decode())
        self.assertIn(b'host aliases and model', proc.stderr)

    def test_empty_broker_host_exits_2(self):
        proc = self._run('--broker-host', '')
        self.assertEqual(proc.returncode, 2, proc.stderr.decode())
        self.assertIn(b'host aliases and model', proc.stderr)

    def test_minutes_zero_exits_2(self):
        proc = self._run('--minutes', '0')
        self.assertEqual(proc.returncode, 2, proc.stderr.decode())
        self.assertIn(b'minutes must be between 1 and 480', proc.stderr)

    def test_minutes_481_exits_2(self):
        proc = self._run('--minutes', '481')
        self.assertEqual(proc.returncode, 2, proc.stderr.decode())
        self.assertIn(b'minutes must be between 1 and 480', proc.stderr)

    def test_minutes_negative_exits_2(self):
        proc = self._run('--minutes', '-5')
        self.assertEqual(proc.returncode, 2, proc.stderr.decode())
        self.assertIn(b'minutes must be between 1 and 480', proc.stderr)

    def test_broker_equals_worker_exits_2(self):
        proc = self._run('--broker-host', 'gocc', '--worker', 'gocc')
        self.assertEqual(proc.returncode, 2, proc.stderr.decode())
        self.assertIn(b'broker and worker must use separate hosts',
                      proc.stderr)
