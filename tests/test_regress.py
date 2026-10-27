"""Regression runner cards reuse openswarm's normal isolated run path."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import _load

xs = _load.load()


class RegressTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="openswarm-regress-")
        self.root = Path(self.tmp.name)
        self.knowledge = self.root / "knowledge"
        self.regression = self.knowledge / "regression"
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.data = self.root / "data"
        self.constants = {key: getattr(xs, key) for key in ("DATA", "LEDGER", "RUNS", "WORKTREES", "HOME")}
        xs.DATA, xs.LEDGER = str(self.data), str(self.data / "ledger.jsonl")
        xs.RUNS, xs.WORKTREES = str(self.data / "runs"), str(self.data / "worktrees")
        xs.HOME = str(self.root / "home")
        self.git("init", "-q")
        self.git("config", "user.name", "Regression Test")
        self.git("config", "user.email", "regression@example.invalid")
        (self.repo / "base.txt").write_text("base\n", encoding="utf-8")
        self.git("add", "base.txt")
        self.git("commit", "-q", "-m", "base")
        self.base = self.git("rev-parse", "HEAD").strip()
        (self.knowledge / "lessons").mkdir(parents=True)
        (self.knowledge / "lessons" / "lesson.yaml").write_text(
            "id: L-9001\ndate: 2026-10-27\nrule: Keep the regression focused.\n"
            "guard_level: L2\napplies_to:\n  card_types: [fix]\n  paths: []\n  backends: [codex]\n",
            encoding="utf-8")
        self.policy = {"knowledge": {"repo": str(self.knowledge)},
                       "regress": {"repos": {"openswarm": str(self.repo)}},
                       "run": {"timeout_seconds": 600, "fallback": False},
                       "budget": {"timezone": "UTC"}, "concurrency": {"total": 4},
                       "workspace": {"sibling_worktrees": True}}
        self.write_card("pass", "true", False)
        self.write_card("fail", "false", False)

    def tearDown(self):
        for key, value in self.constants.items():
            setattr(xs, key, value)
        self.tmp.cleanup()

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, check=True, capture_output=True,
                              text=True).stdout

    def write_card(self, card_id, verify, fails_on_base):
        directory = self.regression / card_id
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "card.md").write_text(f"Fix regression {card_id}.\n", encoding="utf-8")
        (directory / "meta.yaml").write_text(
            f"id: {card_id}\ncard_type: fix\nrepo: openswarm\nbase_commit: {self.base}\n"
            f"verify: '{verify}'\nexpected_outcome: pass\ndifficulty: 2\nest_minutes: 1\n"
            f"verify_fails_on_base: {'true' if fails_on_base else 'false'}\n", encoding="utf-8")

    @staticmethod
    def route(profile, _state, _policy, **_kwargs):
        backend = profile.get("backend") or "codex"
        return {"ok": True, "backend": backend, "model": profile.get("model") or "test-model",
                "effort": "low", "fallbacks": [], "profile": profile}

    @staticmethod
    def execute(*args, **_kwargs):
        backend, decision, _state, _prompt, worktree, _run_dir = args[:6]
        (Path(worktree) / f"changed-{backend}.txt").write_text("fixed\n", encoding="utf-8")
        usage = {"input": 120, "output": 30}
        attempt = {"backend": backend, "model": decision.get("model"), "effort": "low", "seconds": 0.05,
                   "error_class": "none", "meta": {"usage": usage}, "events": "events.jsonl"}
        final = {"status": "done", "summary": "fixed", "files_changed": [f"changed-{backend}.txt"],
                 "tests": "stubbed", "followups": []}
        return [attempt], final, None

    @staticmethod
    def verify(command, _cwd, _timeout):
        return {"cmd": command, "rc": 0 if command == "true" else 1, "tail": "", "failing": []}

    def test_run_records_per_card_verification_lessons_tokens_and_label(self):
        args = type("Args", (), {"cards": "pass,fail", "label": "smoke", "parallel": 2,
                                  "backend": "codex:test-model", "lane": None, "routing": None})()
        output = io.StringIO()
        state = {"codex": {"limits": {"codex": {"windows": []}}}}
        with mock.patch.object(xs, "load_policy", return_value=self.policy), \
                mock.patch.object(xs, "get_state", return_value=state), \
                mock.patch.object(xs, "route", side_effect=self.route), \
                mock.patch.object(xs, "run_executor_with_rate_limit", side_effect=self.execute), \
                mock.patch.object(xs, "run_verify", side_effect=self.verify), \
                mock.patch.object(xs, "probe", return_value=state), contextlib.redirect_stdout(output):
            xs.cmd_regress_run(args, self.policy)

        report = json.loads(output.getvalue())
        self.assertEqual(report["totals"]["pass_count"], 1, report)
        rows = {card["id"]: card for card in report["cards"]}
        self.assertTrue(rows["pass"]["pass"])
        self.assertFalse(rows["fail"]["pass"])
        self.assertEqual(rows["pass"]["tokens"], 150)
        results_path = Path(report["results"])
        self.assertTrue(results_path.is_file())
        self.assertEqual(json.loads(results_path.read_text(encoding="utf-8"))["label"], "smoke")
        ledger = [json.loads(line) for line in Path(xs.LEDGER).read_text(encoding="utf-8").splitlines()]
        self.assertEqual({entry["regress_label"] for entry in ledger}, {"smoke"})
        self.assertIn("L-9001", ledger[0]["lessons_injected"])
        self.assertEqual(len(self.git("worktree", "list", "--porcelain").split("worktree ")), 2)

    def test_weighted_selection_is_temporary_and_uses_advice(self):
        args = type("Args", (), {"cards": "pass", "label": "weighted", "parallel": 1,
                                  "backend": None, "lane": None, "routing": "weighted"})()
        policy_before = copy.deepcopy(self.policy)
        seen = []

        def execute(*args, **kwargs):
            seen.append(args[0])
            return self.execute(*args, **kwargs)

        output = io.StringIO()
        with mock.patch.object(xs, "load_policy", return_value=self.policy), \
                mock.patch.object(xs, "get_state", return_value={}), \
                mock.patch.object(xs, "route", side_effect=self.route), \
                mock.patch.object(xs, "routing_advice", return_value={"backend": "agy", "model": "weighted-model"}), \
                mock.patch.object(xs, "run_executor_with_rate_limit", side_effect=execute), \
                mock.patch.object(xs, "run_verify", side_effect=self.verify), \
                contextlib.redirect_stdout(output):
            xs.cmd_regress_run(args, self.policy)

        self.assertEqual(seen, ["agy"])
        self.assertEqual(self.policy, policy_before)
        self.assertEqual(json.loads(output.getvalue())["cards"][0]["backend"], "agy")

    def test_compare_reports_per_card_totals_and_b_not_worse(self):
        before = self.root / "A.json"
        after = self.root / "B.json"
        before.write_text(json.dumps({"cards": [
            {"id": "pass", "pass": True, "seconds": 8, "tokens": 100},
            {"id": "fail", "pass": False, "seconds": 4, "tokens": 50}],
            "totals": {"cards": 2, "pass_count": 1, "median_seconds": 6, "tokens": 150}}), encoding="utf-8")
        after.write_text(json.dumps({"cards": [
            {"id": "pass", "pass": True, "seconds": 3, "tokens": 80},
            {"id": "fail", "pass": True, "seconds": 5, "tokens": 70}],
            "totals": {"cards": 2, "pass_count": 2, "median_seconds": 4, "tokens": 150}}), encoding="utf-8")
        args = type("Args", (), {"before": str(before), "after": str(after)})()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            xs.cmd_regress_compare(args)
        report = json.loads(output.getvalue())
        self.assertEqual(report["verdict"], "B not worse")
        self.assertEqual(report["B"]["pass_count"], 2)
        self.assertEqual(len(report["cards"]), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
