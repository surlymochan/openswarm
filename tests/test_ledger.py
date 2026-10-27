"""Ledger v2 shape and card type validation use local fixtures only."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

import _load

xs = _load.load()


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="openswarm-ledger-")
        self.root = Path(self.tmp.name)
        self.constants = {key: getattr(xs, key) for key in ("DATA", "LEDGER", "RUNS")}
        xs.DATA = str(self.root / "data")
        xs.LEDGER = str(self.root / "data" / "ledger.jsonl")
        xs.RUNS = str(self.root / "data" / "runs")

    def tearDown(self):
        for key, value in self.constants.items():
            setattr(xs, key, value)
        self.tmp.cleanup()

    def test_old_records_remain_readable_and_json_returns_full_entries(self):
        old = {"id": "old-run", "ts": 1, "status": "done", "backend": "pi", "title": "old"}
        Path(xs.LEDGER).parent.mkdir(parents=True)
        Path(xs.LEDGER).write_text(json.dumps(old) + "\n")
        self.assertEqual(list(xs.ledger_entries()), [old])

        original_argv = sys.argv
        try:
            sys.argv = ["openswarm", "ledger", "--json"]
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                xs.main()
        finally:
            sys.argv = original_argv
        self.assertEqual(json.loads(output.getvalue()), [old])

    def test_known_card_types_reject_unknown_before_creating_run(self):
        knowledge = self.root / "knowledge"
        knowledge.mkdir()
        (knowledge / "card-types.yaml").write_text("types:\n  - feature\n  - bugfix\n")
        policy = {"knowledge": {"repo": str(knowledge)}}
        self.assertEqual(xs.configured_card_types(policy), {"feature", "bugfix"})
        with self.assertRaisesRegex(ValueError, "unknown card type: typo"):
            xs.cmd_run(type("Args", (), {"card_type": "typo"})(), policy)
        self.assertFalse(Path(xs.RUNS).exists())
        self.assertFalse(Path(xs.LEDGER).exists())

    def test_missing_knowledge_repo_accepts_and_records_any_card_type(self):
        policy = {"knowledge": {"repo": str(self.root / "missing")}}
        self.assertIsNone(xs.configured_card_types(policy))
        xs.validate_card_type(policy, "unlisted-type")

    def test_ledger_json_keeps_full_v2_record(self):
        xs.append_ledger({"id": "fake-1", "ts": 123, "title": "fake sk-123456789012 run", "status": "done",
                          "backend": "pi", "files_changed": ["src/a.py", "README.md", "src/b.py"],
                          "usage": {"input": 12, "output": 8}, "seconds": 3.5})
        record = json.loads(Path(xs.LEDGER).read_text())
        self.assertEqual(record["run_id"], "fake-1")
        self.assertNotIn("sk-123", json.dumps(record))
        self.assertEqual(record["area"], ["README.md", "src"])
        self.assertEqual(record["tokens"], {"input": 12, "output": 8, "total": 20})
        self.assertEqual(record["lessons_injected"], [])
        self.assertIsNone(record["merged"])
        self.assertEqual(list(xs.ledger_entries()), [record])

        original_argv = sys.argv
        try:
            sys.argv = ["openswarm", "ledger", "--json"]
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                xs.main()
        finally:
            sys.argv = original_argv
        self.assertEqual(json.loads(output.getvalue()), [record])


if __name__ == "__main__":
    unittest.main(verbosity=2)
