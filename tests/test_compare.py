"""Best-of-two runs use stub executors and reviewers with local fixtures only."""
import contextlib
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


class CompareTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="openswarm-compare-")
        self.root = Path(self.tmp.name)
        self.data = self.root / "data"
        self.runs = self.data / "runs"
        self.knowledge = self.root / "knowledge"
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.constants = {key: getattr(xs, key) for key in ("DATA", "LEDGER", "RUNS")}
        xs.DATA, xs.LEDGER, xs.RUNS = str(self.data), str(self.data / "ledger.jsonl"), str(self.runs)
        self.git("init", "-q")
        self.git("config", "user.name", "Compare Test")
        self.git("config", "user.email", "compare@example.invalid")
        (self.repo / "module.py").write_text("value = 1\n", encoding="utf-8")
        self.git("add", "module.py")
        self.git("commit", "-q", "-m", "base")
        self.base = self.git("rev-parse", "HEAD").strip()
        self.task = self.root / "task.md"
        self.task.write_text("Implement the same task.\n", encoding="utf-8")
        self.policy = {"run": {"timeout_seconds": 120}, "budget": {"timezone": "UTC"},
                       "knowledge": {"repo": str(self.knowledge)}}

    def tearDown(self):
        for key, value in self.constants.items():
            setattr(xs, key, value)
        self.tmp.cleanup()

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, check=True, capture_output=True,
                              text=True).stdout

    def candidate(self, label, verify, p0p1, diff_size, seconds):
        findings = [{"severity": "P1", "path": "x.py", "line": 1, "title": f"finding {index}", "body": "issue"}
                    for index in range(p0p1)]
        candidate = {"label": label, "verify_rc": 0 if verify else 1, "seconds": seconds,
                     "diff": {"available": True, "changed_lines": diff_size},
                     "review": {"status": "reviewed", "findings": findings}}
        candidate["score"] = xs.compare_candidate_score(candidate)
        return candidate

    def test_winner_selection_follows_verify_review_diff_and_time_order(self):
        # Verification wins over findings, diff size, and elapsed time.
        self.assertEqual(xs.choose_compare_winner(self.candidate("A", True, 4, 100, 50),
                                                  self.candidate("B", False, 0, 1, 1))["label"], "A")
        # Review findings win over diff size and elapsed time.
        self.assertEqual(xs.choose_compare_winner(self.candidate("A", True, 0, 100, 50),
                                                  self.candidate("B", True, 1, 1, 1))["label"], "A")
        # Smaller diff wins before elapsed time.
        self.assertEqual(xs.choose_compare_winner(self.candidate("A", True, 0, 5, 50),
                                                  self.candidate("B", True, 0, 10, 1))["label"], "A")
        self.assertEqual(xs.choose_compare_winner(self.candidate("A", True, 0, 5, 1),
                                                  self.candidate("B", True, 0, 5, 2))["label"], "A")

    def test_exact_tie_has_no_winner(self):
        self.assertIsNone(xs.choose_compare_winner(self.candidate("A", True, 1, 5, 2),
                                                   self.candidate("B", True, 1, 5, 2)))

    def test_tied_run_ledger_entries_are_marked_tie(self):
        candidates = [{"run_id": "run-a", "backend": "codex", "result": {"status": "done"}},
                      {"run_id": "run-b", "backend": "agy", "result": {"status": "done"}}]
        xs.update_compare_ledger(candidates, "compare-1", None)
        entries = [json.loads(line) for line in Path(xs.LEDGER).read_text(encoding="utf-8").splitlines()]
        self.assertEqual({entry["compare_result"] for entry in entries}, {"tie"})
        self.assertEqual({entry["compare_id"] for entry in entries}, {"compare-1"})

    def test_default_selection_uses_available_different_families(self):
        state = {"ocgo": {"models": [{"id": "openai-model", "family": "OpenAI"}]}}

        def route(profile, _state, _policy):
            backend = profile["backend"]
            if backend not in ("codex", "ocgo", "deepseek"):
                return {"ok": False}
            return {"ok": True, "backend": backend,
                    "model": {"codex": "codex-model", "ocgo": "openai-model",
                              "deepseek": "deepseek-chat"}[backend]}

        args = type("Args", (), {"a": None, "b": None})()
        with mock.patch.object(xs, "get_state", return_value=state), mock.patch.object(xs, "route", side_effect=route):
            self.assertEqual(xs.select_compare_backends(args, {"routing": {"default_order": [
                "codex", "ocgo", "deepseek"]}}),
                {"a": {"backend": "codex", "model": "codex-model"},
                 "b": {"backend": "deepseek", "model": "deepseek-chat"}})
            args.b = "codex"
            self.assertEqual(xs.select_compare_backends(args, {"routing": {"default_order": [
                "codex", "ocgo", "deepseek"]}}),
                {"a": {"backend": "deepseek", "model": "deepseek-chat"},
                 "b": {"backend": "codex", "model": None}})

    def test_review_failure_is_recorded_and_ledgers_and_proposal_are_written(self):
        args = type("Args", (), {"task_file": str(self.task), "title": "Same task", "a": "codex",
                                  "b": "agy", "verify": "true", "card_type": None,
                                  "budget_minutes": 2})()
        launched = []

        class StubProcess:
            returncode = 0

            def communicate(self):
                self.assert_both_started()
                return ("{}", None)

            def assert_both_started(self):
                if len(launched) != 2:
                    raise AssertionError("both executor processes must start before either is joined")

        def stub_popen(command, **_options):
            if "--no-auto-review" not in command:
                raise AssertionError("compare child runs must not schedule duplicate automatic reviews")
            run_id = command[command.index("--run-id") + 1]
            backend = command[command.index("--backend") + 1]
            model = command[command.index("--model") + 1] if "--model" in command else None
            result = {"id": run_id, "title": "Same task", "status": "done", "backend": backend,
                      "model": model, "seconds": 3 if backend == "codex" else 4, "verify": {"rc": 0},
                      "workspace": {"repo": str(self.repo), "base": self.base}, "branch": f"openswarm/{run_id}",
                      "result": {"summary": f"{backend} completed"}}
            run_dir = self.runs / run_id
            (run_dir / "result.json").write_text(json.dumps(result), encoding="utf-8")
            xs.append_ledger({"id": run_id, "run_id": run_id, "backend": backend, "status": "done"})
            launched.append(run_id)
            return StubProcess()

        def reviewer(run_id, _by, _policy):
            if (json.loads((self.runs / run_id / "result.json").read_text(encoding="utf-8"))["backend"] == "codex"):
                raise RuntimeError("stub reviewer unavailable")
            return {"findings": [{"severity": "P2", "path": "module.py", "line": 1,
                                  "title": "Minor concern", "body": "Keep an eye on this."}],
                    "verdict": "approve"}

        def diff_stats(result):
            is_codex = result["backend"] == "codex"
            return {"available": True, "files": 1, "insertions": 2 if is_codex else 4,
                    "deletions": 1 if is_codex else 4, "changed_lines": 3 if is_codex else 8,
                    "paths": ["module.py" if is_codex else "other.py"]}

        stdout = io.StringIO()
        with mock.patch.object(xs, "select_compare_backends", return_value={
                    "a": {"backend": "codex"}, "b": {"backend": "agy"}}), \
                mock.patch.object(xs, "check_budget"), \
                mock.patch.object(xs.subprocess, "Popen", side_effect=stub_popen), \
                mock.patch.object(xs, "perform_peer_review", side_effect=reviewer) as review, \
                mock.patch.object(xs, "compare_diff_stats", side_effect=diff_stats), \
                contextlib.redirect_stdout(stdout):
            xs.cmd_compare(args, self.policy)

        cli = json.loads(stdout.getvalue())
        report = json.loads(Path(cli["compare_json"]).read_text(encoding="utf-8"))
        self.assertEqual(len(launched), 2)
        self.assertEqual(review.call_count, 2)
        self.assertEqual(report["winner_label"], "A")
        self.assertEqual(report["runs"]["a"]["review"]["status"], "failed")
        self.assertEqual(report["runs"]["b"]["review"]["status"], "reviewed")
        self.assertTrue(Path(cli["compare_md"]).is_file())
        self.assertIn("Review of A failed", Path(cli["compare_md"]).read_text(encoding="utf-8"))
        ledger = [json.loads(line) for line in Path(xs.LEDGER).read_text(encoding="utf-8").splitlines()]
        by_id = {entry["id"]: entry for entry in ledger}
        self.assertEqual(by_id[report["runs"]["a"]["run_id"]]["compare_id"], report["id"])
        self.assertEqual(by_id[report["runs"]["a"]["run_id"]]["compare_result"], "win")
        self.assertEqual(by_id[report["runs"]["b"]["run_id"]]["compare_result"], "lose")
        self.assertTrue(Path(cli["proposal"]).is_file())
        self.assertIn("Proposed pattern", Path(cli["proposal"]).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
