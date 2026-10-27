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
        self.constants = {key: getattr(xs, key) for key in ("DATA", "LEDGER", "HOME")}
        xs.DATA = str(self.root / "data")
        xs.LEDGER = str(self.root / "data" / "ledger.jsonl")
        xs.HOME = str(self.root / "home")
        self.gh = None

    def tearDown(self):
        self.tmp.cleanup()
        for key, value in self.constants.items():
            setattr(xs, key, value)

    def write_lesson(self, name, lesson_id, rule, guard="L2", card_types="[]", paths="[]", backends="[]",
                     root_cause_class=None, recurrences=0):
        extra = (f"root_cause_class: {root_cause_class}\nrecurrences: {recurrences}\n"
                 if root_cause_class else "")
        (self.lessons / name).write_text(
            f"id: {lesson_id}\ndate: 2026-10-27\nrule: {rule}\nguard_level: {guard}\napplies_to:\n"
            f"  card_types: {card_types}\n  paths: {paths}\n  backends: {backends}\n{extra}",
            encoding="utf-8")

    def make_gh(self, fixtures):
        fake = self.root / "gh"
        fixture_file = self.root / "gh-fixtures.json"
        call_log = self.root / "gh-calls.txt"
        fixture_file.write_text(json.dumps(fixtures), encoding="utf-8")
        fake.write_text(
            f"#!{sys.executable}\n"
            "import json, os, sys\n"
            "endpoint = sys.argv[2]\n"
            "with open(os.environ['GH_CALL_LOG'], 'a') as log: log.write(endpoint + '\\n')\n"
            "with open(os.environ['GH_FIXTURES']) as source: fixtures = json.load(source)\n"
            "if endpoint not in fixtures:\n"
            "    print('missing fixture: ' + endpoint, file=sys.stderr)\n"
            "    raise SystemExit(1)\n"
            "print(json.dumps(fixtures[endpoint]))\n", encoding="utf-8")
        fake.chmod(0o755)
        self.gh = {"OPENSWARM_GH_BIN": str(fake), "GH_FIXTURES": str(fixture_file),
                   "GH_CALL_LOG": str(call_log)}
        return call_log

    def set_ledger_finding(self, comment_id=701, title="Validate the archive path before extraction",
                           badge="P1", extra_comments=None, run_id="finding-run"):
        repo, number = "octo/project", 14
        comments = [{"id": comment_id, "user": {"login": "chatgpt-codex-connector[bot]"},
                     "body": f"**[{badge}] {title}**\n\nThe finding affects this code path.\n\n{badge} Badge"}]
        comments.extend(extra_comments or [])
        self.make_gh({f"repos/{repo}/pulls/{number}/comments?per_page=100&page=1": comments})
        path = Path(xs.LEDGER)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"id": run_id, "ts": xs.NOW(), "backend": "codex",
                                    "card_type": "fix", "files_changed": ["src/archive.py", "README.md"],
                                    "pr": {"repo": repo, "number": number,
                                           "url": f"https://github.com/{repo}/pull/{number}"},
                                    "review_findings": {"p0": 0, "p1": 1, "p2": 0}}) + "\n",
                        encoding="utf-8")
        fleet_path = Path(xs.fleet_ledger_path())
        fleet_path.parent.mkdir(parents=True, exist_ok=True)
        fleet_path.write_bytes(path.read_bytes())
        return path

    def run_propose(self, *args):
        original_argv = sys.argv
        try:
            sys.argv = ["openswarm", "lessons", "propose", *args]
            output = io.StringIO()
            with mock.patch.object(xs, "load_policy", return_value=self.policy), \
                    mock.patch.dict(os.environ, self.gh or {}), contextlib.redirect_stdout(output):
                xs.main()
            return output.getvalue()
        finally:
            sys.argv = original_argv

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

    def test_propose_writes_new_lesson_with_class_paths_profile_and_source(self):
        self.set_ledger_finding(extra_comments=[
            {"id": 702, "user": {"login": "reviewer"}, "body": "**[P0] Not from Codex**\nP0 Badge"},
            {"id": 703, "user": {"login": "chatgpt-codex-connector[bot]"},
             "body": "**[P2] Lower priority**\nP2 Badge"},
        ])

        output = self.run_propose("--fleet")

        proposal = self.root / "proposals" / xs.policy_day(self.policy)[0] / "L-0001.yaml"
        text = proposal.read_text(encoding="utf-8")
        self.assertIn('root_cause_class: "input_validation"', text)
        self.assertIn('card_types: ["fix"]', text)
        self.assertIn('paths: ["README.md", "src"]', text)
        self.assertIn('backends: ["codex"]', text)
        self.assertIn("#discussion_r701", text)
        self.assertTrue((proposal.parent / "summary.md").is_file())
        seen = json.loads((self.root / "proposals" / "seen.json").read_text(encoding="utf-8"))
        self.assertIn("octo/project:701", seen["comment_ids"])
        self.assertIn("wrote 1 lesson proposal", output)

    def test_propose_recurs_existing_class_and_overlapping_paths(self):
        self.write_lesson("validation.yaml", "L-0030", "validate input", paths="[src/*.py]",
                          root_cause_class="input_validation", recurrences=4)
        self.set_ledger_finding()

        self.run_propose()

        proposal = self.root / "proposals" / xs.policy_day(self.policy)[0] / "recurrence-L-0030-701.yaml"
        self.assertIn("recurrences: 5", proposal.read_text(encoding="utf-8"))
        self.assertEqual((self.lessons / "validation.yaml").read_text(encoding="utf-8").count("recurrences: 4"), 1)

    def test_unknown_finding_class_maps_to_other(self):
        self.write_lesson("existing.yaml", "L-0001", "unrelated", root_cause_class="error_handling")
        self.set_ledger_finding(title="Rename the summary heading", badge="P0")

        self.run_propose()

        proposal = self.root / "proposals" / xs.policy_day(self.policy)[0] / "L-0002.yaml"
        self.assertIn('root_cause_class: "other"', proposal.read_text(encoding="utf-8"))

    def test_comment_id_is_never_proposed_twice(self):
        ledger = self.set_ledger_finding()
        original = ledger.read_text(encoding="utf-8")
        ledger.write_text(original + original, encoding="utf-8")
        output_dir = self.root / "custom-out"

        self.run_propose("--out", str(output_dir))
        second = self.run_propose("--out", str(output_dir))

        proposals = list(output_dir.glob("L-*.yaml"))
        self.assertEqual(len(proposals), 1)
        self.assertIn("wrote 0 lesson proposal", second)
        self.assertEqual(list(json.loads((self.root / "proposals" / "seen.json").read_text())
                              ["comment_ids"]), ["octo/project:701"])

    def test_dry_run_prints_proposal_without_writing_proposals_or_seen_index(self):
        self.set_ledger_finding()
        output_dir = self.root / "dry-run-out"

        output = self.run_propose("--out", str(output_dir), "--dry-run")

        self.assertIn("--- L-0001.yaml ---", output)
        self.assertIn("# Lesson proposals", output)
        self.assertFalse(output_dir.exists())
        self.assertFalse((self.root / "proposals" / "seen.json").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
