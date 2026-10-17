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
    with open(log, "w") as fh:
        json.dump(sys.argv, fh)
if mode == "sleep":
    time.sleep(30)
    sys.exit(0)
def end(text, usage, **extra):
    msg = {"role": "assistant", "content": [{"type": "text", "text": text}], "usage": usage}
    msg.update(extra)
    print(json.dumps({"type": "message_end", "message": msg}))
print(json.dumps({"type": "session", "id": "s1"}))
print(json.dumps({"type": "message_end", "message": {"role": "user", "content": [], "usage": {"input": 999}}}))
if mode == "rate":
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

    def dispatch(self, **kw):
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                xs.cmd_run(ns(cwd=self.cwd, **kw), POLICY)
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
        code = self.dispatch()
        self.assertEqual(code, 0)
        entry, result = self.last_result()
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["backend"], "pi")
        usage = {"input": 120, "output": 45, "cacheRead": 8, "cacheWrite": 2}
        self.assertEqual(result["usage"], usage)
        self.assertEqual(result["result"]["files_changed"], ["add.py"])
        self.assertEqual(entry["usage"], usage)
        with open(self.log) as fh:
            argv = json.load(fh)
        self.assertEqual(argv[argv.index("--mode") + 1], "json")
        self.assertEqual(argv[argv.index("--tools") + 1], "read,bash,edit,write,grep,find,ls")
        self.assertIn("-p", argv)
        self.assertNotIn("--model", argv)

    def test_error_event_fails_the_attempt(self):
        os.environ["FAKE_PI_MODE"] = "rate"
        code = self.dispatch()
        self.assertEqual(code, 1)
        entry, result = self.last_result()
        self.assertEqual(result["attempts"][0]["error_class"], "rate_limit")
        self.assertEqual(xs.rate_limit_breaker_key("pi", {"model": None}, "429"), "pi/all")
        self.assertIn("pi/all", xs.breakers())

    def test_budget_kills_the_process(self):
        os.environ["FAKE_PI_MODE"] = "sleep"
        rdir = os.path.join(self.tmp, "run")
        os.makedirs(rdir)
        evp = os.path.join(rdir, "events.jsonl")
        rc, final, meta, _err = xs.exec_pi({"backend": "pi", "model": None}, {}, "sleep", self.cwd, rdir, 1, evp)
        self.assertEqual(rc, 124)
        self.assertIsNone(final)
        self.assertEqual(meta["usage"], {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0})


if __name__ == "__main__":
    unittest.main(verbosity=2)
