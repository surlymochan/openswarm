"""Routing weight aggregation and record-only advice use isolated ledger fixtures."""
import contextlib
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import _load

xs = _load.load()
with open(_load.FIXTURE_STATE) as stream:
    BASE_STATE = json.load(stream)


class RoutingWeightsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="openswarm-routing-")
        self.root = Path(self.tmp.name)
        self.data = self.root / "data"
        self.ledger = self.data / "ledger.jsonl"
        self.runs = self.data / "runs"
        self.constants = {key: getattr(xs, key) for key in ("DATA", "LEDGER", "RUNS", "HOME")}
        xs.DATA, xs.LEDGER, xs.RUNS, xs.HOME = str(self.data), str(self.ledger), str(self.runs), str(self.root)

    def tearDown(self):
        for key, value in self.constants.items():
            setattr(xs, key, value)
        self.tmp.cleanup()

    def write_ledger(self, entries):
        self.data.mkdir(parents=True, exist_ok=True)
        self.ledger.write_text("".join(json.dumps(entry) + "\n" for entry in entries))

    def test_fixture_ledger_computes_all_routing_metrics(self):
        rows = [
            {"backend": "codex", "model": "m1", "card_type": "fix", "status": "done", "exec_pass": True,
             "attempts": [{}], "seconds": 1, "tokens": {"total": 100}, "review_findings": {"p0": 0, "p1": 1}},
            {"backend": "codex", "model": "m1", "card_type": "fix", "status": "done", "exec_pass": False,
             "attempts": [{}, {}], "seconds": 2, "tokens": {"total": 200}, "review_findings": {"p0": 0, "p1": 0}},
            {"backend": "codex", "model": "m1", "card_type": "fix", "status": "done", "exec_pass": True,
             "attempts": [{}], "seconds": 3, "tokens": {"total": 300}, "rework_of": "previous"},
            {"backend": "codex", "model": "m1", "card_type": "fix", "status": "partial", "exec_pass": False,
             "attempts": [], "seconds": 4, "tokens": {"total": 400}, "review_findings": {"p0": 1, "p1": 0}},
            {"backend": "ocgo", "model": "m2", "card_type": "fix", "status": "done", "exec_pass": True,
             "attempts": [{}]},
            {"backend": "ocgo", "model": "m2", "card_type": "fix", "status": "done", "exec_pass": True,
             "attempts": [{}]},
            {"backend": "pi", "model": "m3", "card_type": "fix", "status": "done", "exec_pass": None},
        ]

        result = xs.compute_routing_weights(rows, min_samples=3)
        codex = result["entries"][0]
        self.assertEqual(codex["backend"], "codex")
        self.assertEqual(codex["runs"], 4)
        self.assertEqual(codex["pass_rate"], 0.5)
        self.assertEqual(codex["accepted_runs"], 0)
        self.assertIsNone(codex["accepted_rate"])
        self.assertEqual(codex["exec_pass_runs"], 4)
        self.assertEqual(codex["exec_pass_rate"], 0.5)
        self.assertEqual(codex["exec_pass_ratio"], "2/4")
        self.assertEqual(codex["exec_pass_null_runs"], 0)
        self.assertEqual(codex["first_try_rate"], 0.5)
        self.assertEqual(codex["rework_rate"], 0.25)
        self.assertEqual(codex["review_p1_rate"], 2 / 3)
        self.assertEqual(codex["median_tokens"], 250)
        self.assertEqual(codex["median_seconds"], 2.5)
        self.assertTrue(codex["enough"])
        self.assertFalse(result["entries"][1]["enough"])
        pi = next(row for row in result["entries"] if row["backend"] == "pi")
        self.assertEqual(pi["exec_pass_ratio"], "0/0")
        self.assertEqual(pi["exec_pass_null_runs"], 1)
        self.assertFalse(xs.routing_passed({"status": "done", "verify": None, "verify_rc": None}))
        self.assertFalse(xs.routing_passed({"status": "done", "exec_pass": False}))
        self.assertTrue(xs.routing_passed({"status": "partial", "exec_pass": True}))
        self.assertFalse(xs.routing_passed({"accepted": False, "exec_pass": True}))

    def test_acceptance_overrides_exec_pass_and_reports_both_rates(self):
        rows = [
            {"backend": "pi", "status": "failed", "accepted": True, "exec_pass": False},
            {"backend": "pi", "status": "done", "accepted": False, "exec_pass": True},
            {"backend": "pi", "status": "partial", "exec_pass": True},
        ]
        row = xs.compute_routing_weights(rows, min_samples=3)["entries"][0]
        self.assertEqual(row["pass_rate"], 2 / 3)
        self.assertEqual(row["accepted_rate"], 0.5)
        self.assertEqual(row["accepted_runs"], 2)
        self.assertEqual(row["exec_pass_rate"], 2 / 3)
        self.assertEqual(row["exec_pass_runs"], 3)
        self.assertEqual(row["scored_runs"], 3)

    def test_run_records_advice_without_changing_live_route(self):
        policy = copy.deepcopy(xs.load_policy())
        policy["routing"]["min_samples"] = 2
        profile = {"type": "impl", "difficulty": 3}
        state = copy.deepcopy(BASE_STATE)
        expected = xs.route(profile, state, policy)
        ocgo = xs.route({**profile, "backend": "ocgo"}, state, policy)
        self.assertEqual(expected["backend"], "codex")
        self.assertTrue(ocgo["ok"])
        samples = []
        for i in range(2):
            samples.append({"id": f"c{i}", "backend": "codex", "model": expected["model"], "card_type": "fix",
                            "status": "partial", "exec_pass": False, "attempts": [{}, {}], "seconds": 10,
                            "tokens": {"total": 200}})
            samples.append({"id": f"o{i}", "backend": "ocgo", "model": ocgo["model"], "card_type": "fix",
                            "status": "done", "exec_pass": True, "attempts": [{}], "seconds": 5,
                            "tokens": {"total": 100}})
        self.write_ledger(samples)

        args = type("Args", (), {
            "card_type": "fix", "rework_of": None, "run_id": None, "prompt": "implement fix",
            "task_file": None, "title": "fix", "type": "impl", "difficulty": 3, "context_tokens": 0,
            "backend": None, "model": None, "effort": None, "budget_minutes": 5, "dry_run": False,
            "detach": False, "cwd": str(self.root), "no_worktree": True, "base": None,
            "keep_worktree": True, "verify": None, "paths": [], "no_fallback": False,
            "verify_baseline": False,
        })()
        executor_attempt = {"backend": expected["backend"], "seconds": 1, "error_class": None,
                            "meta": {"usage": {"input": 3, "output": 4}}}
        output = io.StringIO()
        with mock.patch.object(xs, "get_state", return_value=state), \
                mock.patch.object(xs, "admit_budget"), \
                mock.patch.object(xs, "prepare_workspace", return_value={"mode": "direct", "path": str(self.root),
                                                                           "root": str(self.root), "repo": None,
                                                                           "base": None}), \
                mock.patch.object(xs, "run_workspace_prepare", return_value=None), \
                mock.patch.object(xs, "add_lessons_to_prompt", return_value=("prompt", [])), \
                mock.patch.object(xs, "run_executor_with_rate_limit",
                                  return_value=([executor_attempt], {"status": "partial", "summary": "ok",
                                                                    "files_changed": []}, None)), \
                mock.patch.object(xs, "probe", return_value=state), \
                mock.patch.object(xs, "finalize_workspace", return_value={"branch": None, "commit": "abc123",
                                                                           "diffstat": "", "patch": None,
                                                                           "worktree": None, "merge_hint": None}), \
                mock.patch.object(xs, "cleanup_worktree"), \
                mock.patch.object(xs, "changed_paths", return_value=[]), \
                contextlib.redirect_stdout(output):
            with self.assertRaises(SystemExit) as result:
                xs.cmd_run(args, policy)
        self.assertEqual(result.exception.code, 1)
        run = json.loads(output.getvalue())
        self.assertEqual(run["backend"], expected["backend"])
        self.assertEqual(run["routing_advice"]["backend"], "ocgo")
        recorded = json.loads(self.ledger.read_text().splitlines()[-1])
        self.assertEqual(recorded["backend"], "codex")
        self.assertEqual(recorded["routing_advice"]["backend"], "ocgo")
        self.assertTrue(recorded["exec_pass"])  # partial with a commit is an execution pass
        self.assertIsNone(recorded["accepted"])

    def test_min_samples_blocks_advice_and_cli_writes_stable_weights(self):
        policy = copy.deepcopy(xs.load_policy())
        policy["routing"]["min_samples"] = 3
        state = copy.deepcopy(BASE_STATE)
        profile = {"type": "impl", "difficulty": 3}
        actual = xs.route(profile, state, policy)
        self.write_ledger([{"backend": "codex", "model": actual["model"], "card_type": "fix",
                            "status": "done", "attempts": [{}]} for _ in range(2)])
        advice = xs.routing_advice(profile, "fix", state, policy, actual)
        self.assertIsNone(advice["backend"])

        output_path = self.root / "custom" / "weights.json"
        original_argv = sys.argv
        try:
            sys.argv = ["openswarm", "routing", "weights", "--out", str(output_path)]
            result = io.StringIO()
            with mock.patch.object(xs, "load_policy", return_value=policy), contextlib.redirect_stdout(result):
                xs.main()
        finally:
            sys.argv = original_argv
        saved = json.loads(output_path.read_text())
        self.assertFalse(saved["entries"][0]["enough"])
        self.assertEqual(json.loads(result.getvalue())["entries"], saved["entries"])
        self.assertEqual(output_path.read_text(), json.dumps(saved, ensure_ascii=False, indent=2, sort_keys=True) + "\n")

        fleet_ledger = self.data / "ledger-fleet.jsonl"
        fleet_ledger.write_text(json.dumps({"backend": "pi", "model": "p1", "card_type": "fix",
                                            "status": "done", "attempts": [{}]}) + "\n")
        knowledge = self.root / "knowledge"
        policy.setdefault("knowledge", {})["repo"] = str(knowledge)
        try:
            sys.argv = ["openswarm", "routing", "weights", "--fleet"]
            result = io.StringIO()
            with mock.patch.object(xs, "load_policy", return_value=policy), contextlib.redirect_stdout(result):
                xs.main()
        finally:
            sys.argv = original_argv
        default_path = knowledge / "routing" / "weights.json"
        self.assertTrue(default_path.is_file())
        self.assertEqual(json.loads(default_path.read_text())["entries"][0]["backend"], "pi")
        self.assertEqual(json.loads(result.getvalue())["path"], str(default_path))

    def test_compare_shows_actual_outcome_and_unrun_counterfactual(self):
        now = xs.NOW()
        self.write_ledger([{"id": "one", "ts": now, "card_type": "fix", "backend": "codex", "model": "c1",
                            "status": "done", "exec_pass": True, "verify": None, "verify_rc": None,
                            "routing_advice": {"backend": "ocgo", "model": "o1", "reason": "fixture"}}])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            xs.cmd_routing_compare(type("Args", (), {"days": 1})(), {})
        self.assertIn("ocgo / o1", output.getvalue())
        self.assertIn("not run", output.getvalue())
        line = output.getvalue().rstrip().splitlines()[-1]
        self.assertIn("codex / c1", line)
        self.assertTrue(line.endswith("pass"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
