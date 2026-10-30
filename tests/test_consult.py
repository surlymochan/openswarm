"""Read-only consult orchestration with isolated fake executors."""
import argparse
import contextlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import time
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"))
import _load  # noqa: E402
import openswarm_consult as consult  # noqa: E402

xs = _load.load()
POLICY = xs.load_policy()

class ConsultComparisonTest(unittest.TestCase):
    def test_early_agy_json_is_not_accepted_as_a_complete_answer(self):
        partial = {'status':'SUCCESS', 'finished_reason':'print_timeout',
                   'response':json.dumps({'opinion':'partial', 'confidence':'high', 'evidence':[], 'risks':[]})}
        self.assertIn('print_timeout', consult._agy_execution_error(json.dumps(partial), ''))

    def test_different_wording_is_not_labeled_disagreement(self):
        answers = [
            {'valid':True, 'family':'OpenAI', 'answer':{'opinion':'Empty input raises ZeroDivisionError.'}},
            {'valid':True, 'family':'Google', 'answer':{'opinion':'An empty list causes division by zero.'}},
        ]
        comparison = consult._agreement(answers)
        self.assertIsNone(comparison['identical_recommendations'])
        self.assertEqual(comparison['disagreement'], [])
        self.assertIn('compare the answers', comparison['summary'])

FAKE = r"""#!/usr/bin/env python3
import json, os, sys, time
argv = sys.argv[1:]
backend = "copilot" if "--available-tools=view" in argv else ("codex" if argv[:1] == ["exec"] else ("agy" if "--output-format" in argv else "pi"))
log_dir = os.environ["FAKE_CONSULT_LOG_DIR"]
with open(os.path.join(log_dir, backend + ".json"), "w") as stream:
    # On macOS Python 3.9, monotonic() has a process-local origin; these
    # timestamps are compared across two separate executor processes.
    started = time.clock_gettime(time.CLOCK_MONOTONIC) if hasattr(time, "clock_gettime") else time.time()
    json.dump({"argv": argv, "cwd": os.getcwd(), "started": started}, stream)
if os.environ.get("FAKE_CONSULT_BARRIER"):
    deadline = time.monotonic() + 5
    while not all(os.path.isfile(os.path.join(log_dir, name + ".json")) for name in ("agy", "pi")):
        if time.monotonic() >= deadline:
            sys.exit(9)
        time.sleep(0.01)
if os.environ.get("FAKE_CONSULT_TIMEOUT_BACKEND") == backend:
    time.sleep(30)
if os.environ.get("FAKE_CONSULT_SLEEP_SECONDS"):
    time.sleep(float(os.environ["FAKE_CONSULT_SLEEP_SECONDS"]))
body = {"opinion": "Keep the current contract", "confidence": "high",
        "evidence": ["README.md:10"], "risks": []}
repair_requested = any("Return only JSON" in value for value in argv)
if os.environ.get("FAKE_CONSULT_REPAIR_BACKEND") == backend and not repair_requested:
    body = {"opinion": 4, "confidence": "certain", "evidence": "README.md", "risks": []}
elif os.environ.get("FAKE_CONSULT_MALFORMED_BACKEND") == backend:
    body = {"opinion": 4, "confidence": "certain", "evidence": "README.md", "risks": []}
text = json.dumps(body)
if backend == "codex":
    output = argv[argv.index("-o") + 1]
    with open(output, "w") as stream:
        stream.write(text)
elif backend == "copilot":
    with open("consult-generated-file", "w") as stream:
        stream.write("discard me")
    print(json.dumps({"type": "assistant.message", "data": {"content": text}}))
    print(json.dumps({"type": "result", "exitCode": 0}))
elif backend == "agy":
    result = {"response": text, "model": "agy-test"}
    if os.environ.get("FAKE_CONSULT_AGY_EARLY"):
        result.update({"status":"SUCCESS", "finished_reason":"print_timeout"})
    print(json.dumps(result))
else:
    print(json.dumps({"type": "message_end", "message": {"role": "assistant",
        "content": [{"type": "text", "text": text}]}}))
"""


class ConsultTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="openswarm-consult-")
        self.script = os.path.join(self.tmp, "fake-executor")
        with open(self.script, "w") as stream:
            stream.write(textwrap.dedent(FAKE))
        os.chmod(self.script, os.stat(self.script).st_mode | stat.S_IEXEC)
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        subprocess.run(["git", "init", "-q", self.repo], check=True)
        subprocess.run(["git", "-C", self.repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-qm", "initial"], check=True)
        self.data = os.path.join(self.tmp, "data")
        xs.DATA = self.data
        xs.RUNS = os.path.join(self.data, "runs")
        xs.LEDGER = os.path.join(self.data, "ledger.jsonl")
        self.logs = os.path.join(self.tmp, "logs")
        os.makedirs(self.logs)
        self.state = {
            "codex": {"binary": self.script, "models": [{"id": "codex-test"}]},
            "agy": {"binary": self.script, "default_model": "gemini-test"},
            "pi": {"binary": self.script, "default_model": "minimax-test"},
        }
        self.policy = xs.deep_merge(POLICY, {"pi": {"require_isolation": False},
                                             "concurrency": {"codex": 2, "agy": 2, "pi": 2, "total": 8}})
        self.env_keys = ("FAKE_CONSULT_LOG_DIR", "FAKE_CONSULT_BARRIER", "FAKE_CONSULT_TIMEOUT_BACKEND",
                         "FAKE_CONSULT_MALFORMED_BACKEND", "FAKE_CONSULT_REPAIR_BACKEND",
                         "FAKE_CONSULT_SLEEP_SECONDS", "FAKE_CONSULT_AGY_EARLY")
        self.old_env = {key: os.environ.get(key) for key in self.env_keys}
        os.environ["FAKE_CONSULT_LOG_DIR"] = self.logs
        os.environ.pop("FAKE_CONSULT_TIMEOUT_BACKEND", None)
        os.environ.pop("FAKE_CONSULT_MALFORMED_BACKEND", None)
        os.environ.pop("FAKE_CONSULT_REPAIR_BACKEND", None)
        os.environ.pop("FAKE_CONSULT_SLEEP_SECONDS", None)
        os.environ.pop("FAKE_CONSULT_AGY_EARLY", None)
        os.environ.pop("FAKE_CONSULT_BARRIER", None)
        self.cwd_patch = mock.patch("openswarm_consult.os.getcwd", return_value=self.repo)
        self.cwd_patch.start()

    def tearDown(self):
        self.cwd_patch.stop()
        for key, value in self.old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_consult(self, backends, deadline=1, context_file=None):
        args = types.SimpleNamespace(question="Should this contract change?", question_file=None,
                                     context_file=context_file or [], ask=backends, lanes=None,
                                     deadline_minutes=deadline, json=True)
        output = io.StringIO()
        with mock.patch.object(xs, "get_state", return_value=self.state), contextlib.redirect_stdout(output):
            code = consult.consult(args, xs, self.policy)
        return code, json.loads(output.getvalue())

    def ledger(self):
        with open(xs.LEDGER, encoding="utf-8") as stream:
            return [json.loads(line) for line in stream]

    def test_default_skips_exhausted_agy_and_discards_copilot_worktree_changes(self):
        lanes = [{"name": "GH1", "backend": "copilot", "model": "haiku-test", "effort": "medium", "binary": self.script, "repo": self.repo}]
        rows = [{"backend": "agy", "model": consult._model(xs, self.state, self.policy, "agy", None, None), "state": "quota_exhausted"}]
        with mock.patch.object(xs, "lane_status_rows", return_value=rows), mock.patch.object(xs, "lane_config", return_value=lanes), mock.patch.object(consult.sys, "platform", "linux"):
            code, result = self.run_consult([])
        self.assertEqual(code, 0)
        copilot = next(row for row in result["answers"] if row["backend"] == "copilot")
        self.assertTrue(copilot["valid"])
        self.assertTrue(copilot["worktree_changes_detected"])
        with open(os.path.join(self.logs, "copilot.json"), encoding="utf-8") as stream:
            worktree = json.load(stream)["cwd"]
        self.assertNotEqual(worktree, self.repo)
        self.assertFalse(os.path.exists(worktree))
        self.assertFalse(os.path.exists(os.path.join(self.repo, "consult-generated-file")))

    def test_consult_outside_git_uses_temporary_worktrees(self):
        outside = os.path.join(self.tmp, "outside")
        os.mkdir(outside)
        with mock.patch("openswarm_consult.os.getcwd", return_value=outside):
            code, result = self.run_consult(["codex", "agy"])
        self.assertEqual(code, 0)
        self.assertTrue(all(row["valid"] for row in result["answers"]))
        for backend in ("codex", "agy"):
            with open(os.path.join(self.logs, backend + ".json"), encoding="utf-8") as stream:
                worktree = json.load(stream)["cwd"]
            self.assertNotEqual(worktree, outside)
            self.assertFalse(os.path.exists(worktree))
        self.assertEqual(os.listdir(outside), [])

    def test_schema_answers_are_parallel_persisted_and_ledgered(self):
        code, result = self.run_consult(["codex:codex-test", "agy:gemini-test", "pi:minimax-test"])
        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "done")
        self.assertTrue(all(row["valid"] for row in result["answers"]))
        self.assertTrue(result["comparison"]["identical_recommendations"])
        self.assertTrue(os.path.isfile(result["consult_file"]))
        with open(result["consult_file"], encoding="utf-8") as stream:
            self.assertEqual(json.load(stream)["id"], result["id"])
        record, = self.ledger()
        self.assertEqual(record["card_type"], "research")
        self.assertTrue(record["exec_pass"])
        self.assertEqual(len(record["backends_asked"]), 3)
        self.assertFalse([name for name in os.listdir(xs.RUNS) if name.startswith("consult-slot-")])

    def test_early_agy_result_produces_partial_consult(self):
        os.environ['FAKE_CONSULT_AGY_EARLY'] = '1'
        code, result = self.run_consult(['codex', 'agy'])
        self.assertEqual(code, 1)
        self.assertEqual(result['status'], 'partial')
        agy = next(x for x in result['answers'] if x['backend'] == 'agy')
        self.assertFalse(agy['valid'])
        self.assertIn('print_timeout', agy['error'])
        self.assertFalse([name for name in os.listdir(xs.RUNS) if name.startswith('consult-slot-')])

    def test_each_backend_command_enforces_read_only_mode(self):
        code, _ = self.run_consult(["codex", "agy", "pi"])
        self.assertEqual(code, 0)
        def argv(backend):
            with open(os.path.join(self.logs, backend + ".json"), encoding="utf-8") as stream:
                return json.load(stream)["argv"]
        codex, agy, pi = argv("codex"), argv("agy"), argv("pi")
        self.assertEqual(codex[codex.index("-s") + 1], "read-only")
        self.assertNotEqual(codex[codex.index("-C") + 1], self.repo)
        self.assertFalse(os.path.exists(codex[codex.index("-C") + 1]))
        self.assertEqual(agy[agy.index("--mode") + 1], "plan")
        self.assertEqual(agy[agy.index("--output-format") + 1], "json")
        self.assertIn("--json-schema", agy)
        self.assertTrue(os.path.isfile(agy[agy.index("--json-schema") + 1]))
        self.assertNotIn("--dangerously-skip-permissions", agy)
        self.assertEqual(pi[pi.index("--tools") + 1], consult.READ_ONLY_PI_TOOLS)
        self.assertNotIn("bash", pi[pi.index("--tools") + 1])
        self.assertNotIn("edit", pi[pi.index("--tools") + 1])
        self.assertNotIn("write", pi[pi.index("--tools") + 1])
        self.assertEqual(os.listdir(self.repo), [".git"])

    def test_executors_run_in_parallel(self):
        os.environ["FAKE_CONSULT_BARRIER"] = "1"
        start = time.monotonic()
        code, result = self.run_consult(["agy", "pi"], deadline=0.25)
        elapsed = time.monotonic() - start
        self.assertEqual(code, 0)
        self.assertLess(elapsed, 3.0)
        self.assertTrue(all(row["valid"] for row in result["answers"]))

    def test_total_concurrency_cap_serializes_admission(self):
        os.environ["FAKE_CONSULT_SLEEP_SECONDS"] = "0.6"
        self.policy["concurrency"]["total"] = 1
        code, _ = self.run_consult(["agy", "pi"], deadline=0.05)
        self.assertEqual(code, 0)
        starts = []
        for backend in ("agy", "pi"):
            with open(os.path.join(self.logs, backend + ".json"), encoding="utf-8") as stream:
                starts.append(json.load(stream)["started"])
        self.assertGreaterEqual(abs(starts[0] - starts[1]), 0.45)

    def test_malformed_and_timeout_answers_keep_raw_text_and_fail_exec_pass(self):
        os.environ["FAKE_CONSULT_MALFORMED_BACKEND"] = "agy"
        os.environ["FAKE_CONSULT_TIMEOUT_BACKEND"] = "pi"
        code, result = self.run_consult(["agy", "pi"], deadline=0.015)
        self.assertEqual(code, 1)
        self.assertFalse(result["answers"][0]["valid"])
        self.assertIn("opinion", result["answers"][0]["raw"])
        self.assertFalse(result["answers"][1]["valid"])
        self.assertEqual(result["answers"][1]["error"], "deadline exceeded")
        self.assertEqual(self.ledger()[0]["exec_pass"], False)

    def test_default_selection_uses_distinct_available_families(self):
        rows = consult.default_profiles(xs, self.state, self.policy)
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0]["family"], rows[1]["family"])

    def test_cli_dispatch_writes_consult_and_ledger(self):
        state_path = os.path.join(self.tmp, "state.json")
        with open(state_path, "w", encoding="utf-8") as stream:
            json.dump({"agy": {"binary": self.script, "default_model": "gemini-test"},
                       "pi": {"binary": self.script, "default_model": "minimax-test"}}, stream)
        env = os.environ.copy()
        env.update({"OPENSWARM_HOME": self.data, "OPENSWARM_STATE_FILE": state_path,
                    "FAKE_CONSULT_LOG_DIR": self.logs})
        os.makedirs(self.data, exist_ok=True)
        with open(os.path.join(self.data, "policy.local.json"), "w", encoding="utf-8") as stream:
            json.dump({"pi": {"require_isolation": False}}, stream)
        command = [sys.executable, os.path.join(os.path.dirname(os.path.dirname(__file__)), "bin", "openswarm"),
                   "consult", "--question", "Should this contract change?", "--ask", "agy", "--ask", "pi", "--json"]
        result = subprocess.run(command, cwd=self.repo, env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(os.path.isfile(payload["consult_file"]))
        with open(os.path.join(self.data, "ledger.jsonl"), encoding="utf-8") as stream:
            record = json.loads(stream.readline())
        self.assertEqual(record["card_type"], "research")
        self.assertTrue(record["exec_pass"])

    def test_schema_rejects_extra_keys_and_wrong_field_types(self):
        answer, error = consult.parse_answer(json.dumps({"opinion": "yes", "confidence": "high",
                                                          "evidence": [], "risks": [], "extra": True}))
        self.assertIsNone(answer)
        self.assertIn("keys", error)
        answer, error = consult.parse_answer(json.dumps({"opinion": "yes", "confidence": "high",
                                                          "evidence": [""], "risks": []}))
        self.assertIsNone(answer)
        self.assertIn("evidence", error)
        for value in ("nan", "inf", "0"):
            with self.assertRaises(argparse.ArgumentTypeError):
                consult.positive_minutes(value)

    def test_parser_extracts_envelope_fenced_and_prose_wrapped_answers(self):
        answer = {"opinion": "Keep it", "confidence": "high", "evidence": ["README.md:10"], "risks": []}
        encoded = json.dumps(answer)
        cases = (
            json.dumps({"response": encoded, "model": "agy-test"}),
            f"```json\n{encoded}\n```",
            f"Analysis first: {{\"note\": true}}\n{encoded}\nDone.",
        )
        for raw in cases:
            with self.subTest(raw=raw):
                self.assertEqual(consult.parse_answer(raw), (answer, None))
        self.assertIn("Do not invoke tools or commands", consult.make_prompt("question", []))

    def test_agy_permission_denial_is_not_treated_as_a_format_retry(self):
        raw = json.dumps({"response": "", "denied_actions": [{"action": "command"}]})
        self.assertIn("tool action was denied", consult._agy_execution_error(
            raw, "agy: no output produced; command permission denied"))

    def test_invalid_agy_answer_retries_and_preserves_both_raw_responses(self):
        os.environ["FAKE_CONSULT_REPAIR_BACKEND"] = "agy"
        code, result = self.run_consult(["agy", "pi"])
        self.assertEqual(code, 0)
        agy_answer, = [row for row in result["answers"] if row["backend"] == "agy"]
        self.assertTrue(agy_answer["valid"])
        self.assertIn("attempt 1", agy_answer["raw"])
        self.assertIn("attempt 2", agy_answer["raw"])
        self.assertIn("opinion", agy_answer["raw"])

    def test_context_file_is_included_in_the_shared_prompt(self):
        path = os.path.join(self.tmp, "context.md")
        with open(path, "w", encoding="utf-8") as stream:
            stream.write("Existing API returns a stable response envelope.")
        code, _ = self.run_consult(["agy", "pi"], context_file=[path])
        self.assertEqual(code, 0)
        for backend in ("agy", "pi"):
            with open(os.path.join(self.logs, backend + ".json"), encoding="utf-8") as stream:
                argv = json.load(stream)["argv"]
            self.assertTrue(any("Existing API returns a stable response envelope." in value for value in argv))


if __name__ == "__main__":
    unittest.main()
