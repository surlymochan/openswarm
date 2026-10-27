import json
import os
import pathlib
import tempfile
import unittest
from unittest.mock import patch

import _load

xs = _load.load()
ROOT = pathlib.Path(__file__).resolve().parents[1]
FAKE = ROOT / 'tests/fixtures/fake_cursor_acp.py'


class HermesTests(unittest.TestCase):
    def test_one_shot_and_environment(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            root = pathlib.Path(tmp)
            auth = root / '.pi/agent/auth.json'
            auth.parent.mkdir(parents=True)
            auth.write_text(json.dumps({'minimax-cn': {'type': 'api_key', 'key': 'fake-secret-abc'}}))
            log = root / 'fake-log'
            cfg = {'hermes': {'binary': str(FAKE), 'provider': 'minimax-cn', 'key_env': 'CUSTOM_KEY'}}
            env = {'HOME': tmp, 'FAKE_CURSOR_ACP_LOG': str(log), 'FAKE_EXPECTED_KEY': 'fake-secret-abc',
                   'FAKE_KEY_ENV': 'CUSTOM_KEY'}
            with patch.dict(os.environ, env), patch.object(xs, 'load_policy', return_value=cfg):
                self.assertEqual(xs.minimax_key_from_pi(), 'fake-secret-abc')
                result = xs.exec_hermes({'model': None}, {}, xs.REPORT_INSTRUCTION, tmp, tmp, 10, str(root / 'events'))
            self.assertEqual(result[0], 0, result)
            self.assertEqual(result[1]['status'], 'done')
            entries = [json.loads(line) for line in log.read_text().splitlines()]
            self.assertTrue(entries[0]['key_matches'])
            self.assertEqual(entries[0]['argv'][1:], ['acp'])
            self.assertTrue(any(e.get('method') == 'session/prompt' for e in entries))
            self.assertNotIn('fake-secret-abc', (root / 'events').read_text() + json.dumps(result) + log.read_text())
            self.assertEqual(xs.execution_provider('hermes'), 'minimax-cn')

    def test_cancel_and_continue(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            cfg = {'hermes': {'binary': str(FAKE)}}
            log = pathlib.Path(tmp) / 'log'
            with patch.dict(os.environ, {'FAKE_CURSOR_ACP_LOG': str(log)}), patch.object(xs, 'minimax_key_from_pi', return_value=None):
                child = xs.LaneChild({'backend': 'hermes', 'model': None}, tmp, cfg)
                try:
                    turn = {}
                    event = str(pathlib.Path(tmp) / 'events')
                    xs.lane_begin_turn({'backend': 'hermes', 'model': None}, child, 'LANE_TEST_WAIT', tmp, event, turn)
                    xs.lane_abort_child({'backend': 'hermes'}, child, turn)
                    reply = child.wait_response(turn['id'], 5)
                    self.assertEqual(reply['result']['stopReason'], 'cancelled')
                    xs.lane_continue_turn({'backend': 'hermes'}, child, xs.REPORT_INSTRUCTION, event, turn)
                    reply = child.wait_response(turn['id'], 5)
                    self.assertEqual(reply['result']['stopReason'], 'end_turn')
                    chunks = [e for e in child.backlog if e and e.get('method') == 'session/update']
                    self.assertTrue(any((e.get('params', {}).get('update', {}).get('sessionUpdate') == 'agent_message_chunk') for e in chunks))
                finally:
                    child.close()
            methods = [json.loads(line).get('method') for line in log.read_text().splitlines()]
            self.assertIn('session/cancel', methods)
            self.assertEqual(methods.count('session/prompt'), 2)


if __name__ == '__main__':
    unittest.main()
