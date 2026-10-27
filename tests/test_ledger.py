"""Ledger v2 shape and card type validation use local fixtures only."""
import contextlib
import datetime as dt
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import urllib.parse
from unittest import mock

import _load

xs = _load.load()


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="openswarm-ledger-")
        self.root = Path(self.tmp.name)
        self.constants = {key: getattr(xs, key) for key in ("DATA", "LEDGER", "RUNS", "HOME")}
        xs.DATA = str(self.root / "data")
        xs.LEDGER = str(self.root / "data" / "ledger.jsonl")
        xs.RUNS = str(self.root / "data" / "runs")
        xs.HOME = str(self.root / "home")

    def tearDown(self):
        for key, value in self.constants.items():
            setattr(xs, key, value)
        self.tmp.cleanup()

    def make_gh(self, fixtures):
        fake = self.root / "gh"
        fixture_file = self.root / "gh-fixtures.json"
        call_log = self.root / "gh-calls.txt"
        fixture_file.write_text(json.dumps(fixtures))
        fake.write_text(
            f"#!{sys.executable}\n"
            "import json, os, sys\n"
            "endpoint = sys.argv[2]\n"
            "with open(os.environ['GH_CALL_LOG'], 'a') as log: log.write(endpoint + '\\n')\n"
            "with open(os.environ['GH_FIXTURES']) as source: fixtures = json.load(source)\n"
            "if endpoint not in fixtures:\n"
            "    print('missing fixture: ' + endpoint, file=sys.stderr)\n"
            "    raise SystemExit(1)\n"
            "result = fixtures[endpoint]\n"
            "if isinstance(result, dict) and '__http_status__' in result:\n"
            "    print('HTTP ' + str(result['__http_status__']) + ': ' + result.get('message', ''), file=sys.stderr)\n"
            "    raise SystemExit(1)\n"
            "print(json.dumps(result))\n"
        )
        fake.chmod(0o755)
        self.gh_env = {"OPENSWARM_GH_BIN": str(fake), "GH_FIXTURES": str(fixture_file),
                       "GH_CALL_LOG": str(call_log)}
        return call_log

    def make_gh_rate_limited_once(self, fixtures, fail_after):
        fake = self.root / "gh-rate-limited"
        fixture_file = self.root / "gh-rate-fixtures.json"
        call_log = self.root / "gh-rate-calls.txt"
        failed = self.root / "gh-rate-failed"
        fixture_file.write_text(json.dumps(fixtures))
        fake.write_text(
            f"#!{sys.executable}\n"
            "import json, os, sys\n"
            "endpoint = sys.argv[2]\n"
            "with open(os.environ['GH_CALL_LOG'], 'a') as log: log.write(endpoint + '\\n')\n"
            "with open(os.environ['GH_CALL_LOG']) as log: calls = len(log.readlines())\n"
            "if calls > int(os.environ['GH_FAIL_AFTER']) and endpoint != 'rate_limit' and not os.path.exists(os.environ['GH_FAILED']):\n"
            "    open(os.environ['GH_FAILED'], 'w').close()\n"
            "    print('HTTP 403: API rate limit exceeded', file=sys.stderr)\n"
            "    raise SystemExit(1)\n"
            "with open(os.environ['GH_FIXTURES']) as source: fixtures = json.load(source)\n"
            "if endpoint not in fixtures:\n"
            "    print('missing fixture: ' + endpoint, file=sys.stderr)\n"
            "    raise SystemExit(1)\n"
            "print(json.dumps(fixtures[endpoint]))\n"
        )
        fake.chmod(0o755)
        self.gh_env = {"OPENSWARM_GH_BIN": str(fake), "GH_FIXTURES": str(fixture_file),
                       "GH_CALL_LOG": str(call_log), "GH_FAIL_AFTER": str(fail_after),
                       "GH_FAILED": str(failed)}
        return call_log

    def run_ingest(self, *args):
        original_argv = sys.argv
        try:
            sys.argv = ["openswarm", "ledger", "ingest", *args]
            output = io.StringIO()
            with mock.patch.dict(os.environ, self.gh_env), contextlib.redirect_stdout(output):
                xs.main()
            return output.getvalue()
        finally:
            sys.argv = original_argv

    def run_ingest_result(self, *args):
        original_argv = sys.argv
        try:
            sys.argv = ["openswarm", "ledger", "ingest", *args]
            output, error = io.StringIO(), io.StringIO()
            with mock.patch.dict(os.environ, self.gh_env), contextlib.redirect_stdout(output), \
                    contextlib.redirect_stderr(error):
                try:
                    xs.main()
                except SystemExit as exc:
                    return exc.code, output.getvalue(), error.getvalue()
            return 0, output.getvalue(), error.getvalue()
        finally:
            sys.argv = original_argv

    def run_ledger_command(self, *args):
        original_argv = sys.argv
        try:
            sys.argv = ["openswarm", "ledger", *args]
            output = io.StringIO()
            with mock.patch.object(xs, "load_policy", return_value={}), contextlib.redirect_stdout(output):
                xs.main()
            return json.loads(output.getvalue())
        finally:
            sys.argv = original_argv

    def put_fleet_ledger(self, *records):
        path = Path(xs.DATA) / "ledger-fleet.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(record) + "\n" for record in records))
        return path

    def test_old_records_remain_readable_and_json_returns_full_entries(self):
        old = {"id": "old-run", "ts": 1, "status": "done", "backend": "pi", "title": "old"}
        Path(xs.LEDGER).parent.mkdir(parents=True)
        Path(xs.LEDGER).write_text(json.dumps(old) + "\n")
        self.assertEqual(list(xs.ledger_entries()), [old])

        original_argv = sys.argv
        try:
            sys.argv = ["openswarm", "ledger", "--json"]
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                xs.main()
        finally:
            sys.argv = original_argv
        self.assertEqual(json.loads(output.getvalue()), [old])

    def test_known_card_types_reject_unknown_before_creating_run(self):
        knowledge = self.root / "knowledge"
        knowledge.mkdir()
        (knowledge / "card-types.yaml").write_text("types:\n  - feature\n  - bugfix\n")
        policy = {"knowledge": {"repo": str(knowledge)}}
        self.assertEqual(xs.configured_card_types(policy), {"feature", "bugfix"})
        with self.assertRaisesRegex(ValueError, "unknown card type: typo"):
            xs.cmd_run(type("Args", (), {"card_type": "typo"})(), policy)
        self.assertFalse(Path(xs.RUNS).exists())
        self.assertFalse(Path(xs.LEDGER).exists())

    def test_missing_knowledge_repo_accepts_and_records_any_card_type(self):
        policy = {"knowledge": {"repo": str(self.root / "missing")}}
        self.assertIsNone(xs.configured_card_types(policy))
        xs.validate_card_type(policy, "unlisted-type")

    def test_ledger_json_keeps_full_v2_record(self):
        xs.append_ledger({"id": "fake-1", "ts": 123, "title": "fake sk-123456789012 run", "status": "done",
                          "backend": "pi", "files_changed": ["src/a.py", "README.md", "src/b.py"],
                          "usage": {"input": 12, "output": 8}, "seconds": 3.5})
        record = json.loads(Path(xs.LEDGER).read_text())
        self.assertEqual(record["run_id"], "fake-1")
        self.assertNotIn("sk-123", json.dumps(record))
        self.assertEqual(record["area"], ["README.md", "src"])
        self.assertEqual(record["tokens"], {"input": 12, "output": 8, "total": 20})
        self.assertEqual(record["lessons_injected"], [])
        self.assertIsNone(record["merged"])
        self.assertFalse(record["exec_pass"])
        self.assertIsNone(record["accepted"])
        self.assertEqual(list(xs.ledger_entries()), [record])

        original_argv = sys.argv
        try:
            sys.argv = ["openswarm", "ledger", "--json"]
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                xs.main()
        finally:
            sys.argv = original_argv
        self.assertEqual(json.loads(output.getvalue()), [record])

    def test_exec_pass_uses_changes_and_verify_not_executor_status(self):
        self.assertTrue(xs.exec_pass_value("partial", True, False, False, None))  # partial with a commit
        self.assertFalse(xs.exec_pass_value("failed", False, False, False, None))
        self.assertTrue(xs.exec_pass_value("failed", True, False, False, None))
        self.assertFalse(xs.exec_pass_value("done", True, False, True, {"rc": 2}))
        self.assertTrue(xs.exec_pass_value("done", False, True, False, None))  # read-only output file

        output = self.root / "review.md"
        output.write_text("review")
        self.assertTrue(xs.read_only_output_exists(str(self.root), ["review.md"]))
        self.assertFalse(xs.read_only_output_exists(str(self.root), ["missing.md"]))

    def test_accept_reject_and_backfill_pass_cli(self):
        Path(xs.LEDGER).parent.mkdir(parents=True)
        Path(xs.LEDGER).write_text("".join(json.dumps(record) + "\n" for record in (
            {"id": "accepted-run", "status": "done"},
            {"id": "rejected-run", "status": "failed"},
        )))
        accepted = self.run_ledger_command("accept", "accepted-run", "--reason", "reviewed and merged")
        rejected = self.run_ledger_command("accept", "rejected-run", "--reject", "--reason", "needs fixes")
        rows = [json.loads(line) for line in Path(xs.LEDGER).read_text().splitlines()]
        self.assertTrue(rows[0]["accepted"])
        self.assertEqual(rows[0]["accepted_reason"], "reviewed and merged")
        self.assertIsInstance(rows[0]["accepted_at"], (int, float))
        self.assertFalse(rows[1]["accepted"])
        self.assertEqual(accepted["run_id"], "accepted-run")
        self.assertFalse(rejected["accepted"])

        Path(xs.LEDGER).write_text("".join(json.dumps(record) + "\n" for record in (
            {"id": "partial", "status": "partial", "commit": "abc"},
            {"id": "failed", "status": "failed", "commit": "def"},
            {"id": "verified-fail", "status": "done", "files_changed": ["x"],
             "verify": {"rc": 2}},
            {"id": "unverified", "status": "done", "files_changed": ["x"], "verify": None},
        )))
        result = self.run_ledger_command("backfill-pass")
        rows = {record["id"]: record for record in
                (json.loads(line) for line in Path(xs.LEDGER).read_text().splitlines())}
        self.assertEqual(result["updated"], 3)
        self.assertTrue(rows["partial"]["exec_pass"])
        self.assertNotIn("exec_pass", rows["failed"])
        self.assertFalse(rows["verified-fail"]["exec_pass"])
        self.assertTrue(rows["unverified"]["exec_pass"])

        fleet = self.put_fleet_ledger({"id": "fleet-run", "status": "done", "commit": "abc"})
        fleet_result = self.run_ledger_command("backfill-pass", "--fleet")
        self.assertEqual(fleet_result["updated"], 1)
        self.assertTrue(json.loads(fleet.read_text())["exec_pass"])

    def pull_list_endpoint(self, repo, page=1):
        query = urllib.parse.urlencode({"state": "all", "sort": "updated", "direction": "desc",
                                        "per_page": 100, "page": page})
        return f"repos/{repo}/pulls?{query}"

    def matched_pr(self, repo, number, updated_at=None, **fields):
        return {"number": number, "updated_at": updated_at or xs.iso(xs.NOW()),
                "html_url": f"https://github.com/{repo}/pull/{number}",
                "head": {"ref": "other-branch", "sha": "0" * 40}, **fields}

    def pr_detail_fixtures(self, repo, number, merged=False):
        return {
            f"repos/{repo}/pulls/{number}": {"number": number, "merged": merged, "state": "closed"},
            f"repos/{repo}/pulls/{number}/comments?per_page=100&page=1": [],
            f"repos/{repo}/pulls/{number}/reviews?per_page=100&page=1": [],
        }

    def test_ledger_ingest_matches_body_branch_and_head_sha_with_one_repo_list(self):
        repo, sha = "octo/project", "a" * 40
        pulls = [
            self.matched_pr(repo, 14, body="Created for run body-run"),
            self.matched_pr(repo, 22, head={"ref": "openswarm/branch-run", "sha": "b" * 40,
                                             "label": "octo:openswarm/branch-run"}),
            self.matched_pr(repo, 31, head={"ref": "another", "sha": sha}),
        ]
        fixtures = {self.pull_list_endpoint(repo): pulls}
        for number in (14, 22, 31):
            fixtures.update(self.pr_detail_fixtures(repo, number))
        calls = self.make_gh(fixtures)
        path = self.put_fleet_ledger(
            {"id": "body-run", "ts": xs.NOW(), "future_field": {"kept": True}},
            {"id": "branch-run", "ts": xs.NOW(), "branch": "refs/heads/openswarm/branch-run"},
            {"id": "sha-run", "ts": xs.NOW(), "commit": sha},
        )

        self.run_ingest("--repo", repo)

        entries = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual([entry["pr"]["number"] for entry in entries], [14, 22, 31])
        self.assertEqual([entry["accepted"] for entry in entries], [False, False, False])
        self.assertEqual(entries[0]["future_field"], {"kept": True})
        called = calls.read_text().splitlines()
        self.assertEqual(called.count(self.pull_list_endpoint(repo)), 1)
        self.assertFalse(any(endpoint.startswith("search/issues?") for endpoint in called))
        self.assertEqual(sum("/comments?" in endpoint for endpoint in called), 3)
        self.assertEqual(sum("/reviews?" in endpoint for endpoint in called), 3)

        first_contents = path.read_bytes()
        self.run_ingest("--repo", repo)
        self.assertEqual(path.read_bytes(), first_contents)
        self.assertEqual(calls.read_text().splitlines(), called)

    def test_ledger_ingest_keeps_review_badges_merge_and_defect_results(self):
        repo, run_id, number = "octo/project", "quality-run", 14
        merged_at = dt.datetime.fromtimestamp(xs.NOW() - 2 * 86400, dt.timezone.utc).isoformat().replace("+00:00", "Z")
        bug_created_at = dt.datetime.fromtimestamp(xs.NOW() - 86400, dt.timezone.utc).isoformat().replace("+00:00", "Z")
        params = {"state": "all", "labels": "bug", "since": merged_at}
        bug_endpoint = f"repos/{repo}/issues?{urllib.parse.urlencode(params)}&per_page=100&page=1"
        pr = self.matched_pr(repo, number, body=f"Created for {run_id}")
        fixtures = {
            self.pull_list_endpoint(repo): [pr],
            f"repos/{repo}/pulls/{number}": {"number": number, "merged": True, "merged_at": merged_at,
                                                 "state": "closed",
                                                 "html_url": f"https://github.com/{repo}/pull/{number}"},
            f"repos/{repo}/pulls/{number}/comments?per_page=100&page=1": [
                {"user": {"login": "chatgpt-codex-connector[bot]"}, "body": "P0 Badge\nP1 Badge"},
                {"user": {"login": "reviewer"}, "body": "P2 Badge"}],
            f"repos/{repo}/pulls/{number}/reviews?per_page=100&page=1": [
                {"user": {"login": "NorbiAI"}, "body": "P1 Badge\nP2 Badge"}],
            bug_endpoint: [{"number": 99, "created_at": bug_created_at,
                            "labels": [{"name": "bug"}], "body": "Fixes #14"}],
        }
        self.make_gh(fixtures)
        path = self.put_fleet_ledger({"id": run_id, "ts": xs.NOW(), "future_field": {"kept": True}})

        self.run_ingest("--repo", repo)

        result = json.loads(path.read_text())
        self.assertEqual(result["pr"], {"repo": repo, "number": number,
                                        "url": f"https://github.com/{repo}/pull/{number}"})
        self.assertEqual(result["review_findings"], {"p0": 1, "p1": 2, "p2": 1})
        self.assertTrue(result["merged"])
        self.assertTrue(result["accepted"])
        self.assertEqual(result["accepted_reason"], "PR merged")
        self.assertTrue(result["defect_within_7d"])
        self.assertEqual(result["future_field"], {"kept": True})

    def test_ingest_preserves_manual_acceptance_and_leaves_open_pr_unset(self):
        manual = {"id": "manual-run", "accepted": False, "accepted_reason": "manual rejection"}
        opened = {"id": "open-run"}
        pulls = {"octo/project": [
            self.matched_pr("octo/project", 61, body="manual-run"),
            self.matched_pr("octo/project", 62, body="open-run"),
        ]}

        def detail(run_id, _repo, _endpoint):
            return {"merged": False, "state": "open" if run_id == "open-run" else "closed"}

        with mock.patch.object(xs, "gh_ingest_api", side_effect=detail), \
                mock.patch.object(xs, "gh_review_findings", return_value={}), \
                mock.patch.object(xs, "gh_has_defect_after_merge", return_value=False):
            xs.ingest_ledger_entry(manual, ["octo/project"], pulls)
            xs.ingest_ledger_entry(opened, ["octo/project"], pulls)

        self.assertFalse(manual["accepted"])
        self.assertEqual(manual["accepted_reason"], "manual rejection")
        self.assertIsNone(opened.get("accepted"))
        self.assertEqual(opened["pr_state"], "open")

    def test_ledger_ingest_paginates_until_updated_at_crosses_since(self):
        repo = "octo/project"
        threshold = xs.NOW() - 3 * 86400
        recent = [self.matched_pr(repo, number, body="no match") for number in range(100)]
        old_time = dt.datetime.fromtimestamp(threshold - 60, dt.timezone.utc).isoformat().replace("+00:00", "Z")
        old_match = self.matched_pr(repo, 101, old_time, body="old-run")
        calls = self.make_gh({self.pull_list_endpoint(repo): recent,
                              self.pull_list_endpoint(repo, 2): [old_match]})
        path = self.put_fleet_ledger({"id": "old-run", "ts": xs.NOW()})

        self.run_ingest("--repo", repo, "--since", "3")

        self.assertIsNone(json.loads(path.read_text()).get("pr"))
        called = calls.read_text().splitlines()
        self.assertEqual(called.count(self.pull_list_endpoint(repo)), 1)
        self.assertEqual(called.count(self.pull_list_endpoint(repo, 2)), 1)
        self.assertFalse(any("/comments?" in endpoint or "/reviews?" in endpoint for endpoint in called))

    def test_ledger_ingest_refresh_reprocesses_filled_records(self):
        repo, run_id = "octo/project", "refresh-run"
        fixtures = {self.pull_list_endpoint(repo): [self.matched_pr(repo, 51, body=run_id)]}
        fixtures.update(self.pr_detail_fixtures(repo, 51))
        calls = self.make_gh(fixtures)
        path = self.put_fleet_ledger({"id": run_id, "ts": xs.NOW(),
                                      "pr": {"repo": repo, "number": 5},
                                      "review_findings": {"p0": 0, "p1": 0, "p2": 0},
                                      "merged": False, "defect_within_7d": None})

        self.run_ingest("--repo", repo, "--refresh")

        self.assertEqual(json.loads(path.read_text())["pr"]["number"], 51)
        self.assertEqual(calls.read_text().splitlines().count(self.pull_list_endpoint(repo)), 1)

    def test_ledger_ingest_rate_limit_saves_progress_and_resume_finishes(self):
        repo = "octo/project"
        first, second = self.matched_pr(repo, 41, body="run-one"), self.matched_pr(repo, 42, body="run-two")
        reset_epoch = xs.NOW() + 3600
        fixtures = {self.pull_list_endpoint(repo): [first, second],
                    "rate_limit": {"resources": {"core": {"remaining": 0, "reset": reset_epoch}}}}
        fixtures.update(self.pr_detail_fixtures(repo, 41))
        fixtures.update(self.pr_detail_fixtures(repo, 42))
        calls = self.make_gh_rate_limited_once(fixtures, fail_after=4)
        path = self.put_fleet_ledger({"id": "run-one", "ts": xs.NOW()},
                                     {"id": "run-two", "ts": xs.NOW()})

        code, _, error = self.run_ingest_result("--repo", repo)

        entries = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual(code, 2)
        self.assertEqual(entries[0]["pr"]["number"], 41)
        self.assertNotIn("pr", entries[1])
        self.assertIn("after 1 runs", error)
        reset = dt.datetime.fromtimestamp(reset_epoch, dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        self.assertIn(reset, error)

        self.run_ingest("--repo", repo)

        entries = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual([entry["pr"]["number"] for entry in entries], [41, 42])
        called = calls.read_text().splitlines()
        self.assertEqual(called.count(self.pull_list_endpoint(repo)), 1)
        self.assertEqual(called.count("rate_limit"), 1)

    def test_ledger_ingest_does_not_suppress_other_github_errors(self):
        repo, run_id = "octo/project", "server-error-run"
        endpoint = self.pull_list_endpoint(repo)
        self.make_gh({endpoint: {"__http_status__": 500, "message": "Internal Server Error"}})
        with mock.patch.dict(os.environ, self.gh_env):
            with self.assertRaisesRegex(xs.BusError, "HTTP 500"):
                xs.linked_pr_for_run({"id": run_id}, repo)


if __name__ == "__main__":
    unittest.main(verbosity=2)
