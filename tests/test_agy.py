"""Antigravity (agy) executor: fake agy, no network and no real CLI.

Covers stream-json parsing, usage on result.json and the ledger, the gemini alias, routing opt-in,
the auth error class, 429/quota mapping and the fleet status line with and without agy installed.
"""
import argparse, contextlib, io, json, os, shutil, stat, subprocess, sys, tempfile, textwrap, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _load  # noqa: E402

xs = _load.load()
POLICY = xs.load_policy()
FAKE = r"""#!/usr/bin/env python3
import json, os, sys, time
mode = os.environ.get("FAKE_AGY_MODE", "ok")
log = os.environ.get("FAKE_AGY_LOG")
if log:
    with open(log, "a") as fh:
        json.dump(sys.argv[1:], fh)
        fh.write("\n")
if sys.argv[1:2] == ["models"]:
    if mode == "auth":
        sys.stderr.write("authentication required: run `agy login` first\n")
        sys.exit(1)
    print("gemini-3.8-flash-high")
    print("gemini-3.8-flash-medium")
    print("gemini-3.8-flash-low")
    print("claude-sonnet-4-6")
    sys.exit(0)
if mode == "sleep":
    time.sleep(30)
    sys.exit(0)
if mode == "auth":
    sys.stderr.write("FatalAuthenticationError: authentication required\n")
    sys.exit(1)
if mode == "long-quota":
    print(json.dumps({"type":"result", "status":"ERROR", "error":"Individual quota reached. Resets in 131h29m32s."}))
    sys.exit(1)
if mode == "rate" or (mode == "rate-once" and not os.path.exists(log + ".retry")):
    if mode == "rate-once":
        open(log + ".retry", "w").close()
    print(json.dumps({"type": "init", "conversation_id": "conv-rate"}))
    print(json.dumps({"type": "result", "conversation_id": "conv-rate", "status": "ERROR",
                      "error": "RESOURCE_EXHAUSTED: quota exceeded (429)",
                      "usage": {"input_tokens": 1, "output_tokens": 0}}))
    sys.stderr.write("429 rate limit exceeded\n")
    sys.exit(1)
body = {"status": "done", "summary": "added add", "files_changed": ["add.py"],
        "tests": "not run: host verify", "followups": []}
if mode == "print-timeout":
    print(json.dumps({"event":"result", "result":{"status":"SUCCESS", "finished_reason":"print_timeout",
                           "response":json.dumps(body)}}))
    sys.exit(0)
print(json.dumps({"type": "init", "conversation_id": "conv-1"}))
print(json.dumps({"type": "step_update", "text": "writing the report"}))
print(json.dumps({"type": "result", "conversation_id": "conv-1", "status": "SUCCESS",
                  "response": json.dumps(body), "duration_seconds": 1.5, "num_turns": 2,
                  "usage": {"input_tokens": 120, "output_tokens": 45, "thinking_tokens": 3,
                            "cache_read_tokens": 8, "total_tokens": 176}}))
"""


def ns(**kw):
    base = dict(type="impl", difficulty=3, context_tokens=0, backend="agy", model=None, effort=None,
                prompt="add a function add(a, b)", task_file=None, title="agy test", cwd=".",
                no_worktree=True, verify=None, verify_baseline=False, keep_worktree=False, no_fallback=False,
                budget_minutes=5, detach=False, run_id=None, dry_run=False)
    base.update(kw)
    return argparse.Namespace(**base)


class AgyBackend(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="openswarm-agy-")
        self.script = os.path.join(self.tmp, "agy")
        with open(self.script, "w") as f:
            f.write(textwrap.dedent(FAKE))
        os.chmod(self.script, os.stat(self.script).st_mode | stat.S_IEXEC)
        self.log = os.path.join(self.tmp, "argv.jsonl")
        self.cwd = os.path.join(self.tmp, "work")
        os.makedirs(self.cwd)
        self._env = {k: os.environ.get(k) for k in ("OPENSWARM_AGY_BIN", "OPENSWARM_STATE_FILE",
                                                    "FAKE_AGY_MODE", "FAKE_AGY_LOG")}
        os.environ["OPENSWARM_AGY_BIN"] = self.script
        os.environ["OPENSWARM_STATE_FILE"] = _load.FIXTURE_STATE
        os.environ["FAKE_AGY_LOG"] = self.log
        os.environ["FAKE_AGY_MODE"] = "ok"
        xs._AGY_LOGIN_CACHE.clear()
        if os.path.exists(xs.BREAKERS):
            os.remove(xs.BREAKERS)

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        xs._AGY_LOGIN_CACHE.clear()
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

    def calls(self):
        with open(self.log) as f:
            return [json.loads(line) for line in f if line.strip()]

    def test_not_auto_selected_and_forced_model(self):
        for kind, diff in (("impl", 3), ("review", 3), ("mechanical", 1), ("docs", 2)):
            self.assertNotEqual(self.route({"type": kind, "difficulty": diff}).get("backend"), "agy")
        forced = self.route({"type": "impl", "difficulty": 3, "backend": "agy"})
        self.assertTrue(forced["ok"])
        self.assertEqual(forced["backend"], "agy")
        self.assertEqual(forced["model"], "gemini-3.8-flash-medium")
        named = self.route({"type": "impl", "difficulty": 2, "backend": "agy", "model": "claude-sonnet-4-6"})
        self.assertEqual(named["model"], "claude-sonnet-4-6")
        opted = json.loads(json.dumps(POLICY))
        opted["routing"]["default_order"] = ["agy", "codex", "ocgo", "deepseek"]
        self.assertEqual(self.route({"type": "impl", "difficulty": 3}, opted)["backend"], "agy")

    def test_gemini_is_an_alias_of_agy(self):
        aliased = self.route({"type": "impl", "difficulty": 3, "backend": "gemini"})
        self.assertTrue(aliased["ok"])
        self.assertEqual(aliased["backend"], "agy")
        self.assertEqual(aliased["model"], "gemini-3.8-flash-medium")
        self.assertEqual(xs.canonical_backend("gemini"), "agy")
        self.assertEqual(xs.canonical_backend("codex"), "codex")

    def test_binary_discovery_order(self):
        self.assertEqual(xs.agy_bin(), self.script)
        os.environ["OPENSWARM_AGY_BIN"] = os.path.join(self.tmp, "missing")
        self.assertIsNone(xs.agy_bin())
        os.environ.pop("OPENSWARM_AGY_BIN")
        bindir = os.path.join(self.tmp, "pathbin")
        os.makedirs(bindir)
        path_bin = os.path.join(bindir, "agy")
        os.symlink(self.script, path_bin)
        old = os.environ.get("PATH", "")
        os.environ["PATH"] = bindir + os.pathsep + old
        try:
            self.assertEqual(xs.agy_bin(), path_bin)
        finally:
            os.environ["PATH"] = old

    def test_success_parses_result_and_records_usage(self):
        code = self.dispatch()
        self.assertEqual(code, 0)
        entry, result = self.last_result()
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["backend"], "agy")
        self.assertEqual(result["model"], "gemini-3.8-flash-medium")
        usage = {"input_tokens": 120, "output_tokens": 45, "thinking_tokens": 3,
                 "cache_read_tokens": 8, "total_tokens": 176}
        self.assertEqual(result["usage"], usage)
        self.assertEqual(result["result"]["summary"], "added add")
        self.assertEqual(result["result"]["files_changed"], ["add.py"])
        self.assertEqual(entry["usage"], usage)
        self.assertEqual(entry["backend"], "agy")
        argv = self.calls()[0]
        self.assertEqual(argv[argv.index("--model") + 1], "gemini-3.8-flash-medium")
        self.assertEqual(argv[argv.index("--output-format") + 1], "stream-json")
        self.assertIn("-p", argv)
        self.assertIn("--dangerously-skip-permissions", argv)

    def test_events_are_written_to_the_run_event_log(self):
        self.dispatch()
        _entry, result = self.last_result()
        path = os.path.join(result["run_dir"], "events.1.agy.jsonl")
        with open(path) as fh:
            events = [json.loads(line) for line in fh]
        kinds = [event.get("type") for event in events]
        self.assertIn("init", kinds)
        self.assertIn("step_update", kinds)
        self.assertIn("result", kinds)
        self.assertEqual(xs.summarize_event(events[1]), ("msg", "writing the report"))
        self.assertEqual(xs.summarize_event(events[2])[0], "end")

    def test_auth_error_is_its_own_error_class(self):
        os.environ["FAKE_AGY_MODE"] = "auth"
        code = self.dispatch()
        self.assertEqual(code, 1)
        entry, result = self.last_result()
        self.assertEqual(result["attempts"][0]["error_class"], "auth")
        self.assertEqual(entry["error_class"], "auth")
        self.assertNotIn("429", result["attempts"][0].get("error_tail") or "")

    def test_early_timeout_cannot_report_success_from_partial_json(self):
        os.environ["FAKE_AGY_MODE"] = "print-timeout"
        self.assertEqual(self.dispatch(), 1)
        _entry, result = self.last_result()
        self.assertNotEqual(result["status"], "done")
        self.assertIn("print_timeout", result["attempts"][0]["error_tail"])

    def test_long_quota_reset_does_not_retry_past_budget(self):
        os.environ["FAKE_AGY_MODE"] = "long-quota"
        self.assertEqual(self.dispatch(), 1)
        _entry, result = self.last_result()
        self.assertEqual(len(result["attempts"]), 1)
        self.assertEqual(result["attempts"][0]["error_class"], "rate_limit")
        self.assertEqual(xs.agy_quota_reset_seconds("agy", "Resets in 131h29m32s."), 473372)
        self.assertIsNone(xs.agy_quota_reset_seconds("codex", "Resets in 131h29m32s."))
        self.assertIsNone(xs.agy_quota_reset_seconds("agy", "quota exceeded"))

    def test_rate_limit_is_classified_and_trips_a_breaker(self):
        os.environ["FAKE_AGY_MODE"] = "rate"
        policy = xs.deep_merge(POLICY, {"rate_limit": {"max_retries": 0}})
        code = self.dispatch(policy=policy)
        self.assertEqual(code, 1)
        entry, result = self.last_result()
        self.assertEqual(len(result["attempts"]), 1)
        self.assertEqual(result["attempts"][0]["error_class"], "rate_limit")
        self.assertEqual(entry["error_class"], "rate_limit")
        self.assertEqual(xs.rate_limit_breaker_key("agy", {"model": "gemini-3.8-flash-medium"}, "429"),
                         "agy/gemini-3.8-flash-medium")
        self.assertIn("agy/gemini-3.8-flash-medium", xs.breakers())

    def test_rate_limit_retries_in_the_same_conversation(self):
        os.environ["FAKE_AGY_MODE"] = "rate-once"
        policy = xs.deep_merge(POLICY, {"rate_limit": {"backoff_seconds": [0], "max_retries": 1}})
        self.assertEqual(self.dispatch(policy=policy), 0)
        _entry, result = self.last_result()
        self.assertEqual(result["status"], "done")
        self.assertEqual([attempt["error_class"] for attempt in result["attempts"]], ["rate_limit", None])
        calls = self.calls()
        self.assertEqual(len(calls), 2)
        self.assertNotIn("--conversation", calls[0])
        self.assertEqual(calls[1][calls[1].index("--conversation") + 1], "conv-rate")

    def test_usage_tolerates_missing_fields(self):
        self.assertEqual(xs.agy_usage(None), {k: 0 for k in xs.AGY_USAGE_KEYS})
        self.assertEqual(xs.agy_usage({}), {k: 0 for k in xs.AGY_USAGE_KEYS})
        partial = xs.agy_usage({"input_tokens": 7})
        self.assertEqual(partial["input_tokens"], 7)
        self.assertEqual(partial["output_tokens"], 0)
        self.assertEqual(partial["total_tokens"], 0)
        broken = xs.agy_usage({"input_tokens": "many", "output_tokens": None})
        self.assertEqual(broken["input_tokens"], 0)
        self.assertEqual(broken["output_tokens"], 0)

    def test_budget_kills_the_process(self):
        os.environ["FAKE_AGY_MODE"] = "sleep"
        rdir = os.path.join(self.tmp, "run")
        os.makedirs(rdir)
        evp = os.path.join(rdir, "events.jsonl")
        rc, final, meta, _err = xs.exec_agy(
            {"backend": "agy", "model": "gemini-3.8-flash-medium"}, {}, "sleep", self.cwd, rdir, 1, evp)
        self.assertEqual(rc, 124)
        self.assertIsNone(final)
        self.assertEqual(meta["usage"], {k: 0 for k in xs.AGY_USAGE_KEYS})

    def test_login_state_never_shows_credentials(self):
        self.assertEqual(xs.agy_login_state(), "logged in")
        os.environ["FAKE_AGY_MODE"] = "auth"
        xs._AGY_LOGIN_CACHE.clear()
        self.assertEqual(xs.agy_login_state(), "not logged in")
        os.environ["OPENSWARM_AGY_BIN"] = os.path.join(self.tmp, "missing")
        xs._AGY_LOGIN_CACHE.clear()
        self.assertEqual(xs.agy_login_state(), "unknown")

    def test_fleet_status_works_with_and_without_agy(self):
        cli = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin", "openswarm")
        env = dict(os.environ, OPENSWARM_HOME=os.path.join(self.tmp, "fleet-home"))
        with_agy = subprocess.run([sys.executable, cli, "fleet", "status"], env=env, text=True,
                                  capture_output=True, timeout=60)
        self.assertEqual(with_agy.returncode, 0, with_agy.stderr)
        self.assertIn("agy bin=", with_agy.stdout)
        self.assertIn("model=gemini-3.8-flash-medium", with_agy.stdout)
        self.assertIn("login=logged in", with_agy.stdout)
        env["OPENSWARM_AGY_BIN"] = os.path.join(self.tmp, "missing")
        without_agy = subprocess.run([sys.executable, cli, "fleet", "status"], env=env, text=True,
                                     capture_output=True, timeout=60)
        self.assertEqual(without_agy.returncode, 0, without_agy.stderr)
        self.assertIn("agy bin=-", without_agy.stdout)
        self.assertNotIn("login=logged in", without_agy.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
