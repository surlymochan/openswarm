"""Weekly retro aggregation and orchestration use fixture ledgers only."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest import mock

import _load

xs = _load.load()


class RetroTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="openswarm-retro-")
        self.root = Path(self.tmp.name)
        self.knowledge = self.root / "knowledge"
        self.knowledge.mkdir()
        self.policy = {"knowledge": {"repo": str(self.knowledge)}, "timezone": "UTC"}
        self.constants = {key: getattr(xs, key) for key in ("DATA", "LEDGER", "HOME", "DEFAULT_KNOWLEDGE_REPO")}
        xs.DATA = str(self.root / "data")
        xs.LEDGER = str(self.root / "data" / "ledger.jsonl")
        xs.HOME = str(self.root / "home")
        xs.DEFAULT_KNOWLEDGE_REPO = str(self.knowledge)
        self.now = 1_800_000_000
        self.write_ledger(
            {"id": "pass-1", "ts": self.now - 100, "status": "done", "backend": "codex",
             "model": "gpt-test", "card_type": "fix", "exec_pass": True, "accepted": True,
             "routing_advice": {"backend": "codex", "model": "gpt-test"}},
            {"id": "reject-2", "ts": self.now - 200, "status": "failed", "backend": "codex",
             "model": "gpt-test", "card_type": "fix", "exec_pass": False, "accepted": False,
             "accepted_reason": "tests failed", "error_class": "exec_error",
             "routing_advice": {"backend": "pi", "model": "pi-test"}},
            {"id": "run-3", "ts": self.now - 300, "status": "done", "backend": "pi",
             "model": "pi-test", "card_type": "docs"},
        )

    def tearDown(self):
        self.tmp.cleanup()
        for key, value in self.constants.items():
            setattr(xs, key, value)

    def write_ledger(self, *entries):
        path = Path(xs.fleet_ledger_path())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(entry) + "\n" for entry in entries), encoding="utf-8")
        return path

    def run_retro(self, *args, policy=None, step=None):
        original_argv = sys.argv
        output, error = io.StringIO(), io.StringIO()
        calls = []

        def fake_step(name, step_args, policy_arg):
            calls.append((name, step_args))
            if step:
                result = step(name, step_args, policy_arg)
                if result is not None:
                    return result
            if name == "lessons escalate":
                return False
            return True

        try:
            sys.argv = ["openswarm", "retro", *args]
            with mock.patch.object(xs, "load_policy", return_value=policy or self.policy), \
                    mock.patch.object(xs, "NOW", return_value=self.now), \
                    mock.patch.object(xs, "retro_step", side_effect=fake_step), \
                    contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
                try:
                    xs.main()
                except SystemExit as exc:
                    return exc.code, output.getvalue(), error.getvalue(), calls
            return 0, output.getvalue(), error.getvalue(), calls
        finally:
            sys.argv = original_argv

    def test_writes_markdown_sections_and_fixture_counts_with_no_pull(self):
        code, output, error, calls = self.run_retro("--days", "7", "--repos", "octo/one", "octo/two", "--no-pull")

        self.assertEqual((code, error), (0, ""))
        summary = json.loads(output)
        self.assertEqual(summary["counts"], {"runs": 3, "groups": 2, "failures": 1, "rejected": 1,
                                             "lesson_proposals": 0, "guard_proposals": 0,
                                             "routing_comparisons": 2})
        self.assertFalse(any(name == "fleet ledger pull" for name, _ in calls))
        ingest = next(args for name, args in calls if name == "ledger ingest")
        self.assertEqual(ingest.repo, ["octo/one", "octo/two"])
        self.assertEqual(ingest.since, 7)

        report = Path(summary["paths"]["report"]).read_text(encoding="utf-8")
        for heading in ("Runs by backend/model/card_type", "Top failure error_classes", "Rejected runs",
                        "New lesson and guard proposals", "Routing advice vs actual", "Proposed changes"):
            self.assertIn(heading, report)
        self.assertIn("1/2 (50%)", report)
        self.assertIn("exec_error", report)
        self.assertIn("tests failed", report)
        self.assertIn("Skipped fleet ledger pull (--no-pull).", report)
        self.assertIn("Skipped lessons escalate", report)

    def test_runs_pull_when_enabled(self):
        code, output, error, calls = self.run_retro()

        self.assertEqual((code, error), (0, ""))
        self.assertIn("fleet ledger pull", [name for name, _ in calls])
        self.assertEqual(json.loads(output)["counts"]["runs"], 3)

    def test_missing_knowledge_repo_fails_before_running_steps(self):
        missing = {"knowledge": {"repo": str(self.root / "missing")}}
        code, output, error, calls = self.run_retro(policy=missing)

        self.assertEqual(code, 2)
        self.assertEqual(output, "")
        self.assertIn("knowledge repo not found", error)
        self.assertEqual(calls, [])

    def test_rate_limit_is_noted_and_later_steps_continue(self):
        def rate_limited(name, _args, _policy):
            if name == "ledger ingest":
                raise xs.BusError("HTTP 403: API rate limit exceeded")
            return None

        code, output, error, calls = self.run_retro("--no-pull", step=rate_limited)

        self.assertEqual((code, error), (0, ""))
        names = [name for name, _ in calls]
        self.assertIn("ledger backfill-pass", names)
        self.assertIn("routing weights", names)
        self.assertIn("lessons propose", names)
        self.assertIn("routing compare", names)
        self.assertIn("rate limit", Path(json.loads(output)["paths"]["report"]).read_text(encoding="utf-8"))
        self.assertTrue(json.loads(output)["steps"]["ledger ingest"]["rate_limited"])

    def test_escalate_skips_without_command_and_invokes_cli_when_available(self):
        args = type("Args", (), {})()
        with mock.patch.object(xs, "cmd_lessons_escalate", None, create=True):
            self.assertFalse(xs.retro_step("lessons escalate", args, self.policy))

        result = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with mock.patch.object(xs, "cmd_lessons_escalate", lambda *_: None, create=True), \
                mock.patch.object(xs.subprocess, "run", return_value=result) as run:
            self.assertTrue(xs.retro_step("lessons escalate", args, self.policy))
        self.assertEqual(run.call_args.args[0][-2:], ["lessons", "escalate"])


if __name__ == "__main__":
    unittest.main()
