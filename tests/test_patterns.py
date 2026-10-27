"""Pattern matching, proposal generation and retirement checks use local fixtures only."""
import contextlib
import datetime as dt
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _load

xs = _load.load()


class PatternsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="openswarm-patterns-")
        self.root = Path(self.tmp.name)
        self.knowledge = self.root / "knowledge"
        self.skills = self.knowledge / "skills"
        self.skills.mkdir(parents=True)
        self.proposals = self.knowledge / "proposals"
        self.runs = self.root / "runs"
        self.runs.mkdir()
        self.data = self.root / "data"
        self.ledger = self.data / "ledger.jsonl"
        self.policy = {"knowledge": {"repo": str(self.knowledge)}}
        self.constants = {key: getattr(xs, key) for key in ("DATA", "LEDGER", "RUNS", "HOME", "NOW")}
        xs.DATA, xs.LEDGER, xs.RUNS, xs.HOME = str(self.data), str(self.ledger), str(self.runs), str(self.root)
        fixed = dt.datetime(2026, 10, 27, 12, tzinfo=dt.timezone.utc).timestamp()
        xs.NOW = lambda: fixed

    def tearDown(self):
        for key, value in self.constants.items():
            setattr(xs, key, value)
        self.tmp.cleanup()

    def write_pattern(self, pattern_id, title, body="When to use: common change.\nPattern:\n- Validate input.\n- Test boundary cases.\nExample: [PR](https://example.invalid/pr/1)",
                      card_types="[]", paths="[]", backends="[]", status="active"):
        path = self.skills / f"{pattern_id}.md"
        path.write_text(
            f"---\nid: {pattern_id}\ntitle: {title}\napplies_to:\n"
            f"  card_types: {card_types}\n  paths: {paths}\n  backends: {backends}\n"
            f"source: \"PR #1\"\nstatus: {status}\n---\n{body}\n", encoding="utf-8")
        return path

    def run_command(self, *args):
        old_argv = sys.argv
        output = io.StringIO()
        try:
            sys.argv = ["openswarm", *args]
            with mock.patch.object(xs, "load_policy", return_value=self.policy), contextlib.redirect_stdout(output):
                xs.main()
        finally:
            sys.argv = old_argv
        return output.getvalue()

    def test_selection_matches_and_caps_three_patterns_and_1500_characters(self):
        for number in range(1, 5):
            self.write_pattern(f"P-{number:04d}", f"Pattern {number}", card_types="[fix]",
                               paths="[src/**]", backends="[codex]")
        self.write_pattern("P-0005", "Wrong card", card_types="[docs]")
        self.write_pattern("P-0006", "Retired", status="retired")
        block, ids = xs.select_patterns(self.policy, "fix", "codex", paths=["src/file.py"])
        self.assertEqual(ids, ["P-0001", "P-0002", "P-0003"])
        self.assertLessEqual(len(block), 1500)
        self.assertNotIn("Wrong card", block)
        self.assertNotIn("Retired", block)

        (self.skills / "P-0001.md").write_text(
            (self.skills / "P-0001.md").read_text(encoding="utf-8").replace("- Validate input.", "x" * 2000),
            encoding="utf-8")
        block, ids = xs.select_patterns(self.policy, "fix", "codex", paths=["src/file.py"])
        self.assertLessEqual(len(block), xs.PATTERNS_MAX_CHARS)
        self.assertEqual(ids[0], "P-0001")

    def test_preview_shows_matching_lessons_and_patterns(self):
        self.write_pattern("P-0010", "Preview pattern", card_types="[fix]", backends="[codex]")
        (self.knowledge / "lessons").mkdir()
        (self.knowledge / "lessons" / "L-0010.yaml").write_text(
            "id: L-0010\nrule: Preview lesson\nguard_level: L2\napplies_to:\n"
            "  card_types: [fix]\n  paths: []\n  backends: [codex]\n", encoding="utf-8")
        output = self.run_command("lessons", "preview", "--card-type", "fix", "--backend", "codex")
        self.assertIn("[L-0010] Preview lesson", output)
        self.assertIn("[P-0010] Preview pattern", output)

    def test_ledger_normalization_preserves_patterns_injected(self):
        xs.append_ledger({"id": "run-1", "patterns_injected": ["P-0001"]})
        entry = json.loads(self.ledger.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(entry["patterns_injected"], ["P-0001"])
        xs.append_ledger({"id": "run-2"})
        entry = json.loads(self.ledger.read_text(encoding="utf-8").splitlines()[1])
        self.assertEqual(entry["patterns_injected"], [])

    def test_propose_converts_compare_winner_proposal_without_editing_skills(self):
        source_dir = self.proposals / "2026-10-26"
        source_dir.mkdir(parents=True)
        source = source_dir / "pattern-cmp-123.md"
        source.write_text("# Pattern proposal from compare cmp-123\n\n## Proposed pattern\n\nReview the winner.\n",
                          encoding="utf-8")
        run_dir = self.runs / "cmp-123"
        run_dir.mkdir()
        (run_dir / "compare.json").write_text(json.dumps({
            "id": "cmp-123", "created_at": dt.datetime.fromtimestamp(xs.NOW() - 60, dt.timezone.utc).isoformat(),
            "title": "Update the archive parser", "card_type": "fix", "winner_label": "A",
            "diff_notes": ["Winner rejects traversal paths before extraction."],
            "runs": {"a": {"backend": "codex", "run_id": "winner-1",
                            "result": {"result": {"files_changed": ["src/archive.py"]}}}}}),
            encoding="utf-8")
        original_skills = list(self.skills.iterdir())
        output = json.loads(self.run_command("patterns", "propose", "--since", "30"))
        self.assertEqual(output["count"], 1)
        draft = Path(output["proposals"][0])
        text = draft.read_text(encoding="utf-8")
        self.assertIn("proposal_type: pattern_draft", text)
        self.assertIn('source: "compare cmp-123"', text)
        self.assertIn('card_types: ["fix"]', text)
        self.assertIn('backends: ["codex"]', text)
        self.assertIn("Winner rejects traversal paths", text)
        self.assertIn("Example: [compare proposal](../2026-10-26/pattern-cmp-123.md)", text)
        self.assertEqual(list(self.skills.iterdir()), original_skills)

    def test_propose_collects_repeated_peer_review_findings(self):
        for run_id in ("review-one", "review-two"):
            run_dir = self.runs / run_id
            run_dir.mkdir()
            (run_dir / "review.json").write_text(json.dumps({"findings": [{
                "severity": "P1", "path": "src/archive.py", "line": 10,
                "title": "Validate archive paths", "body": "Reject paths outside the extraction root."}]}),
                encoding="utf-8")
        self.ledger.parent.mkdir(parents=True)
        self.ledger.write_text("".join(json.dumps({"id": run_id, "ts": xs.NOW() - 60,
                                                     "backend": "codex", "card_type": "fix"}) + "\n"
                                       for run_id in ("review-one", "review-two")), encoding="utf-8")
        output = json.loads(self.run_command("patterns", "propose"))
        self.assertEqual(output["count"], 1)
        text = Path(output["proposals"][0]).read_text(encoding="utf-8")
        self.assertIn("review finding", text)
        self.assertIn("Validate archive paths", text)

    def test_retire_check_requires_eight_runs_in_both_cohorts(self):
        self.write_pattern("P-0020", "Old pattern")
        entries = []
        for index in range(8):
            entries.append({"id": f"injected-{index}", "ts": xs.NOW() - 60, "card_type": "fix",
                            "backend": "codex", "patterns_injected": ["P-0020"], "exec_pass": False,
                            "accepted": False})
        for index in range(8):
            entries.append({"id": f"control-{index}", "ts": xs.NOW() - 60, "card_type": "fix",
                            "backend": "codex", "patterns_injected": [], "exec_pass": True,
                            "accepted": True})
        self.ledger.parent.mkdir(parents=True)
        self.ledger.write_text("".join(json.dumps(entry) + "\n" for entry in entries), encoding="utf-8")
        output = json.loads(self.run_command("patterns", "retire-check"))
        self.assertEqual(len(output["retirement_proposals"]), 1)
        self.assertIn("retire-P-0020.md", output["retirement_proposals"][0])

        self.ledger.write_text("".join(json.dumps(entry) + "\n" for entry in entries if not entry["id"] == "injected-7"),
                               encoding="utf-8")
        output = json.loads(self.run_command("patterns", "retire-check"))
        self.assertEqual(output["retirement_proposals"], [])
        self.assertEqual(output["insufficient_samples"], 1)


if __name__ == "__main__":
    unittest.main()
