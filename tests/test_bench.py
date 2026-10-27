"""openswarm bench is deterministic and matches the checked-in rubric. No OmO required."""
import json
import os
import sys
import tempfile
import unittest

BENCH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bench")
sys.path.insert(0, BENCH)

import make_trace  # noqa: E402
import route_openswarm as harness  # noqa: E402


def _rows(path):
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


class BenchOpenswarm(unittest.TestCase):
    def test_trace_matches_rubric(self):
        expected = make_trace.build()
        on_disk = _rows(os.path.join(BENCH, "trace.jsonl"))
        self.assertGreaterEqual(len(on_disk), 60)
        self.assertLessEqual(len(on_disk), 100)
        self.assertEqual(on_disk, expected)

    def test_catalogue_tiers_match_live_classifiers(self):
        old = os.environ.copy()
        try:
            home = tempfile.mkdtemp(prefix="openswarm-bench-test-")
            xs = harness.load_openswarm(home)
            policy = xs.jload(os.path.join(harness.ROOT, "policy.json"), {})
            catalogue = harness.load_json(os.path.join(BENCH, "catalogue.json"))
            scenarios = harness.load_json(os.path.join(BENCH, "scenarios.json"))
            state = harness.build_state(xs, policy, catalogue, scenarios["scenarios"][0])
            expected = {m["id"]: m["tier"] for m in catalogue["models"]}
            for model in state["codex"]["models"] + state["ocgo"]["models"]:
                self.assertEqual(model["tier"], expected[model["id"]], model["id"])
        finally:
            os.environ.clear()
            os.environ.update(old)

    def test_decisions_are_deterministic(self):
        old = os.environ.copy()
        try:
            home = tempfile.mkdtemp(prefix="openswarm-bench-test-")
            xs = harness.load_openswarm(home)
            policy = xs.jload(os.path.join(harness.ROOT, "policy.json"), {})
            catalogue = harness.load_json(os.path.join(BENCH, "catalogue.json"))
            scenarios = harness.load_json(os.path.join(BENCH, "scenarios.json"))
            trace = harness.load_jsonl(os.path.join(BENCH, "trace.jsonl"))
            first, _ = harness.route_all(xs, policy, catalogue, scenarios, trace)
            second, _ = harness.route_all(xs, policy, catalogue, scenarios, trace)
            self.assertEqual(first, second)
            fresh = {(r["task_id"], r["backend"], r["model"]) for r in first if r["scenario"] == "fresh"}
            self.assertIn(("t06", "codex", "gpt-6.1-sol"), fresh)
            self.assertIn(("t24", "codex", "gpt-6-luna-fast"), fresh)
            self.assertTrue(all(r["failed_attempts"] == 0 for r in first))
        finally:
            os.environ.clear()
            os.environ.update(old)


if __name__ == "__main__":
    unittest.main()
