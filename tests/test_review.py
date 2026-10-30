"""Peer reviews use stubbed reviewers and local git fixtures only."""
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


class PeerReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="openswarm-review-")
        self.root = Path(self.tmp.name)
        self.data = self.root / "data"
        self.runs = self.data / "runs"
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.constants = {key: getattr(xs, key) for key in ("DATA", "LEDGER", "RUNS")}
        xs.DATA, xs.LEDGER, xs.RUNS = str(self.data), str(self.data / "ledger.jsonl"), str(self.runs)
        self.git("init", "-q")
        self.git("config", "user.name", "Review Test")
        self.git("config", "user.email", "review@example.invalid")
        (self.repo / "module.py").write_text("value = 1\n", encoding="utf-8")
        self.git("add", "module.py")
        self.git("commit", "-q", "-m", "base")
        self.base = self.git("rev-parse", "HEAD").strip()
        self.git("checkout", "-qb", "openswarm/run-1")
        (self.repo / "module.py").write_text("value = 2\n", encoding="utf-8")
        self.git("commit", "-qam", "change")
        self.run_dir = self.runs / "run-1"
        self.run_dir.mkdir(parents=True)
        self.result = {"id": "run-1", "status": "done", "backend": "codex", "model": "codex-test",
                       "branch": "openswarm/run-1", "commit": self.git("rev-parse", "HEAD").strip(),
                       "workspace": {"repo": str(self.repo), "base": self.base}}
        (self.run_dir / "result.json").write_text(json.dumps(self.result), encoding="utf-8")
        self.ledger_entry = {"id": "run-1", "run_id": "run-1", "review_findings": {"p0": 0, "p1": 1, "p2": 0}}
        self.write_ledger(self.ledger_entry)

    def tearDown(self):
        for key, value in self.constants.items():
            setattr(xs, key, value)
        self.tmp.cleanup()

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, check=True, capture_output=True,
                              text=True).stdout

    def write_ledger(self, *entries):
        self.data.mkdir(parents=True, exist_ok=True)
        Path(xs.LEDGER).write_text("".join(json.dumps(entry) + "\n" for entry in entries), encoding="utf-8")

    def test_perform_review_stores_findings_and_peer_ledger_fields(self):
        findings = [{"severity": "P1", "path": "module.py", "line": 1,
                     "title": "Value is not preserved", "body": "The changed value breaks the contract."}]
        with mock.patch.object(xs, "review_available", return_value={
                "pi": {"backend": "pi", "binary": "/fake/pi", "model": "pi-model"}}), \
                mock.patch.object(xs, "invoke_reviewer", return_value={"findings": findings, "verdict": "changes"}) as invoke:
            payload = xs.perform_peer_review("run-1", None, {"review": {"prefer": ["codex", "pi"]}})

        self.assertEqual(payload, {"findings": findings, "verdict": "changes"})
        self.assertIn("Diff:", invoke.call_args.args[1])
        saved = json.loads((self.run_dir / "review.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["by"], "pi:pi-model")
        ledger = json.loads(Path(xs.LEDGER).read_text(encoding="utf-8"))
        self.assertEqual(ledger["peer_review"], {"by": "pi:pi-model", "verdict": "changes",
                                                  "p0": 0, "p1": 1, "p2": 0})
        self.assertEqual(ledger["review_findings"], {"p0": 0, "p1": 1, "p2": 0})

    def test_family_selection_uses_policy_order_and_opposite_family(self):
        available = {backend: {"backend": backend, "binary": f"/fake/{backend}"}
                     for backend in ("codex", "agy", "pi", "cursor")}
        policy = {"review": {"prefer": ["codex", "agy", "pi", "cursor"]}}
        self.assertEqual(xs.choose_review_backend("codex", policy, available)["backend"], "agy")
        self.assertEqual(xs.choose_review_backend("pi", policy, available)["backend"], "codex")
        self.assertTrue(xs.review_family_compatible("gemini", "codex"))
        self.assertFalse(xs.review_family_compatible("codex", "codex"))

    def test_reviewer_commands_are_read_only(self):
        policy = {"pi": {"provider": "fixture", "require_isolation": False}}
        cases = {
            "codex": ("codex", ["--sandbox", "read-only"]),
            "agy": ("agy", ["--mode", "plan", "--json-schema"]),
            "pi": ("pi", ["--tools", "read,grep,find,ls"]),
            "cursor": ("cursor", ["--mode=ask"]),
        }
        for backend, (binary, required) in cases.items():
            scratch = self.root / f"scratch-{backend}"
            scratch.mkdir()
            command = xs.review_command({"backend": backend, "binary": f"/fake/{binary}",
                                         "model": "fixture-model"}, "review this diff",
                                        str(self.repo), str(scratch), policy)
            for token in required:
                self.assertIn(token, command)
            if backend == "agy":
                self.assertEqual(command[command.index("--output-format") + 1], "json")
            if backend == "pi":
                self.assertNotIn("bash", command)
                self.assertNotIn("edit", command)
                self.assertNotIn("write", command)

    def test_auto_review_trigger_respects_default_and_policy_card_types(self):
        with mock.patch.object(xs, "spawn_auto_review", return_value=True) as spawn:
            self.assertTrue(xs.maybe_schedule_auto_review("run-1", "fix", "done", "branch", "commit", {}))
            self.assertFalse(xs.maybe_schedule_auto_review("run-1", "docs", "done", "branch", "commit", {}))
            self.assertFalse(xs.maybe_schedule_auto_review(
                "run-1", "fix", "done", "branch", "commit", {"review": {"auto_card_types": []}}))
        self.assertEqual(spawn.call_count, 1)

    def test_auto_review_concurrency_cap_and_failure_do_not_escape_run_hook(self):
        held_fds = []

        def keep_slot(_command, **options):
            held_fds.append(os.dup(options["pass_fds"][0]))

        policy = {"review": {"max_concurrent": 1}}
        try:
            with mock.patch.object(xs.subprocess, "Popen", side_effect=keep_slot):
                self.assertTrue(xs.spawn_auto_review("run-1", policy))
                self.assertFalse(xs.spawn_auto_review("run-1", policy))
            os.close(held_fds.pop())
            with mock.patch.object(xs.subprocess, "Popen", side_effect=OSError("spawn failed")):
                self.assertFalse(xs.maybe_schedule_auto_review(
                    "run-1", "fix", "done", "branch", "commit", policy))
        finally:
            for fd in held_fds:
                os.close(fd)

    def test_cli_json_output_uses_findings_schema(self):
        payload = {"findings": [], "verdict": "approve"}
        event = json.dumps({"type": "result", "response": json.dumps(payload)})
        self.assertEqual(xs.review_payload_from_text(event), payload)
        output = io.StringIO()
        with mock.patch.object(xs, "perform_peer_review", return_value=payload), \
                contextlib.redirect_stdout(output):
            xs.cmd_review(type("Args", (), {"run_id": "run-1", "by": None, "json": True})(), {})
        self.assertEqual(json.loads(output.getvalue()), payload)

    def test_review_parser_extracts_first_schema_object_from_common_wrappers(self):
        payload = {"findings": [], "verdict": "approve"}
        encoded = json.dumps(payload)
        cases = (
            json.dumps({"response": encoded, "model": "agy-test"}),
            f"```json\n{encoded}\n```",
            f"Review complete. {{\"note\": \"not a findings object\"}}\n{encoded}",
        )
        for raw in cases:
            with self.subTest(raw=raw):
                self.assertEqual(xs.review_payload_from_text(raw), payload)

    def test_invalid_review_response_retries_and_saves_redacted_raw_output(self):
        invalid = "I could not format JSON. ghp_12345678901234567890"
        valid = json.dumps({"conversation_id": "b71c8819-baae-4a9a-ab6a-fd65e1932b43",
                            "response": "```json\n{\"findings\": [], \"verdict\": \"approve\"}\n```"})
        completed = [
            subprocess.CompletedProcess([], 0, invalid, ""),
            subprocess.CompletedProcess([], 0, valid, ""),
        ]
        with mock.patch.object(xs.subprocess, "run", side_effect=completed) as run:
            payload = xs.invoke_reviewer({"backend": "agy", "binary": "/fake/agy", "model": "test"},
                                         "review the diff", str(self.repo), str(self.run_dir), {})
        self.assertEqual(payload, {"findings": [], "verdict": "approve"})
        self.assertEqual(run.call_count, 2)
        self.assertIn("Return only JSON", run.call_args_list[1].args[0][run.call_args_list[1].args[0].index("-p") + 1])
        raw = (self.run_dir / "review.raw.txt").read_text(encoding="utf-8")
        self.assertIn("attempt 1", raw)
        self.assertIn("attempt 2", raw)
        self.assertIn("[REDACTED]", raw)
        self.assertNotIn("ghp_12345678901234567890", raw)
        self.assertNotIn("b71c8819-baae-4a9a-ab6a-fd65e1932b43", raw)

    def test_failed_review_still_saves_raw_output(self):
        completed = [
            subprocess.CompletedProcess([], 0, "not json", ""),
            subprocess.CompletedProcess([], 0, "still not json", ""),
        ]
        with mock.patch.object(xs.subprocess, "run", side_effect=completed):
            with self.assertRaisesRegex(RuntimeError, "after repair retry"):
                xs.invoke_reviewer({"backend": "agy", "binary": "/fake/agy"}, "review", str(self.repo),
                                   str(self.run_dir), {})
        raw = (self.run_dir / "review.raw.txt").read_text(encoding="utf-8")
        self.assertIn("not json", raw)
        self.assertIn("still not json", raw)

    def test_agy_permission_denial_is_reported_without_a_paid_format_retry(self):
        output = json.dumps({"response": "", "denied_actions": [{"action": "command"}]})
        completed = subprocess.CompletedProcess([], 0, output, "agy: no output produced; command permission denied")
        with mock.patch.object(xs.subprocess, "run", return_value=completed) as run:
            with self.assertRaisesRegex(RuntimeError, "tool action was denied"):
                xs.invoke_reviewer({"backend": "agy", "binary": "/fake/agy"}, "review", str(self.repo),
                                   str(self.run_dir), {})
        self.assertEqual(run.call_count, 1)
        raw = (self.run_dir / "review.raw.txt").read_text(encoding="utf-8")
        self.assertIn("denied_actions", raw)
        self.assertIn("permission denied", raw)


if __name__ == "__main__":
    unittest.main(verbosity=2)
