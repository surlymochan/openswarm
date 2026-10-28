"""Regression tests for worktrees, dependency links, host-side verification, risk rules and credential opt-ins."""
import base64, json, os, shlex, shutil, subprocess, sys, tempfile, threading, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _load  # noqa: E402

xs = _load.load()


def sh(*a, cwd):
    subprocess.run(a, cwd=cwd, check=True, capture_output=True)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


class WorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(os.path.join(self.repo, "pkg", "node_modules", "@x", "sdk"))
        write(os.path.join(self.repo, "pkg", "node_modules", "@x", "sdk", "index.js"), "1")
        write(os.path.join(self.repo, ".gitignore"), "node_modules/\n")
        write(os.path.join(self.repo, "pkg", "a.txt"), "a")
        sh("git", "init", "-q", cwd=self.repo)
        sh("git", "add", "-A", cwd=self.repo)
        sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init", cwd=self.repo)
        xs.WORKTREES = os.path.join(self.tmp, "wt")
        self.rdir = os.path.join(self.tmp, "run")
        os.makedirs(self.rdir)

    def test_dependency_dirs_linked_and_never_committed(self):
        ws = xs.prepare_workspace(self.repo, "r1", True)
        self.assertEqual(ws["mode"], "worktree")
        self.assertEqual(ws["linked"], [os.path.join("pkg", "node_modules")])
        self.assertTrue(os.path.exists(os.path.join(ws["root"], "pkg", "node_modules", "@x", "sdk", "index.js")))
        write(os.path.join(ws["root"], "pkg", "b.txt"), "b")
        fin = xs.finalize_workspace(ws, "t", "codex", self.rdir, keep=False)
        files = subprocess.run(["git", "show", "--name-only", "--format=", fin["commit"]], cwd=self.repo,
                               capture_output=True, text=True).stdout.split()
        self.assertEqual(files, ["pkg/b.txt"])

    def test_executor_directories_and_logs_are_never_committed(self):
        ws = xs.prepare_workspace(self.repo, "artifacts", True)
        excluded = [".pi/session.jsonl", ".codex/session.jsonl", "pkg/.cursor/log.jsonl", "codex-run.log"]
        for path in excluded:
            write(os.path.join(ws["root"], path), "private executor data")
        write(os.path.join(ws["root"], "pkg", "real.txt"), "real change")
        sh("git", "add", "-A", cwd=ws["root"])
        fin = xs.finalize_workspace(ws, "artifacts", "codex", self.rdir, keep=False)
        self.assertEqual(fin["excluded_files"], sorted(excluded))
        files = subprocess.check_output(["git", "show", "--name-only", "--format=", fin["commit"]],
                                        cwd=self.repo, text=True).split()
        self.assertEqual(files, ["pkg/real.txt"])

    def test_workspace_prepare_runs_in_worktree_and_replaces_dependency_link(self):
        home = os.path.join(self.tmp, "home")
        write(os.path.join(home, "policy.local.json"), json.dumps({"workspace": {
            "prepare": {"repo": "touch prepared.marker"}, "prepare_replaces_links": ["node_modules"]}}))
        old_data, xs.DATA = xs.DATA, home
        self.addCleanup(setattr, xs, "DATA", old_data)
        os.makedirs(os.path.join(self.repo, "node_modules", "leftpad"))
        write(os.path.join(self.repo, "node_modules", "leftpad", "index.js"), "base")
        ws = xs.prepare_workspace(self.repo, "prepare", True)
        self.assertNotIn("node_modules", ws["linked"])
        prepared = xs.run_workspace_prepare(ws["repo"], ws["root"], "before_executor")
        self.assertEqual(prepared["rc"], 0)
        self.assertGreaterEqual(prepared["seconds"], 0)
        self.assertTrue(os.path.exists(os.path.join(ws["root"], "prepared.marker")))
        self.assertFalse(os.path.exists(os.path.join(ws["root"], "node_modules", "leftpad", "index.js")))
        xs.cleanup_worktree(ws, None)

    def test_concurrent_repo_prepares_are_serialized(self):
        home = os.path.join(self.tmp, "home")
        active = os.path.join(self.tmp, "prepare-active")
        script = ("import pathlib,sys,time; p=pathlib.Path(sys.argv[1]); "
                  "p.mkdir(); time.sleep(.15); p.rmdir()")
        command = f"python3 -c {shlex.quote(script)} {shlex.quote(active)}"
        write(os.path.join(home, "policy.local.json"), json.dumps({"workspace": {
            "prepare": {os.path.realpath(self.repo): command}}}))
        old_data, xs.DATA = xs.DATA, home
        self.addCleanup(setattr, xs, "DATA", old_data)
        workdirs = [os.path.join(self.tmp, "prepare-a"), os.path.join(self.tmp, "prepare-b")]
        for path in workdirs:
            os.makedirs(path)
        barrier = threading.Barrier(3)
        results = []

        def prepare(path):
            barrier.wait()
            results.append(xs.run_workspace_prepare(self.repo, path, "before_executor"))

        threads = [threading.Thread(target=prepare, args=(path,)) for path in workdirs]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=5)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(len(results), 2)
        self.assertTrue(all(result["rc"] == 0 for result in results), results)
        self.assertTrue(all(result["attempts"] == [0] for result in results), results)

    def test_workspace_prepare_retries_once_after_failure(self):
        home = os.path.join(self.tmp, "retry-home")
        marker = os.path.join(self.tmp, "prepare-first-attempt")
        command = f"if [ ! -f {shlex.quote(marker)} ]; then touch {shlex.quote(marker)}; exit 1; fi; exit 0"
        write(os.path.join(home, "policy.local.json"), json.dumps({"workspace": {
            "prepare": {os.path.realpath(self.repo): command}}}))
        old_data, xs.DATA = xs.DATA, home
        self.addCleanup(setattr, xs, "DATA", old_data)
        result = xs.run_workspace_prepare(self.repo, self.repo, "before_executor")
        self.assertEqual(result["rc"], 0)
        self.assertEqual(result["attempts"], [1, 0])

    def test_bun_workspace_without_prepare_gets_warning_only(self):
        write(os.path.join(self.repo, "package.json"), json.dumps({"workspaces": ["packages/*"]}))
        write(os.path.join(self.repo, "bun.lock"), "lock")
        sh("git", "add", "package.json", "bun.lock", cwd=self.repo)
        sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "bun", cwd=self.repo)
        ws = xs.prepare_workspace(self.repo, "bun", True)
        self.assertIn("Bun workspace detected", ws["workspace_note"])
        self.assertIn(os.path.join("pkg", "node_modules"), ws["linked"])
        xs.cleanup_worktree(ws, None)

    def test_lockfile_change_requests_second_prepare(self):
        ws = xs.prepare_workspace(self.repo, "lock", True)
        write(os.path.join(ws["root"], "bun.lock"), "lock")
        fin = xs.finalize_workspace(ws, "t", "codex", self.rdir, keep=True)
        self.assertTrue(xs.lockfiles_changed(ws))
        xs.cleanup_worktree(ws, fin["commit"])

    def test_remote_verify_runs_workspace_prepare(self):
        home = os.path.join(self.tmp, "home")
        write(os.path.join(home, "policy.local.json"), json.dumps({"workspace": {
            "prepare": {os.path.realpath(self.repo): "touch prepared.marker"},
            "prepare_replaces_links": ["node_modules"]}}))
        old_data, xs.DATA = xs.DATA, home
        self.addCleanup(setattr, xs, "DATA", old_data)
        rev = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.repo, text=True).strip()
        result = xs.verify_in_worktree(self.repo, rev, "test -f prepared.marker", 30)
        self.assertEqual(result["rc"], 0)
        self.assertEqual(len(result["prepare"]), 1)
        self.assertEqual(result["prepare"][0]["phase"], "before_verify")
        self.assertEqual(result["prepare"][0]["rc"], 0)
        self.assertGreaterEqual(result["prepare"][0]["seconds"], 0)

    def test_tracked_relative_link_to_sibling_repo_resolves(self):
        sibling = os.path.join(self.tmp, "sdkrepo", "sdk")
        os.makedirs(sibling)
        write(os.path.join(sibling, "index.js"), "sdk")
        os.symlink("../../sdkrepo/sdk", os.path.join(self.repo, "pkg", "sdk-link"))
        sh("git", "add", "pkg/sdk-link", cwd=self.repo)
        sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "link", cwd=self.repo)
        ws = xs.prepare_workspace(self.repo, "r2", True)
        self.assertEqual(os.path.realpath(os.path.dirname(ws["root"])), os.path.realpath(self.tmp))
        self.assertTrue(os.path.exists(os.path.join(ws["root"], "pkg", "sdk-link", "index.js")))
        xs.finalize_workspace(ws, "t", "codex", self.rdir, keep=False)

    def test_verify_sees_committed_clean_tree(self):
        # Release builders refuse dirty sources; verify must run after the executor's edit is committed.
        ws = xs.prepare_workspace(self.repo, "r3", True)
        write(os.path.join(ws["root"], "pkg", "c.txt"), "c")
        fin = xs.finalize_workspace(ws, "t", "codex", self.rdir, keep=True)
        v = xs.run_verify("test -z \"$(git status --porcelain)\" && test -f pkg/c.txt", ws["root"], 30)
        self.assertEqual(v["rc"], 0, v["tail"])
        xs.cleanup_worktree(ws, fin["commit"])
        self.assertFalse(os.path.exists(ws["root"]))

    def test_explicit_base_is_used_without_changing_checkout(self):
        def current_head():
            return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.repo, text=True).strip()
        base = current_head()
        write(os.path.join(self.repo, "later.txt"), "later")
        sh("git", "add", "later.txt", cwd=self.repo)
        sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "later", cwd=self.repo)
        head = current_head()
        ws = xs.prepare_workspace(self.repo, "explicit-base", True, base)
        self.assertEqual(ws["base"], base)
        self.assertFalse(os.path.exists(os.path.join(ws["root"], "later.txt")))
        self.assertEqual(current_head(), head)
        xs.cleanup_worktree(ws, None)

    def test_watch_accepts_several_runs(self):
        home = os.path.join(self.tmp, "home")
        runs = os.path.join(home, "runs")
        for rid in ("a1", "b2"):
            os.makedirs(os.path.join(runs, rid))
            xs.jsave(os.path.join(runs, rid, "status.json"), {"state": "finished", "status": "done", "title": rid})
        out = subprocess.run([sys.executable, os.path.join(HERE, "..", "bin", "openswarm"), "watch", "a1", "b2",
                              "--max-minutes", "0.2"], capture_output=True, text=True, timeout=60,
                             env={**os.environ, "OPENSWARM_HOME": home})
        self.assertNotIn("error: argument", out.stderr)
        self.assertEqual(out.stdout.count("DONE: "), 2, out.stdout + out.stderr)

    def test_partially_tracked_node_modules_gets_missing_packages(self):
        nm = os.path.join(self.repo, "node_modules")
        os.makedirs(os.path.join(nm, "@x", "tracked"))
        os.makedirs(os.path.join(nm, "@x", "untracked"))
        os.makedirs(os.path.join(nm, "leftpad"))
        write(os.path.join(nm, "@x", "tracked", "a.js"), "t")
        write(os.path.join(nm, "leftpad", "index.js"), "l")
        sh("git", "add", "-f", "node_modules/@x/tracked/a.js", cwd=self.repo)
        sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "partial", cwd=self.repo)
        ws = xs.prepare_workspace(self.repo, "r4", True)
        self.assertTrue(os.path.exists(os.path.join(ws["root"], "node_modules", "leftpad", "index.js")))
        self.assertTrue(os.path.isdir(os.path.join(ws["root"], "node_modules", "@x", "untracked")))
        status = subprocess.run(["git", "status", "--porcelain"], cwd=ws["root"], capture_output=True, text=True).stdout
        self.assertEqual(status.strip(), "")
        xs.cleanup_worktree(ws, None)

    def test_verify_paths_rewritten_into_worktree(self):
        cmd = "node --test /repo/x/test/a.test.mjs /other/b.mjs"
        self.assertEqual(xs.rewrite_verify_paths(cmd, "/repo/x", "/wt/x-1"),
                         "node --test /wt/x-1/test/a.test.mjs /other/b.mjs")
        self.assertEqual(xs.rewrite_verify_paths("pytest -q tests", "/repo/x", "/wt/x-1"), "pytest -q tests")

    def test_keychain_risk_only_on_real_credential_reads(self):
        rule = next(rx for rx, name in xs.DEFAULT_RISKS if name == "keychain access")
        import re
        rx = re.compile(rule, re.I)
        self.assertIsNone(rx.search("rg -n 'keychain|security' src/profile-backup-runtime.mjs"))
        self.assertIsNotNone(rx.search("security find-generic-password -s x -w"))
        self.assertIsNotNone(rx.search("security dump-keychain"))

    def test_failing_tests_parsed_from_node_and_pytest(self):
        node = "ok\n✖ failing tests:\n\ntest at a.mjs:1:1\n✖ alpha works (3.2ms)\n✖ beta works (1ms)\n"
        self.assertEqual(xs.failing_tests(node), {"alpha works", "beta works"})
        self.assertEqual(xs.failing_tests("FAILED tests/t.py::test_x - AssertionError\n"), {"tests/t.py::test_x"})

    def test_baseline_verify_separates_new_from_known_failures(self):
        check = os.path.join(self.repo, "check.py")
        write(check, "import os,sys\nprint('FAILED t::known')\n"
                     "os.path.exists('pkg/new_bug') and print('FAILED t::new')\nsys.exit(1)\n")
        sh("git", "add", "-A", cwd=self.repo)
        sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "check", cwd=self.repo)
        for bug, expect_new in ((False, []), (True, ["t::new"])):
            ws = xs.prepare_workspace(self.repo, f"b{int(bug)}", True)
            write(os.path.join(ws["root"], "pkg", "new_bug" if bug else "ok.txt"), "x")
            fin = xs.finalize_workspace(ws, "t", "codex", self.rdir, keep=True)
            v = xs.run_verify("python3 check.py", ws["root"], 30)
            base = xs.verify_against_baseline("python3 check.py", ws, 30)
            new = sorted(set(v["failing"]) - xs.failing_tests(base["output"]))
            self.assertEqual(new, expect_new)
            xs.cleanup_worktree(ws, fin["commit"])

    def test_secret_print_rule_targets_credentials_only(self):
        import re
        rx = re.compile(next(r for r, n in xs.DEFAULT_RISKS if n == "possible secret print"), re.I)
        for cmd in ["cat services/api/test/backup-runtime-recovery.test.mjs | head",
                    "cat > scripts/tools/channel.py <<'PY'", "rg -n 'token' src/session.mjs",
                    "cat scripts/acp-mcp-shared-token-e2e.ts", "head -40 src/main/secret-redaction.tsx",
                    "tail docs/password-store.md"]:
            self.assertIsNone(rx.search(cmd), cmd)
        for cmd in ["cat ~/.codex/auth.json", "cat /var/lib/myapp/api.token", "printenv OPENAI_API_KEY",
                    "echo $GITHUB_TOKEN", "head -c 20 /etc/myapp/secret.env",
                    "cat ~/.config/github-token.txt", "cat deploy/api-key.json"]:
            self.assertIsNotNone(rx.search(cmd), cmd)

    def test_verify_overrides_executor_status(self):
        ok = xs.run_verify("true", self.repo, 30)
        bad = xs.run_verify("exit 3", self.repo, 30)
        self.assertEqual(xs.adjust_status_for_verify("partial", ok)[0], "done")
        self.assertEqual(xs.adjust_status_for_verify("done", bad)[0], "partial")
        self.assertEqual(xs.adjust_status_for_verify("failed", ok)[0], "failed")
        self.assertEqual(xs.adjust_status_for_verify("done", None), ("done", None))

    def test_change_evidence_downgrades_false_done(self):
        self.assertEqual(xs.adjust_status_for_change_evidence(
            "done", None, commit=None, workspace_changed=False, changed=False, read_only=False, verify=None),
            ("partial", "no_change_evidence"))
        self.assertEqual(xs.adjust_status_for_change_evidence(
            "done", None, commit=None, workspace_changed=False, changed=False, read_only=True, verify=None),
            ("done", None))
        verify = {"rc": 0, "output": "ok", "failing": []}
        self.assertEqual(xs.adjust_status_for_change_evidence(
            "done", None, commit=None, workspace_changed=False, changed=False, read_only=False, verify=verify),
            ("done", None))


class CredentialOptInTest(unittest.TestCase):
    """Reading other tools' auth/account files must be explicit opt-in."""

    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.home, True)
        claims = base64.urlsafe_b64encode(json.dumps(
            {"https://api.openai.com/auth": {"chatgpt_plan_type": "pro"}}).encode()).decode().rstrip("=")
        write(os.path.join(self.home, ".codex", "auth.json"), json.dumps({"tokens": {"id_token": f"h.{claims}.s"}}))
        write(os.path.join(self.home, ".claude.json"), json.dumps({"oauthAccount": {"organizationType": "claude_max",
                                                                                    "userRateLimitTier": "max_20x"}}))
        self._home, xs.HOME = xs.HOME, self.home
        self.addCleanup(setattr, xs, "HOME", self._home)
        for k in ("OPENSWARM_CODEX_PLAN", "OPENSWARM_CLAUDE_PLAN", "XSWARM_CODEX_PLAN", "XSWARM_CLAUDE_PLAN"):
            os.environ.pop(k, None)
        self.policy = xs.jload(f"{xs.SKILL_DIR}/policy.json", {})

    def test_defaults_do_not_read_auth_files(self):
        self.assertFalse(self.policy["local_sources"]["codex_auth_plan"])
        self.assertFalse(self.policy["local_sources"]["claude_account_plan"])
        self.assertEqual(self.policy["deepseek"]["keychain_items"], [])
        self.assertEqual(xs.codex_plan(self.policy), "unknown")
        self.assertEqual(xs.probe_claude(self.policy)["plan"], "unknown")

    def test_opt_in_reads_only_plan_claims(self):
        pol = xs.deep_merge(self.policy, {"local_sources": {"codex_auth_plan": True, "claude_account_plan": True}})
        self.assertEqual(xs.codex_plan(pol), "pro")
        self.assertEqual(xs.probe_claude(pol)["plan"], "max-20x")

    def test_explicit_plan_wins_without_reading_files(self):
        os.environ["OPENSWARM_CODEX_PLAN"] = "team"
        os.environ["OPENSWARM_CLAUDE_PLAN"] = "pro"
        self.addCleanup(os.environ.pop, "OPENSWARM_CODEX_PLAN", None)
        self.addCleanup(os.environ.pop, "OPENSWARM_CLAUDE_PLAN", None)
        os.remove(os.path.join(self.home, ".codex", "auth.json"))
        self.assertEqual(xs.codex_plan(self.policy), "team")
        self.assertEqual(xs.probe_claude(self.policy)["plan"], "pro")

    def test_legacy_xswarm_env_vars_still_honoured(self):
        os.environ["XSWARM_CODEX_PLAN"] = "legacy"
        self.addCleanup(os.environ.pop, "XSWARM_CODEX_PLAN", None)
        self.assertEqual(xs.codex_plan(self.policy), "legacy")
        os.environ["OPENSWARM_CODEX_PLAN"] = "new"
        self.addCleanup(os.environ.pop, "OPENSWARM_CODEX_PLAN", None)
        self.assertEqual(xs.codex_plan(self.policy), "new")

    def test_keychain_is_noop_off_macos(self):
        if sys.platform == "darwin":
            self.skipTest("macOS has a real Keychain")
        self.assertIsNone(xs.keychain("anything"))


class OfflineStateTest(unittest.TestCase):
    def test_route_cli_with_state_file_needs_no_backends(self):
        out = subprocess.run([sys.executable, os.path.join(HERE, "..", "bin", "openswarm"), "route", "--type", "impl",
                              "--difficulty", "3"], capture_output=True, text=True, timeout=60,
                             env={**os.environ, "OPENSWARM_STATE_FILE": _load.FIXTURE_STATE})
        self.assertEqual(out.returncode, 0, out.stderr)
        r = json.loads(out.stdout)
        self.assertTrue(r["ok"])
        self.assertEqual(r["backend"], "codex")


if __name__ == "__main__":
    unittest.main()
