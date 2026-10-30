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

    def test_sandbox_bypass_is_not_automatically_approved(self):
        with tempfile.TemporaryDirectory() as tmp:
            child = mock.Mock()
            child.lane = {"backend": "copilot"}
            msg = {"id": 8, "method": "session/request_permission", "params": {
                "toolCall": {"rawInput": {"requestSandboxBypass": True}},
                "options": [{"kind": "allow_once", "optionId": "once"}]}}
            self.assertTrue(xs.lane_acp_request(child, msg, os.path.join(tmp, 'events')))
            self.assertEqual(child.send.call_args.args[0]['result']['outcome']['outcome'], 'cancelled')

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

    def test_difficulty_selects_requested_model_and_effort(self):
        with mock.patch.object(xs, "copilot_bin", return_value="/bin/true"):
            policy = xs.load_policy()
            routine, _ = xs.plan_copilot({"difficulty": 3}, {}, policy, True, [])
            complex_task, _ = xs.plan_copilot({"difficulty": 4}, {}, policy, True, [])
        self.assertEqual((routine["model"], routine["effort"]), ("claude-haiku-5.5", "medium"))
        self.assertEqual((complex_task["model"], complex_task["effort"]), ("claude-sonnet-5.5", "medium"))

    def test_complex_task_uses_default_when_override_is_missing(self):
        policy = xs.load_policy()
        policy["copilot"].pop("complex_model", None)
        with mock.patch.object(xs, "copilot_bin", return_value="/bin/true"):
            decision, reason = xs.plan_copilot({"difficulty": 4}, {}, policy, True, [])
        self.assertIsNone(reason)
        self.assertEqual(decision["model"], policy["copilot"]["default_model"])


if __name__ == "__main__":
    unittest.main()
