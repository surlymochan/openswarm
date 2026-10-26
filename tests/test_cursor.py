"""Cursor executor: fake cursor-agent, no network and no real CLI.

Covers stream-json parsing, usage on result.json and the ledger, budget kill, and rate-limit classification.
"""
import argparse, contextlib, io, json, os, stat, sys, tempfile, textwrap, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _load  # noqa: E402

xs = _load.load()
POLICY = xs.load_policy()
FAKE = r"""#!/usr/bin/env python3
import json, os, sys, time
mode = os.environ.get("FAKE_CURSOR_MODE", "ok")
log = os.environ.get("FAKE_CURSOR_LOG")
if log:
    with open(log, "w") as fh:
        json.dump(sys.argv, fh)
if mode == "sleep":
    time.sleep(30)
    sys.exit(0)
if mode == "rate" or (mode == "rate-once" and not os.path.exists(log + ".retry")):
    if mode == "rate-once":
        open(log + ".retry", "w").close()
    print(json.dumps({
        "type": "result", "subtype": "error", "is_error": True,
        "result": "429 rate limit exceeded",
        "usage": {"inputTokens": 1, "outputTokens": 0, "cacheReadTokens": 0, "cacheWriteTokens": 0},
    }))
    sys.stderr.write("rate limit: too many requests\n")
    sys.exit(1)
body = {"status": "done", "summary": "added add", "files_changed": ["add.py"],
        "tests": "not run: host verify", "followups": []}
print(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                  "result": json.dumps(body),
                  "usage": {"inputTokens": 120, "outputTokens": 45,
                            "cacheReadTokens": 8, "cacheWriteTokens": 2}}))
"""


def ns(**kw):
    base = dict(type="impl", difficulty=3, context_tokens=0, backend="cursor", model=None, effort=None,
                prompt="add a function add(a, b)", task_file=None, title="cursor test", cwd=".",
                no_worktree=True, verify=None, verify_baseline=False, keep_worktree=False, no_fallback=False,
                budget_minutes=5, detach=False, run_id=None, dry_run=False)
    base.update(kw)
    return argparse.Namespace(**base)


class CursorBackend(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="openswarm-cursor-")
        self.script = os.path.join(self.tmp, "cursor-agent")
        with open(self.script, "w") as f:
            f.write(textwrap.dedent(FAKE))
        os.chmod(self.script, os.stat(self.script).st_mode | stat.S_IEXEC)
        self.log = os.path.join(self.tmp, "argv.json")
        self.cwd = os.path.join(self.tmp, "work")
        os.makedirs(self.cwd)
        self._env = {k: os.environ.get(k) for k in ("OPENSWARM_CURSOR_BIN", "OPENSWARM_STATE_FILE", "FAKE_CURSOR_MODE", "FAKE_CURSOR_LOG")}
        os.environ["OPENSWARM_CURSOR_BIN"] = self.script
        os.environ["OPENSWARM_STATE_FILE"] = _load.FIXTURE_STATE
        os.environ["FAKE_CURSOR_LOG"] = self.log
        os.environ["FAKE_CURSOR_MODE"] = "ok"
        if os.path.exists(xs.BREAKERS):
            os.remove(xs.BREAKERS)

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        # leftover trees are under the test home or this temp dir; drop the temp dir only
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def route(self, prof, policy=None):
        with open(_load.FIXTURE_STATE) as f:
            st = json.load(f)
        return xs.route(prof, st, policy or POLICY)

    def dispatch(self, policy=POLICY, **kw):
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                xs.cmd_run(ns(cwd=self.cwd, **kw), policy)
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
            got = self.route({"type": kind, "difficulty": diff})
            self.assertNotEqual(got.get("backend"), "cursor", got)
        forced = self.route({"type": "impl", "difficulty": 3, "backend": "cursor"})
        self.assertTrue(forced["ok"])
        self.assertEqual(forced["backend"], "cursor")
        self.assertEqual(forced["model"], "grok-4.7-high")
        named = self.route({"type": "impl", "difficulty": 2, "backend": "cursor", "model": "grok-4.7-low"})
        self.assertEqual(named["model"], "grok-4.7-low")
        opted = json.loads(json.dumps(POLICY))
        opted["routing"]["default_order"] = ["cursor", "codex", "ocgo", "deepseek"]
        self.assertEqual(self.route({"type": "impl", "difficulty": 3}, opted)["backend"], "cursor")

    def test_binary_discovery_order(self):
        self.assertEqual(xs.cursor_bin(), self.script)
        os.environ["OPENSWARM_CURSOR_BIN"] = os.path.join(self.tmp, "missing")
        self.assertIsNone(xs.cursor_bin())
        os.environ.pop("OPENSWARM_CURSOR_BIN")
        bindir = os.path.join(self.tmp, "pathbin")
        os.makedirs(bindir)
        path_bin = os.path.join(bindir, "cursor-agent")
        os.symlink(self.script, path_bin)
        old = os.environ.get("PATH", "")
        os.environ["PATH"] = bindir + os.pathsep + old
        try:
            self.assertEqual(xs.cursor_bin(), path_bin)
            os.environ["PATH"] = old
            home_bin = os.path.join(self.tmp, "home", "cursor-agent")
            os.makedirs(os.path.dirname(home_bin))
            os.symlink(self.script, home_bin)
            real = os.path.expanduser
            os.path.expanduser = lambda p: home_bin if p == "~/.local/bin/cursor-agent" else real(p)
            try:
                # PATH no longer has cursor-agent first; which() may still see the real one.
                # Hide it by pointing PATH at an empty dir, then the home fallback must win.
                os.environ["PATH"] = os.path.join(self.tmp, "empty-path")
                os.makedirs(os.environ["PATH"], exist_ok=True)
                self.assertEqual(xs.cursor_bin(), home_bin)
            finally:
                os.path.expanduser = real
        finally:
            os.environ["PATH"] = old

    def test_success_parses_result_and_records_usage(self):
        code = self.dispatch()
        self.assertEqual(code, 0)
        entry, result = self.last_result()
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["backend"], "cursor")
        self.assertEqual(result["model"], "grok-4.7-high")
        usage = {"inputTokens": 120, "outputTokens": 45, "cacheReadTokens": 8, "cacheWriteTokens": 2}
        self.assertEqual(result["usage"], usage)
        self.assertEqual(result["result"]["summary"], "added add")
        self.assertEqual(result["result"]["files_changed"], ["add.py"])
        self.assertGreater(result["seconds"], 0)
        self.assertEqual(entry["usage"], usage)
        self.assertEqual(entry["seconds"], result["seconds"])
        self.assertEqual(entry["backend"], "cursor")
        with open(self.log) as fh:
            argv = json.load(fh)
        self.assertEqual(argv[argv.index("--model") + 1], "grok-4.7-high")
        self.assertEqual(argv[argv.index("--output-format") + 1], "stream-json")
        for flag in ("-p", "--trust", "--force", "--"):
            self.assertIn(flag, argv)

    def test_rate_limit_is_classified_without_retries(self):
        os.environ["FAKE_CURSOR_MODE"] = "rate"
        policy = xs.deep_merge(POLICY, {"rate_limit": {"max_retries": 0}})
        code = self.dispatch(policy=policy)
        self.assertEqual(code, 1)
        entry, result = self.last_result()
        self.assertEqual(len(result["attempts"]), 1)
        self.assertEqual(result["attempts"][0]["error_class"], "rate_limit")
        self.assertEqual(entry["error_class"], "rate_limit")
        self.assertEqual(xs.rate_limit_breaker_key("cursor", {"model": "grok-4.7-high"}, "429"),
                         "cursor/grok-4.7-high")
        self.assertIn("cursor/grok-4.7-high", xs.breakers())

    def test_rate_limit_retries_with_cursor_continue(self):
        os.environ["FAKE_CURSOR_MODE"] = "rate-once"
        policy = xs.deep_merge(POLICY, {"rate_limit": {"backoff_seconds": [0], "max_retries": 1}})
        self.assertEqual(self.dispatch(policy=policy), 0)
        _entry, result = self.last_result()
        self.assertEqual(result["status"], "done")
        self.assertEqual([attempt["error_class"] for attempt in result["attempts"]], ["rate_limit", None])
        event_path = os.path.join(xs.RUNS, result["id"], "events.1.cursor.jsonl")
        with open(event_path) as fh:
            event_rows = [json.loads(line) for line in fh]
        self.assertEqual(sum(row.get("type") == "RATELIMIT" for row in event_rows), 1)
        with open(self.log) as fh:
            argv = json.load(fh)
        self.assertEqual(argv.count("--continue"), 1)

    def test_budget_kills_the_process(self):
        os.environ["FAKE_CURSOR_MODE"] = "sleep"
        rdir = os.path.join(self.tmp, "run")
        os.makedirs(rdir)
        evp = os.path.join(rdir, "events.jsonl")
        rc, final, meta, _err = xs.exec_cursor(
            {"backend": "cursor", "model": "grok-4.7-high"}, {}, "sleep", self.cwd, rdir, 1, evp)
        self.assertEqual(rc, 124)
        self.assertIsNone(final)
        self.assertEqual(meta["usage"], {"inputTokens": 0, "outputTokens": 0, "cacheReadTokens": 0, "cacheWriteTokens": 0})


if __name__ == "__main__":
    unittest.main(verbosity=2)
