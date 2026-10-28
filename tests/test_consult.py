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
FAKE = r"""#!/usr/bin/env python3
import json, os, sys, time
argv = sys.argv[1:]
backend = "codex" if argv[:1] == ["exec"] else ("agy" if "--output-format" in argv else "pi")
log_dir = os.environ["FAKE_CONSULT_LOG_DIR"]
with open(os.path.join(log_dir, backend + ".json"), "w") as stream:
    json.dump({"argv": argv, "cwd": os.getcwd(), "started": time.monotonic()}, stream)
if os.environ.get("FAKE_CONSULT_BARRIER"):
    deadline = time.monotonic() + 5
    while not all(os.path.isfile(os.path.join(log_dir, name + ".json")) for name in ("agy", "codex")):
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
elif backend == "agy":
    print(json.dumps({"response": text, "model": "agy-test"}))
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
        self.policy = xs.deep_merge(POLICY, {"concurrency": {"codex": 2, "agy": 2, "pi": 2, "total": 8}})
        self.env_keys = ("FAKE_CONSULT_LOG_DIR", "FAKE_CONSULT_BARRIER", "FAKE_CONSULT_TIMEOUT_BACKEND",
                         "FAKE_CONSULT_MALFORMED_BACKEND", "FAKE_CONSULT_REPAIR_BACKEND",
                         "FAKE_CONSULT_SLEEP_SECONDS")
        self.old_env = {key: os.environ.get(key) for key in self.env_keys}
        os.environ["FAKE_CONSULT_LOG_DIR"] = self.logs
        os.environ.pop("FAKE_CONSULT_TIMEOUT_BACKEND", None)
        os.environ.pop("FAKE_CONSULT_MALFORMED_BACKEND", None)
        os.environ.pop("FAKE_CONSULT_REPAIR_BACKEND", None)
        os.environ.pop("FAKE_CONSULT_SLEEP_SECONDS", None)
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

    def test_schema_answers_are_parallel_persisted_and_ledgered(self):
        code, result = self.run_consult(["codex:codex-test", "agy:gemini-test"])
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
        self.assertEqual(len(record["backends_asked"]), 2)
        self.assertFalse([name for name in os.listdir(xs.RUNS) if name.startswith("consult-slot-")])

    def test_each_backend_command_enforces_read_only_mode(self):
        code, _ = self.run_consult(["codex", "agy"])
        self.assertEqual(code, 0)
        def argv(backend):
            with open(os.path.join(self.logs, backend + ".json"), encoding="utf-8") as stream:
                return json.load(stream)["argv"]
        codex, agy = argv("codex"), argv("agy")
        self.assertEqual(codex[codex.index("-s") + 1], "read-only")
        self.assertEqual(codex[codex.index("-C") + 1], self.repo)
        self.assertEqual(agy[agy.index("--mode") + 1], "plan")
        self.assertEqual(agy[agy.index("--output-format") + 1], "json")
        self.assertIn("--json-schema", agy)
        self.assertTrue(os.path.isfile(agy[agy.index("--json-schema") + 1]))
        self.assertNotIn("--dangerously-skip-permissions", agy)
        self.assertEqual(os.listdir(self.repo), [])

    def test_executors_run_in_parallel(self):
        os.environ["FAKE_CONSULT_BARRIER"] = "1"
        start = time.monotonic()
        code, result = self.run_consult(["agy", "codex"], deadline=0.25)
        elapsed = time.monotonic() - start
        self.assertEqual(code, 0)
        self.assertLess(elapsed, 3.0)
        self.assertTrue(all(row["valid"] for row in result["answers"]))

    def test_total_concurrency_cap_serializes_admission(self):
        os.environ["FAKE_CONSULT_SLEEP_SECONDS"] = "0.6"
        self.policy["concurrency"]["total"] = 1
        code, _ = self.run_consult(["agy", "codex"], deadline=0.05)
        self.assertEqual(code, 0)
        starts = []
        for backend in ("agy", "codex"):
            with open(os.path.join(self.logs, backend + ".json"), encoding="utf-8") as stream:
                starts.append(json.load(stream)["started"])
        self.assertGreaterEqual(abs(starts[0] - starts[1]), 0.45)

    def test_malformed_and_timeout_answers_keep_raw_text_and_fail_exec_pass(self):
        os.environ["FAKE_CONSULT_MALFORMED_BACKEND"] = "agy"
        os.environ["FAKE_CONSULT_TIMEOUT_BACKEND"] = "codex"
        code, result = self.run_consult(["agy", "codex"], deadline=0.015)
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

    def test_default_selection_does_not_choose_unverified_pi(self):
        state = {"agy": self.state["agy"], "pi": self.state["pi"]}
        with self.assertRaisesRegex(ValueError, "two available"):
            consult.default_profiles(xs, state, self.policy)

    def test_cli_dispatch_writes_consult_and_ledger(self):
        state_path = os.path.join(self.tmp, "state.json")
        with open(state_path, "w", encoding="utf-8") as stream:
            json.dump({"agy": {"binary": self.script, "default_model": "gemini-test"},
                       "codex": {"binary": self.script, "models": [{"id": "codex-test"}]}}, stream)
        env = os.environ.copy()
        env.update({"OPENSWARM_HOME": self.data, "OPENSWARM_STATE_FILE": state_path,
                    "FAKE_CONSULT_LOG_DIR": self.logs})
        command = [sys.executable, os.path.join(os.path.dirname(os.path.dirname(__file__)), "bin", "openswarm"),
                   "consult", "--question", "Should this contract change?", "--ask", "agy", "--ask", "codex", "--json"]
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
        code, result = self.run_consult(["agy", "codex"])
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
        code, _ = self.run_consult(["agy", "codex"], context_file=[path])
        self.assertEqual(code, 0)
        for backend in ("agy", "codex"):
            with open(os.path.join(self.logs, backend + ".json"), encoding="utf-8") as stream:
                argv = json.load(stream)["argv"]
            self.assertTrue(any("Existing API returns a stable response envelope." in value for value in argv))


if __name__ == "__main__":
    unittest.main()
