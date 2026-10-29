"""Exercise Copilot's JSONL adapter with a controlled local process."""
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _load

xs = _load.load()


class CopilotTests(unittest.TestCase):
    def test_lane_permission_does_not_grant_persistent_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            child = mock.Mock()
            child.lane = {"backend": "copilot"}
            msg = {"id": 7, "method": "session/request_permission", "params": {"options": [
                {"kind": "allow_always", "optionId": "always"},
                {"kind": "allow_once", "optionId": "once"}]}}
            self.assertTrue(xs.lane_acp_request(child, msg, os.path.join(tmp, 'events')))
            self.assertEqual(child.send.call_args.args[0]['result']['outcome']['optionId'], 'once')

    def test_result_and_error_are_not_confused(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = pathlib.Path(tmp, "copilot")
            binary.write_text("#!/usr/bin/env python3\n"
                              "import json, os\n"
                              "print(json.dumps({'type':'assistant.usage','data':{'inputTokens':3}}))\n"
                              "print(json.dumps({'type':'assistant.message','data':{'content':"
                              "json.dumps({'status':'done','summary':'ok','files_changed':[],"
                              "'tests':'none','followups':[]})}}))\n"
                              "print(json.dumps({'type':'result','exitCode':0}))\n"
                              "if os.environ.get('FAKE_SESSION_ERROR'):"
                              " print(json.dumps({'type':'session.error','data':{'message':'failure'}}))\n")
            binary.chmod(0o755)
            events = os.path.join(tmp, "events")
            with mock.patch.dict(os.environ, {"OPENSWARM_COPILOT_BIN": str(binary)}, clear=False):
                rc, result, meta, _ = xs.exec_copilot({"model": "test-model"}, {}, "test", tmp, tmp, 5, events)
                self.assertEqual(rc, 0)
                self.assertEqual(result["status"], "done")
                self.assertEqual(meta["usage"]["inputTokens"], 3)
                with mock.patch.dict(os.environ, {"FAKE_SESSION_ERROR": "1"}):
                    rc, _, _, _ = xs.exec_copilot({"model": "test-model"}, {}, "test", tmp, tmp, 5, events)
                    self.assertNotEqual(rc, 0)

    def test_missing_model_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(xs, "copilot_bin", return_value="/bin/true"):
                policy = xs.load_policy()
                policy["copilot"]["default_model"] = None
                decision, reason = xs.plan_copilot({"difficulty": 3}, {}, policy, True, [])
            self.assertIsNone(decision)
            self.assertIn("verified model", reason)


if __name__ == "__main__":
    unittest.main()
