"""Lesson selection, bounded rendering and preview CLI coverage."""
import contextlib
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


class LessonsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="openswarm-lessons-")
        self.root = Path(self.tmp.name)
        self.lessons = self.root / "lessons"
        self.lessons.mkdir()
        self.policy = {"knowledge": {"repo": str(self.root)}}

    def tearDown(self):
        self.tmp.cleanup()

    def write_lesson(self, name, lesson_id, rule, guard="L2", card_types="[]", paths="[]", backends="[]"):
        (self.lessons / name).write_text(
            f"id: {lesson_id}\ndate: 2026-10-27\nrule: {rule}\nguard_level: {guard}\napplies_to:\n"
            f"  card_types: {card_types}\n  paths: {paths}\n  backends: {backends}\n",
            encoding="utf-8")

    def test_selection_filters_card_type_backend_and_paths_then_orders_specificity(self):
        self.write_lesson("global.yaml", "L-0001", "general rule")
        self.write_lesson("specific.yaml", "L-0002", "specific rule", card_types="[fix]",
                          paths="[src/*.py]", backends="[codex]")
        self.write_lesson("wrong-type.yaml", "L-0003", "wrong type", card_types="[docs]")
        self.write_lesson("wrong-backend.yaml", "L-0004", "wrong backend", backends="[pi]")
        self.write_lesson("wrong-path.yaml", "L-0005", "wrong path", paths="[tests/*.py]")
        self.write_lesson("l3.yaml", "L-0006", "reminder rule", guard="L3", card_types="[fix]")
        block, ids = xs.select_lessons(self.policy, "fix", "codex", "Update src/widget.py")
        self.assertEqual(ids, ["L-0002", "L-0006", "L-0001"])
        self.assertLess(block.index("[L-0002]"), block.index("[L-0006]"))
        self.assertIn("L3 entries are reminders", block)

    def test_paths_hint_matches_and_missing_repo_is_empty(self):
        self.write_lesson("path.yaml", "L-0010", "touch the hinted path", paths="[src/*.py]")
        self.assertEqual(xs.select_lessons(self.policy, "fix", "codex", paths=["src/a.py"])[1], ["L-0010"])
        self.assertEqual(xs.select_lessons({"knowledge": {"repo": str(self.root / "absent")}}, "fix", "codex"),
                         ("", []))

    def test_bad_yaml_is_warned_and_skipped(self):
        self.write_lesson("good.yaml", "L-0011", "valid")
        (self.lessons / "bad.yaml").write_text(
            "id: L-0012\nrule: nope\nguard_level: L2\napplies_to:\n  unknown: [value]\n", encoding="utf-8")
        warning = io.StringIO()
        with contextlib.redirect_stderr(warning):
            block, ids = xs.select_lessons(self.policy, "fix", "codex")
        self.assertEqual(ids, ["L-0011"])
        self.assertIn("skipping lesson file", warning.getvalue())
        self.assertIn("unsupported applies_to key 'unknown'", warning.getvalue())

    def test_yaml_scalar_subset_accepts_null_booleans_integers_and_strings(self):
        lesson = xs.parse_lesson_yaml(
            "id: L-0014\nrule: a plain string\nguard_level: L2\nguard_ref: null\n"
            "enabled: true\nretired: false\nretry_count: 123\ntitle: 'quoted string'\n"
            "applies_to:\n  card_types: []\n  paths: []\n  backends: []\n")
        self.assertIsNone(lesson["guard_ref"])
        self.assertIs(lesson["enabled"], True)
        self.assertIs(lesson["retired"], False)
        self.assertEqual(lesson["retry_count"], 123)
        self.assertEqual(lesson["rule"], "a plain string")
        self.assertEqual(lesson["title"], "quoted string")

    def test_yaml_scalar_subset_still_rejects_anchors(self):
        with self.assertRaisesRegex(ValueError, "unsupported YAML scalar syntax"):
            xs.parse_lesson_yaml(
                "id: L-0015\nrule: valid\nguard_level: L2\nextra: &anchor value\n")

    def test_selection_caps_at_twelve_lessons_and_three_thousand_characters(self):
        for index in range(15):
            self.write_lesson(f"{index:02}.yaml", f"L-{index:04}", "short")
        block, ids = xs.select_lessons(self.policy, "fix", "codex")
        self.assertEqual(len(ids), 12)
        self.assertLessEqual(len(block), xs.LESSONS_MAX_CHARS)
        self.assertIn("[L-0000]", block)
        for index in range(15):
            (self.lessons / f"{index:02}.yaml").unlink()
        for index in range(15):
            self.write_lesson(f"{index:02}.yaml", f"L-{index:04}", "x" * 280)
        block, ids = xs.select_lessons(self.policy, "fix", "codex")
        self.assertEqual(len(ids), 10)
        self.assertLessEqual(len(block), xs.LESSONS_MAX_CHARS)

    def test_preview_command_prints_selected_block(self):
        self.write_lesson("preview.yaml", "L-0013", "protect src path", paths="[src/*.py]", backends="[codex]")
        old_argv = sys.argv
        try:
            sys.argv = ["openswarm", "lessons", "preview", "--card-type", "fix", "--backend", "codex",
                        "--paths", "src/widget.py"]
            output = io.StringIO()
            with mock.patch.object(xs, "load_policy", return_value=self.policy), \
                    contextlib.redirect_stdout(output):
                xs.main()
        finally:
            sys.argv = old_argv
        self.assertIn("[L-0013] protect src path", output.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
