"""Copilot lane must not claim read-only when writes remain possible."""
import json
import os
import socket
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _load

xs = _load.load()


class CopilotReadonlyTests(unittest.TestCase):
    def test_readonly_profile_is_not_routed_to_copilot(self):
        decision, reason = xs.plan_copilot({'read_only': True, 'difficulty': 3}, {}, xs.load_policy(), True, [])
        self.assertIsNone(decision)
        self.assertIn('read-only isolation is unavailable', reason)

    def test_readonly_assignment_is_rejected(self):
        server = socket.socket()
        server.bind(('127.0.0.1', 0))
        server.listen()
        client = socket.create_connection(server.getsockname())
        try:
            client.sendall((json.dumps({'op': 'assign', 'read_only': True, 'run_id': 'test'}) + '\n').encode())
            lane = {'name': 'GH1', 'backend': 'copilot'}
            with mock.patch.object(xs, 'jload', return_value={'state': 'idle'}), \
                    mock.patch.object(xs.shutil, 'which', return_value=None):
                xs.lane_control_messages(server, lane, {}, None, {}, [])
            answer = json.loads(client.recv(4096))
            self.assertFalse(answer['ok'])
            self.assertIn('read-only isolation is unavailable', answer['error'])
        finally:
            client.close()
            server.close()


if __name__ == '__main__':
    unittest.main()
