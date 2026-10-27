"""Offline issue-bus coverage using fake gh and local bare target repositories."""
import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock

import _load

xs = _load.load()
FAKE_GH = Path(_load.HERE) / "fixtures" / "fake_gh.py"


class BusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="openswarm-bus-")
        self.root = Path(self.tmp.name)
        self.data = self.root / "planner"
        self.data.mkdir()
        self.state_path = self.root / "github.json"
        self.state_path.write_text(json.dumps({"repos": {"org/bus": {"labels": [], "issues": [], "files": {}}}}))
        self.bare = self.root / "target.git"
        self.repo = self.root / "target"
        self.git(None, "init", "--bare", "-q", str(self.bare))
        self.repo.mkdir()
        self.git(self.repo, "init", "-q")
        self.git(self.repo, "config", "user.name", "Test User")
        self.git(self.repo, "config", "user.email", "test@example.invalid")
        (self.repo / "base.txt").write_text("base\n")
        self.git(self.repo, "add", "base.txt")
        self.git(self.repo, "commit", "-qm", "base")
        self.base = self.git(self.repo, "rev-parse", "HEAD")
        self.git(self.repo, "branch", "-M", "main")
        self.git(self.repo, "remote", "add", "origin", str(self.bare))
        self.git(self.repo, "push", "-q", "origin", "main")
        self.env_names = ("OPENSWARM_HOME", "OPENSWARM_GH_BIN", "OPENSWARM_FAKE_GH_STATE",
                          "OPENSWARM_FAKE_GH_LOGIN", "OPENSWARM_FAKE_GH_ISSUE_LOGIN",
                          "OPENSWARM_BUS_TARGET_URL", "OPENSWARM_STATE_FILE",
                          "OPENSWARM_PI_BIN")
        self.old_env = {key: os.environ.get(key) for key in self.env_names}
        os.environ.update({"OPENSWARM_HOME": str(self.data), "OPENSWARM_GH_BIN": str(FAKE_GH),
                           "OPENSWARM_FAKE_GH_STATE": str(self.state_path), "OPENSWARM_FAKE_GH_LOGIN": "planner",
                           "OPENSWARM_FAKE_GH_ISSUE_LOGIN": "planner",
                           "OPENSWARM_BUS_TARGET_URL": str(self.bare), "OPENSWARM_STATE_FILE": _load.FIXTURE_STATE})
        FAKE_GH.chmod(FAKE_GH.stat().st_mode | stat.S_IEXEC)
        self.constants = {key: getattr(xs, key) for key in ("DATA", "RUNS", "LEDGER", "WORKTREES", "BREAKERS")}
        xs.DATA = str(self.data)
        xs.RUNS = str(self.data / "runs")
        xs.LEDGER = str(self.data / "ledger.jsonl")
        xs.WORKTREES = str(self.data / "worktrees")
        xs.BREAKERS = str(self.data / "breakers.json")
        self.policy = xs.deep_merge(xs.load_policy(), {"bus": {"repo": "org/bus", "allowed_executors": ["executor"],
                                                               "allowed_planners": ["planner"],
                                                               "worker_repos": ["org/target"],
                                                               "poll_seconds": 0.02, "labels_prefix": "openswarm"}})
        self.cfg = xs.bus_config(self.policy)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        for key, value in self.old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        for key, value in self.constants.items():
            setattr(xs, key, value)
        self.tmp.cleanup()

    @staticmethod
    def git(cwd, *args):
        command = ["git"] + (["-C", str(cwd)] if cwd else []) + list(args)
        return subprocess.check_output(command, stderr=subprocess.PIPE, text=True).strip()

    def state(self):
        for _ in range(10):
            try:
                return json.loads(self.state_path.read_text())
            except (OSError, ValueError):
                time.sleep(0.005)
        self.fail("fake GitHub state was not readable")

    def issue(self, number=1):
        return next(row for row in self.state()["repos"]["org/bus"]["issues"] if row["number"] == number)

    def add_comment(self, number, body, login="executor"):
        previous = os.environ.get("OPENSWARM_FAKE_GH_LOGIN")
        os.environ["OPENSWARM_FAKE_GH_LOGIN"] = login
        try:
            return xs.bus_comment("org/bus", number, body)
        finally:
            os.environ["OPENSWARM_FAKE_GH_LOGIN"] = previous or "planner"

    def set_labels(self, number, add, remove):
        labels = xs.bus_label_names(self.cfg)
        return xs.bus_set_issue_labels("org/bus", number, self.cfg,
                                       add=[labels[name] for name in add],
                                       remove=[labels[name] for name in remove])

    def run_args(self, budget=1.0, task="create a file", executor=None):
        return argparse.Namespace(type="impl", difficulty=3, context_tokens=0, backend="github-issue", model=None,
                                  effort=None, prompt=task, task_file=None, title="bus test", cwd=str(self.repo),
                                  base=self.base, no_worktree=False, verify=None, verify_baseline=False,
                                  keep_worktree=False, no_fallback=False, budget_minutes=budget,
                                  detach=False, run_id=None, dry_run=False, executor=executor)

    def start_planner(self, budget=1.0, task="create a file", executor=None):
        outcome = {}

        def run():
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    xs.cmd_run(self.run_args(budget=budget, task=task, executor=executor), self.policy)
            except SystemExit as error:
                outcome["exit"] = error.code
            except BaseException as error:  # retain thread failures as test evidence
                outcome["error"] = error

        with mock.patch.object(xs, "bus_remote", return_value=(str(self.repo), str(self.bare), "org/target")):
            thread = threading.Thread(target=run, daemon=True)
            thread.start()
            self.addCleanup(lambda: thread.join(timeout=5))
            for _ in range(250):
                issues = self.state()["repos"]["org/bus"]["issues"]
                if issues and issues[0]["comments"]:
                    return thread, outcome, issues[0]
                time.sleep(0.01)
        self.fail("planner did not create its issue")

    def push_result_branch(self, run_id, filename="worker.txt"):
        clone = self.root / ("executor-" + run_id)
        self.git(None, "clone", "-q", str(self.bare), str(clone))
        self.git(clone, "config", "user.name", "Executor")
        self.git(clone, "config", "user.email", "executor@example.invalid")
        (clone / filename).write_text("completed\n")
        self.git(clone, "add", filename)
        self.git(clone, "commit", "-qm", "external result")
        self.git(clone, "push", "-q", "origin", "HEAD:refs/heads/openswarm/" + run_id)
        return self.git(clone, "rev-parse", "HEAD")

    @staticmethod
    def report(status, branch=None, commit=None):
        return {"status": status, "summary": "external work", "files_changed": ["worker.txt"],
                "tests": "fake verify", "followups": [], "branch": branch, "commit": commit, "pr_url": None}

    def send_result(self, number, report, label="done", login="executor"):
        self.add_comment(number, "```openswarm-result\n" + json.dumps(report) + "\n```", login=login)
        self.set_labels(number, [label], ["queued", "claimed"])

    def finish_thread(self, thread, outcome):
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive(), "planner did not finish")
        self.assertNotIn("error", outcome, outcome.get("error"))
        return json.loads((Path(xs.RUNS) / self.issue()["body"].split('"run_id": "')[1].split('"')[0] / "result.json").read_text())

    def test_create_claim_fetch_branch_verify_and_done_result(self):
        thread, outcome, issue = self.start_planner(task="create worker.txt", executor="external-agent")
        metadata = xs.bus_extract_task(issue["body"])
        self.assertEqual(metadata["repo"], "org/target")
        self.assertEqual(metadata["base"], self.base)
        self.assertIn("executor:external-agent", xs.issue_label_names(issue))
        run_id = metadata["run_id"]
        self.add_comment(issue["number"], "/claim external-agent")
        self.set_labels(issue["number"], ["claimed"], ["queued"])
        commit = self.push_result_branch(run_id)
        self.send_result(issue["number"], self.report("done", f"openswarm/{run_id}", commit))
        saved_issue = self.issue()
        self.assertEqual(xs.bus_label_names(self.cfg)["done"], xs.issue_label_names(saved_issue).intersection(
            {xs.bus_label_names(self.cfg)["done"]}).pop())
        self.assertTrue(any(xs.bus_parse_result(comment["body"]) for comment in saved_issue["comments"]))
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive(), {"outcome": outcome, "labels": saved_issue["labels"],
                                              "comments": saved_issue["comments"]})
        self.assertEqual(outcome.get("exit"), 0)
        result = json.loads((Path(xs.RUNS) / run_id / "result.json").read_text())
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["branch"], f"openswarm/{run_id}")
        self.assertEqual(result["commit"], commit)
        self.assertEqual(result["bus"]["executor"], "external-agent")
        self.assertEqual(self.git(self.repo, "rev-parse", f"openswarm/{run_id}"), commit)
        ledger = [json.loads(line) for line in Path(xs.LEDGER).read_text().splitlines()]
        self.assertEqual(ledger[-1]["status"], "done")

    def test_untrusted_claim_and_result_are_ignored(self):
        thread, outcome, issue = self.start_planner(budget=0.035)
        self.add_comment(issue["number"], "/claim intruder", login="not-allowed")
        self.add_comment(issue["number"], "/claim external-agent", login="executor")
        self.set_labels(issue["number"], ["claimed"], ["queued"])
        self.send_result(issue["number"], self.report("done"), login="not-allowed")
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(outcome.get("exit"), 1)
        run_id = xs.bus_extract_task(issue["body"])["run_id"]
        result = json.loads((Path(xs.RUNS) / run_id / "result.json").read_text())
        self.assertEqual(result["status"], "budget_exceeded")
        self.assertIsNone(result["result"])
        self.assertIn("openswarm:cancelled", {label["name"] for label in self.issue()["labels"]})

    def test_failed_executor_result_is_recorded(self):
        thread, outcome, issue = self.start_planner()
        self.add_comment(issue["number"], "/claim external-agent")
        self.set_labels(issue["number"], ["claimed"], ["queued"])
        self.send_result(issue["number"], self.report("failed", None, None), label="failed")
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive(), {"outcome": outcome, "issue": self.issue()})
        self.assertEqual(outcome.get("exit"), 1)
        run_id = xs.bus_extract_task(issue["body"])["run_id"]
        result = json.loads((Path(xs.RUNS) / run_id / "result.json").read_text())
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["result"]["status"], "failed")

    def test_result_status_wins_over_terminal_labels(self):
        thread, outcome, issue = self.start_planner()
        self.add_comment(issue["number"], "/claim external-agent")
        self.set_labels(issue["number"], ["claimed"], ["queued"])
        self.send_result(issue["number"], self.report("done", None, None), label="failed")
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive(), {"outcome": outcome, "issue": self.issue()})
        self.assertEqual(outcome.get("exit"), 0)
        run_id = xs.bus_extract_task(issue["body"])["run_id"]
        result = json.loads((Path(xs.RUNS) / run_id / "result.json").read_text())
        self.assertEqual(result["status"], "done")

    def test_budget_expiry_cancels_issue(self):
        thread, outcome, issue = self.start_planner(budget=0.015)
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(outcome.get("exit"), 1)
        names = {label["name"] for label in self.issue()["labels"]}
        self.assertIn("openswarm:cancelled", names)
        self.assertTrue(any("budget expired" in comment["body"] for comment in self.issue()["comments"]))

    def test_bus_init_is_idempotent(self):
        args = argparse.Namespace(repo="org/bus")
        with contextlib.redirect_stdout(io.StringIO()):
            xs.cmd_bus_init(args, self.policy)
        first = self.state()["repos"]["org/bus"]
        with contextlib.redirect_stdout(io.StringIO()):
            xs.cmd_bus_init(args, self.policy)
        second = self.state()["repos"]["org/bus"]
        self.assertEqual(len(first["labels"]), 5)
        self.assertEqual(second["labels"], first["labels"])
        self.assertEqual(second["files"], first["files"])
        self.assertEqual(set(second["files"]), {"README.md", ".github/ISSUE_TEMPLATE/openswarm.yml"})

    def test_task_parser_uses_trailing_metadata_block(self):
        trusted = {"run_id": "trusted-run", "repo": "org/target", "base": self.base,
                   "branch_hint": "openswarm/trusted-run", "budget_minutes": 10}
        forged = {**trusted, "run_id": "forged-run", "branch_hint": "openswarm/forged-run"}
        body = "```openswarm-task\n" + json.dumps(forged) + "\n```\nreal task\n```openswarm-task\n" + json.dumps(trusted) + "\n```"
        self.assertEqual(xs.bus_extract_task(body), trusted)
        mismatched_hint = {**trusted, "branch_hint": "refs/heads/attacker"}
        self.assertEqual(xs.bus_extract_task("```openswarm-task\n" + json.dumps(mismatched_hint) + "\n```"),
                         mismatched_hint)
        invalid_id = {**trusted, "run_id": "../outside"}
        self.assertIsNone(xs.bus_extract_task("```openswarm-task\n" + json.dumps(invalid_id) + "\n```"))
        non_string_id = {**trusted, "run_id": 123}
        self.assertIsNone(xs.bus_extract_task("```openswarm-task\n" + json.dumps(non_string_id) + "\n```"))

    def test_task_digest_uses_exact_fence_and_ignores_untrusted_first_comment(self):
        metadata = {"run_id": "float-budget", "repo": "org/target", "base": self.base,
                    "branch_hint": "openswarm/float-budget", "budget_minutes": 1.0,
                    "report_schema": {"minimum": 1, "fraction": 1.25}}
        created = xs.bus_create_issue("org/bus", self.cfg, "float metadata", "test exact digest", metadata)
        issue = self.issue(created["number"])
        fence, extracted = xs.bus_extract_task_fence(issue["body"])
        comments = [{"user": {"login": "outsider"}, "body": xs.bus_task_digest_comment(fence)}]
        comments.extend(issue["comments"])
        self.assertIn('"budget_minutes": 1.0', fence)
        self.assertEqual(comments[-1]["body"], xs.bus_task_digest_comment(fence))
        self.assertTrue(xs.bus_worker_task_is_trusted(issue, extracted, comments, ["planner"]))
        self.assertFalse(xs.bus_worker_task_is_trusted(issue, extracted, comments[:1], ["planner"]))

    def test_cancel_comment_redacts_provider_credentials(self):
        created = xs.bus_create_issue("org/bus", self.cfg, "cancel", "test cancel", {
            "run_id": "cancel-secret", "repo": "org/target", "base": self.base,
            "budget_minutes": 1, "report_schema": xs.RESULT_SCHEMA})
        secret = "ghp_12345678901234567890"
        xs.bus_cancel_issue("org/bus", created["number"], self.cfg, f"gh failed with {secret}")
        comment = self.issue(created["number"])["comments"][-1]["body"]
        self.assertNotIn(secret, comment)
        self.assertIn("[REDACTED]", comment)

    def test_result_branch_validation_matches_run_id_pattern(self):
        report = {"status": "done", "summary": "ok", "files_changed": [], "tests": "",
                  "followups": [], "branch": "openswarm/a_B-9", "commit": None}
        body = lambda value: "```openswarm-result\n" + json.dumps({**report, "branch": value}) + "\n```"
        self.assertIsNotNone(xs.bus_parse_result(body(report["branch"])))
        self.assertIsNone(xs.bus_parse_result(body("openswarm/a.b")))

    def test_cmd_run_redacts_github_bus_errors_in_result_and_ledger(self):
        secret = "github_pat_12345678901234567890"
        args = self.run_args()
        with mock.patch.object(xs, "new_run_id", return_value="redact-bus-error"), \
                mock.patch.object(xs, "cmd_bus_run", side_effect=xs.BusError(f"gh failed: {secret}")), \
                contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            xs.cmd_run(args, self.policy)
        rdir = Path(xs.RUNS) / "redact-bus-error"
        result = json.loads((rdir / "result.json").read_text())
        ledger = Path(xs.LEDGER).read_text()
        self.assertNotIn(secret, result["error"])
        self.assertNotIn(secret, ledger)
        self.assertIn("[REDACTED]", result["error"])

    def test_unpushed_base_fails_before_issue_creation(self):
        (self.repo / "local-only.txt").write_text("not pushed\n")
        self.git(self.repo, "add", "local-only.txt")
        self.git(self.repo, "commit", "-qm", "local only")
        args = self.run_args()
        args.base = self.git(self.repo, "rev-parse", "HEAD")
        stderr = io.StringIO()
        with mock.patch.object(xs, "bus_remote", return_value=(str(self.repo), str(self.bare), "org/target")), \
                contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as exited:
            xs.cmd_run(args, self.policy)
        self.assertEqual(exited.exception.code, 2)
        report = json.loads((Path(xs.RUNS) / next(Path(xs.RUNS).iterdir()).name / "result.json").read_text())
        self.assertEqual(report["error"], "push the base first")
        self.assertEqual(self.state()["repos"]["org/bus"]["issues"], [])

    def test_reference_worker_claims_runs_pushes_and_reports(self):
        fake_pi = self.root / "fake-pi"
        fake_pi.write_text("""#!/usr/bin/env python3
import json
with open('worker.txt', 'w') as stream:
    stream.write('worker result\\n')
report = {'status':'done','summary':'worker completed','files_changed':['worker.txt'],'tests':'fake executor','followups':[]}
message = {'role':'assistant','content':[{'type':'text','text':json.dumps(report)}], 'usage':{'input':2,'output':3}}
print(json.dumps({'type':'message_end','message':message}), flush=True)
""")
        fake_pi.chmod(fake_pi.stat().st_mode | stat.S_IEXEC)
        os.environ["OPENSWARM_PI_BIN"] = str(fake_pi)
        knowledge = self.root / "knowledge"
        (knowledge / "lessons").mkdir(parents=True)
        (knowledge / "card-types.yaml").write_text("types:\n  - fix\n")
        (knowledge / "lessons" / "L-0300.yaml").write_text(
            "id: L-0300\nrule: Validate worker edits with a focused test.\nguard_level: L2\n"
            "applies_to:\n  card_types: [fix]\n  paths: [src/*.py]\n  backends: [pi]\n")
        self.policy = xs.deep_merge(self.policy, {"knowledge": {"repo": str(knowledge)}})
        local_policy = {"knowledge": {"repo": str(knowledge)},
                        "bus": {"repo": "org/bus", "allowed_executors": ["executor"], "poll_seconds": 0.02}}
        (self.data / "policy.local.json").write_text(json.dumps(local_policy))
        run_id = "worker-e2e"
        metadata = {"run_id": run_id, "repo": "org/target", "base": self.base,
                    "branch_hint": "openswarm/ignored-hint", "verify": f"touch {self.root / 'untrusted-verify-ran'}",
                    "budget_minutes": 1,
                    "report_schema": xs.RESULT_SCHEMA}
        issue = xs.bus_create_issue("org/bus", self.cfg, "worker e2e", "write worker.txt", metadata)
        worker_args = argparse.Namespace(bus="org/bus", name="external-agent", backend="pi", model=None,
                                         budget_minutes=1, once=True, repos="org/target", verify=None,
                                         card_type="fix", paths=["src/worker.py"])
        os.environ["OPENSWARM_FAKE_GH_LOGIN"] = "executor"
        xs.cmd_bus_worker(worker_args, self.policy)
        current = self.issue(issue["number"])
        names = {label["name"] for label in current["labels"]}
        self.assertIn("openswarm:done", names)
        result_comments = [xs.bus_parse_result(comment["body"]) for comment in current["comments"]]
        report = next(report for report in result_comments if report)
        ledger = [json.loads(line) for line in (self.data / "ledger.jsonl").read_text().splitlines()]
        entry = next(row for row in ledger if row["id"] == run_id)
        self.assertEqual(entry["lessons_injected"], ["L-0300"])
        self.assertEqual(report["status"], "done")
        self.assertEqual(report["branch"], f"openswarm/{run_id}")
        self.assertFalse((self.root / "untrusted-verify-ran").exists())
        self.assertTrue(any("Ignored task verify" in note for note in report["followups"]))
        self.assertTrue(report["commit"])
        self.assertIn("worker.txt", self.git(None, "--git-dir", str(self.bare), "ls-tree", "-r", "--name-only", report["commit"]))

    def test_worker_rejects_changed_task_body_and_unlisted_repo(self):
        metadata = {"run_id": "digest-check", "repo": "org/target", "base": self.base,
                    "branch_hint": "openswarm/digest-check", "budget_minutes": 1,
                    "report_schema": xs.RESULT_SCHEMA}
        issue = xs.bus_create_issue("org/bus", self.cfg, "digest", "write a file", metadata)
        state = self.state()
        changed = next(row for row in state["repos"]["org/bus"]["issues"] if row["number"] == issue["number"])
        changed["body"] = changed["body"].replace('"budget_minutes": 1', '"budget_minutes": 2')
        self.state_path.write_text(json.dumps(state))
        os.environ["OPENSWARM_FAKE_GH_LOGIN"] = "executor"
        worker_args = argparse.Namespace(bus="org/bus", name="external-agent", backend="pi", model=None,
                                         budget_minutes=1, once=True, repos="org/target", verify=None)
        self.assertFalse(xs.bus_worker_run(changed, self.cfg, worker_args, self.policy))
        self.assertEqual(len(self.issue(issue["number"])["comments"]), 1)
        self.assertIn("openswarm:queued", {label["name"] for label in self.issue(issue["number"])["labels"]})

        other = {**metadata, "run_id": "repo-check", "repo": "org/other",
                 "branch_hint": "openswarm/invalid/ignored"}
        other_issue = xs.bus_create_issue("org/bus", self.cfg, "repo", "write a file", other)
        self.assertFalse(xs.bus_worker_run(other_issue, self.cfg, worker_args, self.policy))
        self.assertEqual(len(self.issue(other_issue["number"])["comments"]), 1)

        os.environ["OPENSWARM_FAKE_GH_ISSUE_LOGIN"] = "not-planner"
        forged_author_issue = xs.bus_create_issue("org/bus", self.cfg, "author", "write a file", metadata)
        os.environ["OPENSWARM_FAKE_GH_ISSUE_LOGIN"] = "planner"
        self.assertFalse(xs.bus_worker_run(forged_author_issue, self.cfg, worker_args, self.policy))
        self.assertEqual(len(self.issue(forged_author_issue["number"])["comments"]), 1)

    def test_bus_redacts_events_results_and_gh_errors(self):
        secrets = "ghp_12345678901234567890 github_pat_12345678901234567890 sk-12345678901234567890 Bearer abcdefghijklmnop"
        event_path = Path(xs.RUNS) / "redact" / "events.jsonl"
        xs.bus_event(str(event_path), secrets)
        event = event_path.read_text()
        for token in ("ghp_123", "github_pat_", "sk-123", "Bearer abc"):
            self.assertNotIn(token, event)
        self.assertGreaterEqual(event.count("[REDACTED]"), 4)

        rdir = Path(xs.RUNS) / "redact"
        with self.assertRaises(SystemExit):
            xs.bus_finish_run("redact", str(rdir), "redact test", str(self.repo), str(self.bare), None,
                              {"status": "failed", "summary": secrets, "files_changed": [], "tests": "",
                               "followups": [], "branch": None, "commit": None}, None, "failed", time.time())
        saved = (rdir / "result.json").read_text()
        ledger = Path(xs.LEDGER).read_text()
        self.assertNotIn("github_pat_", saved)
        self.assertNotIn("ghp_123", saved)
        self.assertNotIn("sk-123", ledger)

        with mock.patch.object(xs.subprocess, "run", return_value=mock.Mock(returncode=1, stdout="",
                                                                               stderr="github_pat_12345678901234567890")):
            with self.assertRaises(xs.BusError) as error:
                xs.gh_transport("repos/org/bus/issues")
        self.assertNotIn("github_pat_", str(error.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
