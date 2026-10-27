"""Pi executor: fake pi, no network and no real CLI.

Covers json event parsing, usage on result.json and the ledger, budget kill, routing opt-in.
"""
import argparse, contextlib, io, json, os, shutil, stat, sys, tempfile, textwrap, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _load  # noqa: E402

xs = _load.load()
POLICY = xs.load_policy()
FAKE = r"""#!/usr/bin/env python3
import json, os, sys, time
mode = os.environ.get("FAKE_PI_MODE", "ok")
log = os.environ.get("FAKE_PI_LOG")
if log:
    with open(log, "a") as fh:
        json.dump(sys.argv, fh)
        fh.write("\n")
sessions = sys.argv[sys.argv.index("--session-dir") + 1] if "--session-dir" in sys.argv else "."
attempt_marker = os.path.join(sessions, "fake-rate-attempted")
rate_once_first = os.environ.get("FAKE_PI_MODE") == "rate-once" and not os.path.exists(attempt_marker)
if rate_once_first:
    open(attempt_marker, "w").close()
if mode == "sleep":
    time.sleep(30)
    sys.exit(0)
if mode in ("risk", "quick-risk"):
    print(json.dumps({"type": "tool_execution_start", "toolName": "bash", "args": {"command": "git push secret-value"}}), flush=True)
    if mode == "risk":
        time.sleep(30)
def end(text, usage, **extra):
    msg = {"role": "assistant", "content": [{"type": "text", "text": text}], "usage": usage}
    msg.update(extra)
    print(json.dumps({"type": "message_end", "message": msg}))
print(json.dumps({"type": "session", "id": "s1"}))
print(json.dumps({"type": "message_end", "message": {"role": "user", "content": [], "usage": {"input": 999}}}))
if mode == "rate" or rate_once_first:
    end("", {"input": 1, "output": 0, "cacheRead": 0, "cacheWrite": 0},
        stopReason="error", errorMessage="429 rate limit exceeded")
    sys.stderr.write("rate limit: too many requests\n")
    sys.exit(1)
end("looking", {"input": 100, "output": 20, "cacheRead": 5, "cacheWrite": 1, "totalTokens": 126})
body = {"status": "done", "summary": "added add", "files_changed": ["add.py"],
        "tests": "not run: host verify", "followups": []}
end(json.dumps(body), {"input": 20, "output": 25, "cacheRead": 3, "cacheWrite": 1, "totalTokens": 49})
"""


def ns(**kw):
    base = dict(type="impl", difficulty=3, context_tokens=0, backend="pi", model=None, effort=None,
                prompt="add a function add(a, b)", task_file=None, title="pi test", cwd=".",
                no_worktree=True, verify=None, verify_baseline=False, keep_worktree=False, no_fallback=False,
                budget_minutes=5, detach=False, run_id=None, dry_run=False)
    base.update(kw)
    return argparse.Namespace(**base)


class PiBackend(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="openswarm-pi-")
        self.script = os.path.join(self.tmp, "pi")
        with open(self.script, "w") as f:
            f.write(textwrap.dedent(FAKE))
        os.chmod(self.script, os.stat(self.script).st_mode | stat.S_IEXEC)
        self.log = os.path.join(self.tmp, "argv.json")
        self.cwd = os.path.join(self.tmp, "work")
        os.makedirs(self.cwd)
        self._env = {k: os.environ.get(k) for k in ("OPENSWARM_PI_BIN", "OPENSWARM_STATE_FILE", "FAKE_PI_MODE", "FAKE_PI_LOG")}
        os.environ["OPENSWARM_PI_BIN"] = self.script
        os.environ["OPENSWARM_STATE_FILE"] = _load.FIXTURE_STATE
        os.environ["FAKE_PI_LOG"] = self.log
        os.environ["FAKE_PI_MODE"] = "ok"
        if os.path.exists(xs.BREAKERS):
            os.remove(xs.BREAKERS)

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def route(self, prof, policy=None):
        with open(_load.FIXTURE_STATE) as f:
            st = json.load(f)
        return xs.route(prof, st, policy or POLICY)

    def dispatch(self, policy=None, **kw):
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                xs.cmd_run(ns(cwd=self.cwd, **kw), policy or POLICY)
            except SystemExit as e:
                return e.code
        self.fail("cmd_run returned without exiting")

    def last_result(self):
        with open(xs.LEDGER) as f:
            entry = json.loads(f.readlines()[-1])
        with open(os.path.join(xs.RUNS, entry["id"], "result.json")) as f:
            return entry, json.load(f)

    def test_not_auto_selected_and_forced_model(self):
        for kind, diff in (("impl", 3), ("review", 3), ("mechanical", 1)):
            self.assertNotEqual(self.route({"type": kind, "difficulty": diff}).get("backend"), "pi")
        forced = self.route({"type": "impl", "difficulty": 3, "backend": "pi"})
        self.assertTrue(forced["ok"])
        self.assertEqual(forced["backend"], "pi")
        self.assertIsNone(forced["model"])
        named = self.route({"type": "impl", "difficulty": 2, "backend": "pi", "model": "some-model"})
        self.assertEqual(named["model"], "some-model")
        self.assertNotIn("opencode_model", named)
        opted = json.loads(json.dumps(POLICY))
        opted["routing"]["default_order"] = ["pi", "codex", "ocgo", "deepseek"]
        self.assertEqual(self.route({"type": "impl", "difficulty": 3}, opted)["backend"], "pi")

    def test_missing_override_is_an_error(self):
        self.assertEqual(xs.pi_bin(), self.script)
        os.environ["OPENSWARM_PI_BIN"] = os.path.join(self.tmp, "missing")
        self.assertIsNone(xs.pi_bin())

    def test_success_parses_result_and_records_usage(self):
        knowledge = os.path.join(self.tmp, "knowledge")
        os.makedirs(knowledge)
        with open(os.path.join(knowledge, "card-types.yaml"), "w") as stream:
            stream.write("card_types:\n  - feature\n")
        policy = xs.deep_merge(POLICY, {"knowledge": {"repo": knowledge}})
        code = self.dispatch(policy=policy, card_type="feature", rework_of="previous-run")
        self.assertEqual(code, 0)
        entry, result = self.last_result()
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["backend"], "pi")
        usage = {"input": 120, "output": 45, "cacheRead": 8, "cacheWrite": 2}
        self.assertEqual(result["usage"], usage)
        self.assertEqual(result["result"]["files_changed"], ["add.py"])
        self.assertEqual(entry["usage"], usage)
        self.assertEqual(entry["card_type"], "feature")
        self.assertEqual(entry["rework_of"], "previous-run")
        self.assertEqual(entry["run_id"], entry["id"])
        self.assertEqual(entry["area"], ["add.py"])
        self.assertEqual(entry["tokens"], {"input": 120, "output": 45, "total": 165})
        for key in ("host", "lane", "backend", "model", "effort", "status", "error_class",
                    "verify_rc", "attempts", "seconds", "branch", "commit", "pr",
                    "review_findings", "merged", "defect_within_7d", "lessons_injected"):
            self.assertIn(key, entry)
        self.assertIsNone(entry["pr"])
        self.assertIsNone(entry["review_findings"])
        self.assertIsNone(entry["merged"])
        self.assertIsNone(entry["defect_within_7d"])
        self.assertEqual(entry["lessons_injected"], [])
        with open(self.log) as fh:
            argv = json.load(fh)
        self.assertEqual(argv[argv.index("--mode") + 1], "json")
        self.assertEqual(argv[argv.index("--tools") + 1], "read,bash,edit,write,grep,find,ls")
        self.assertIn("-p", argv)
        sessions = argv[argv.index("--session-dir") + 1]
        self.assertTrue(os.path.isdir(sessions))
        self.assertFalse(os.path.commonpath([sessions, self.cwd]) == self.cwd)
        self.assertNotIn("--model", argv)

    def test_error_event_fails_the_attempt(self):
        os.environ["FAKE_PI_MODE"] = "rate"
        policy = xs.deep_merge(POLICY, {"rate_limit": {"max_retries": 0}})
        code = self.dispatch(policy=policy)
        self.assertEqual(code, 1)
        entry, result = self.last_result()
        self.assertEqual(result["attempts"][0]["error_class"], "rate_limit")
        self.assertEqual(xs.rate_limit_breaker_key("pi", {"model": None}, "429"), "pi/all")
        self.assertIn("pi/all", xs.breakers())

    def test_rate_limit_retries_with_pi_session_continue(self):
        os.environ["FAKE_PI_MODE"] = "rate-once"
        policy = xs.deep_merge(POLICY, {"rate_limit": {"backoff_seconds": [0], "max_retries": 1}})
        self.assertEqual(self.dispatch(policy=policy), 0)
        entry, result = self.last_result()
        self.assertEqual(result["status"], "done")
        self.assertEqual([attempt["error_class"] for attempt in result["attempts"]], ["rate_limit", None])
        event_files = [os.path.join(result["run_dir"], name) for name in os.listdir(result["run_dir"])
                       if name.startswith("events.") and name.endswith(".jsonl")]
        events = []
        for path in event_files:
            with open(path) as fh:
                events.extend(json.loads(line) for line in fh)
        self.assertEqual(sum(event.get("type") == "RATELIMIT" for event in events), 1)
        with open(self.log) as fh:
            calls = [json.loads(line) for line in fh]
        self.assertEqual(len(calls), 2)
        self.assertNotIn("--continue", calls[0])
        self.assertIn("--continue", calls[1])

    def test_budget_kills_the_process(self):
        os.environ["FAKE_PI_MODE"] = "sleep"
        rdir = os.path.join(self.tmp, "run")
        os.makedirs(rdir)
        evp = os.path.join(rdir, "events.jsonl")
        rc, final, meta, _err = xs.exec_pi({"backend": "pi", "model": None}, {}, "sleep", self.cwd, rdir, 1, evp)
        self.assertEqual(rc, 124)
        self.assertIsNone(final)
        self.assertEqual(meta["usage"], {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0})

    def test_risk_stops_runner_even_if_executor_reports_done(self):
        for mode in ("risk", "quick-risk"):
            with self.subTest(mode=mode):
                os.environ["FAKE_PI_MODE"] = mode
                self.assertEqual(self.dispatch(verify="true"), 1)
                entry, result = self.last_result()
                self.assertEqual(result["status"], "stopped")
                self.assertEqual(entry["error_class"], "risk_blocked")
                self.assertIsNone(result["verify"])
                self.assertEqual(result["risk_blocks"][0]["risk"], "push")
                self.assertNotIn("secret-value", json.dumps(result))


if __name__ == "__main__":
    unittest.main(verbosity=2)
