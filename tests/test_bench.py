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

    def test_mixed_late_week_reviews_degrade_with_model_level_separation(self):
        # mixed_late_week leaves one usable family for the reference
        # implementer: family-level independence stays honestly at 0, but
        # single-family reviews must degrade to a different model (marked
        # `degraded: single-family`) instead of silently re-using the
        # implementer's own model. The four reviews whose implementer proxy
        # is Codex (d4/d5) keep the reference model; they are cross-family
        # against their own implementer, an artifact of the one-reference
        # scoring rule, so model-level lands at 8/12, not 12/12.
        import score  # bench/score.py
        old = os.environ.copy()
        try:
            home = tempfile.mkdtemp(prefix="openswarm-bench-test-")
            xs = harness.load_openswarm(home)
            policy = xs.jload(os.path.join(harness.ROOT, "policy.json"), {})
            policy["routing"]["default_order"] = ["codex", "ocgo", "deepseek"]
            policy["codex"]["require_default_model"] = False
            catalogue = harness.load_json(os.path.join(BENCH, "catalogue.json"))
            scenarios = harness.load_json(os.path.join(BENCH, "scenarios.json"))
            trace = harness.load_jsonl(os.path.join(BENCH, "trace.jsonl"))
            prices = harness.load_json(os.path.join(BENCH, "prices.json"))
            rows, _ = harness.route_all(xs, policy, catalogue, scenarios, trace)
            block = score.accumulate("openswarm", rows, trace, scenarios, catalogue, prices)
            mlw = block["scenarios"]["mixed_late_week"]
            self.assertEqual(mlw["review_independent_rate"], 0.0)
            self.assertEqual(mlw["review_model_independent_rate"], round(8 / 12, 4))
            self.assertEqual(mlw["review_degraded"], mlw["review_n"])
            self.assertEqual(mlw["review_degraded_model_independent"], 8)
            self.assertEqual(mlw["review_degraded_flagged"], 6)
            self.assertEqual(block["overall"]["review_independent_rate"], 0.8571)
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
            self.assertIn(("t06", "codex", "gpt-6-luna"), fresh)
            self.assertIn(("t24", "codex", "gpt-6-luna"), fresh)
            self.assertTrue(all(r["failed_attempts"] == 0 for r in first))
        finally:
            os.environ.clear()
            os.environ.update(old)


if __name__ == "__main__":
    unittest.main()
