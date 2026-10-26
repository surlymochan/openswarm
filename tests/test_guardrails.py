"""Policy guardrails and reports use fixture ledgers and fake executors only."""
import argparse
import contextlib
import datetime as dt
import io
import json
import os
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest
from unittest import mock

import _load

xs = _load.load()


class GuardrailsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name, value in (("DATA", str(self.root)), ("RUNS", str(self.root / "runs")),
                            ("LEDGER", str(self.root / "ledger.jsonl"))):
            patch = mock.patch.object(xs, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        self.now = dt.datetime(2026, 10, 26, 16, 30, tzinfo=dt.timezone.utc).timestamp()
        patch = mock.patch.object(xs, "NOW", return_value=self.now)
        patch.start()
        self.addCleanup(patch.stop)
        self.policy = xs.deep_merge(xs.load_policy(), {"budget": {"timezone": "Asia/Singapore",
                                   "daily_executor_minutes": 10, "per_backend": {"pi": 6}, "per_run_max_minutes": 5}})

    def ledger(self, entries):
        Path(xs.LEDGER).write_text("\n".join(json.dumps(e) for e in entries) + "\n")

    def test_budget_uses_policy_day_and_all_attempt_backends(self):
        self.ledger([{"ts": self.now, "backend": "pi", "seconds": 180,
                      "attempts": [{"backend": "codex", "seconds": 60}, {"backend": "pi", "seconds": 120}]},
                     {"ts": self.now - 3600, "backend": "pi", "seconds": 900}])
        status = xs.budget_status(self.policy)
        self.assertEqual(status["date"], "2026-10-27")
        self.assertEqual(status["used_minutes"], 3)
        self.assertEqual(status["per_backend"]["pi"], {"used_minutes": 2, "cap_minutes": 6})
        self.assertEqual(status["per_backend"]["codex"]["used_minutes"], 1)
        xs.check_budget(self.policy, [("pi", 4)])
        with self.assertRaises(xs.BudgetExceeded) as e:
            xs.check_budget(self.policy, [("pi", 5)])
        self.assertEqual(e.exception.detail["scope"], "backend")
        with self.assertRaises(xs.BudgetExceeded) as e:
            xs.check_budget(self.policy, [("codex", 4), ("codex", 4)])
        self.assertEqual(e.exception.detail["scope"], "daily")
        with self.assertRaises(xs.BudgetExceeded) as e:
            xs.check_budget(self.policy, [("codex", 6)])
        self.assertEqual(e.exception.detail["scope"], "per_run")

    def test_budget_reserves_concurrent_admissions_and_releases_finished(self):
        import os
        xs.admit_budget(self.policy, "pi", 5, str(self.root / "runs" / "first"),
                        id="first", state="queued", runner_pid=os.getpid())
        with self.assertRaises(xs.BudgetExceeded):
            xs.admit_budget(self.policy, "pi", 2, str(self.root / "runs" / "second"),
                            id="second", state="queued", runner_pid=os.getpid())
        self.ledger([{"id": "first", "ts": self.now, "backend": "pi", "seconds": 60}])
        xs.check_budget(self.policy, [("pi", 5)])

    def test_run_and_detach_refuse_before_creating_workspace(self):
        self.ledger([{"ts": self.now, "backend": "pi", "seconds": 600}])
        for detach in (False, True):
            args = argparse.Namespace(run_id=None, prompt="task", task_file=None, title="test", cwd=str(self.root),
                                      type="impl", difficulty=3, context_tokens=0, backend="pi", model=None,
                                      effort=None, budget_minutes=1, detach=detach, dry_run=False)
            output = io.StringIO()
            with mock.patch.object(xs, "get_state", return_value={}), \
                    mock.patch.object(xs, "route", return_value={"ok": True, "backend": "pi"}), \
                    mock.patch.object(xs, "prepare_workspace") as prepare, \
                    contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as e:
                xs.cmd_run(args, self.policy)
            self.assertEqual(e.exception.code, 3)
            self.assertEqual(json.loads(output.getvalue())["error"], "budget_exceeded_daily")
            prepare.assert_not_called()

    def test_budget_status_json(self):
        self.ledger([{"ts": self.now, "backend": "cursor", "seconds": 120}])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            xs.cmd_budget(argparse.Namespace(json=True), self.policy)
        self.assertEqual(json.loads(output.getvalue())["per_backend"]["cursor"]["used_minutes"], 2)

    def test_fallback_checks_new_backend_cap_before_executing(self):
        policy = xs.deep_merge(self.policy, {"budget": {"per_backend": {"codex": 0}}})
        args = argparse.Namespace(run_id=None, prompt="task", task_file=None, title="fallback", cwd=str(self.root),
                                  type="impl", difficulty=3, context_tokens=0, backend=None, model=None, effort=None,
                                  budget_minutes=5, detach=False, dry_run=False, no_worktree=True, base=None,
                                  verify=None, no_fallback=False, keep_worktree=False)
        decisions = [{"ok": True, "backend": b, "model": "fake"} for b in ("pi", "codex")]
        executor = mock.Mock(return_value=(75, None, {"usage": {}}, "rate limit"))
        next_executor = mock.Mock()
        output = io.StringIO()
        with mock.patch.object(xs, "get_state", return_value={}), mock.patch.object(xs, "probe", return_value={}), \
                mock.patch.object(xs, "route", side_effect=decisions), mock.patch.object(xs, "trip_breaker"), \
                mock.patch.dict(xs.EXECUTORS, {"pi": executor, "codex": next_executor}), \
                contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as e:
            xs.cmd_run(args, policy)
        self.assertEqual(e.exception.code, 3)
        self.assertEqual(json.loads(output.getvalue())["error_class"], "budget_exceeded_daily")
        executor.assert_called_once()
        next_executor.assert_not_called()

    def test_enforced_risks_all_protocols_and_policy_opt_out(self):
        events = [
            {"type": "item.started", "item": {"type": "command_execution", "command": "git -C repo push origin"}},
            {"type": "tool_use", "part": {"tool": "bash", "state": {"input": {"command": "npm --access public publish"}}}},
            {"type": "tool_execution_start", "args": {"command": "echo $API_KEY"}},
            {"type": "tool_call", "tool_call": {"shellToolCall": {"args": {"command": "sudo run private"}}}},
            {"method": "session/request_permission", "params": {"toolCall": {"title": "Shell", "rawInput": {"command": "rm local /outside"}}}},
            {"method": "session/update", "params": {"update": {"sessionUpdate": "tool_call", "rawInput": {"command": "rm -rf ../outside"}}}},
        ]
        for event, expected in zip(events, ("push", "publish", "secret print", "sudo", "rm outside worktree", "rm outside worktree")):
            self.assertEqual(xs.enforced_risk(event, {}, str(self.root))["risk"], expected)
            self.assertIsNone(xs.enforced_risk(event, {"risk": {"enforce": []}}, str(self.root)))
        for command in ("rm -rf local", f"rm -rf {self.root}/local", "git grep push", "git log --grep=push",
                        "rg 'sudo|git push|npm publish' source.py", "echo 'sudo please'", "echo rm /outside",
                        "git status; echo 'git push'", "rm local > /outside"):
            self.assertIsNone(xs.enforced_risk({"type": "tool_execution_start", "args": {"command": command}}, {}, str(self.root)))
        self.assertTrue(xs.rm_outside_worktree("cd /tmp && rm -rf file", str(self.root)))
        self.assertTrue(xs.rm_outside_worktree("bash -lc 'rm -rf /outside'", str(self.root)))
        self.assertTrue(xs.rm_outside_worktree("cd /tmp && bash -lc 'rm file'", str(self.root)))
        self.assertTrue(xs.enforced_risk({"type": "tool_execution_start", "args": {"command": "bash -lc 'git push origin'"}}, {}, str(self.root)))
        self.assertEqual(xs.enforced_risk({"type": "tool_execution_start", "args": {"command": "cat .env"}}, {}, str(self.root))["risk"], "secret print")

    def report_fixture(self):
        self.ledger([
            {"id": "a", "ts": self.now, "status": "done", "backend": "codex", "lane": "C1", "seconds": 60,
             "usage": {"input_tokens": 100, "cached_input_tokens": 80, "output_tokens": 20}, "summary": "first done"},
            {"id": "b", "ts": self.now, "status": "partial", "backend": "pi", "lane": "M1", "seconds": 120,
             "usage": {"input": 3, "output": 2, "cacheRead": 7, "cacheWrite": 1, "totalTokens": 13}, "summary": "partial change"},
            {"id": "c", "ts": self.now, "status": "failed", "backend": "cursor", "seconds": 180,
             "usage": {"inputTokens": 4, "outputTokens": 6, "cacheReadTokens": 2, "cacheWriteTokens": 1},
             "summary": "<script>alert('unsafe')</script>\nfailed"},
            {"id": "d", "ts": self.now, "status": "stopped", "backend": "pi", "lane": "M1", "seconds": 30,
             "usage": {"input": 10}, "error_class": "risk_blocked", "risk_blocks": [{"risk": "push", "command": "git push [redacted]"}]},
            {"id": "e", "ts": self.now, "status": "done", "backend": "pi", "seconds": 60,
             "attempts": [{"backend": "codex", "seconds": 30, "meta": {"usage": {"input_tokens": 2, "output_tokens": 1}}},
                          {"backend": "pi", "seconds": 30, "meta": {"usage": {"input": 2, "output": 3}}}]},
            {"id": "yesterday", "ts": self.now - 86400, "status": "failed", "backend": "pi", "seconds": 600},
        ])
        xs.jsave(str(self.root / "runs" / "e" / "result.json"), {"result": {"summary": "old ledger summary"}})

    def test_daily_report_counts_backend_lane_usage_and_risks(self):
        self.report_fixture()
        report = xs.daily_report(self.policy, "2026-10-27")
        self.assertEqual(report["totals"], {"runs": 5, "counts": {"done": 2, "partial": 1, "failed": 1, "stopped": 1},
                                           "executor_minutes": 7.5, "tokens": 164, "risk_blocks": 1})
        self.assertEqual(report["per_backend"]["codex"]["runs"], 2)
        self.assertEqual(report["per_backend"]["codex"]["tokens"], 123)
        self.assertEqual(report["per_lane"]["M1"]["runs"], 2)
        self.assertEqual(report["per_lane"]["M1"]["executor_minutes"], 2.5)
        self.assertEqual(report["budget"]["used_minutes"], 7.5)
        self.assertEqual(report["risks"], {"push": 1})
        self.assertEqual(report["slowest_runs"][0]["id"], "c")
        self.assertEqual([r["id"] for r in report["failed_runs"]], ["c"])
        self.assertIn("old ledger summary", json.dumps(report))

    def test_daily_cli_html_and_json_are_offline_and_escape_summaries(self):
        self.report_fixture()
        (self.root / "policy.local.json").write_text(json.dumps({"budget": self.policy["budget"]}))
        target = self.root / "report.html"
        result = subprocess.run([sys.executable, str(Path(_load.ROOT) / "bin" / "openswarm"), "report", "--daily",
                                 "--date", "2026-10-27", "--html", str(target), "--json"],
                                env={**os.environ, "OPENSWARM_HOME": str(self.root)}, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["totals"]["runs"], 5)
        html = target.read_text()
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<script>", html)
        self.assertIn("prefers-color-scheme:dark", html)
        self.assertIn("Runs by backend", html)
        self.assertIn("Budget use", html)
        self.assertNotIn('src="http', html)
        self.assertNotIn('href="http', html)

    def test_empty_daily_report_and_corrupt_ledger_line(self):
        Path(xs.LEDGER).write_text('{"truncated":\n')
        report = xs.daily_report(self.policy)
        self.assertEqual(report["totals"]["runs"], 0)
        self.assertEqual(report["slowest_runs"], [])
        self.assertIn("No runs", xs.daily_report_html(report))
