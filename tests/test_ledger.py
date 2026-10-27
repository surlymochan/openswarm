"""Ledger v2 shape and card type validation use local fixtures only."""
import contextlib
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

    def test_ledger_ingest_links_by_sha_counts_badges_merged_defect_and_is_idempotent(self):
        run_id, repo, sha, number = "sha-run-1", "octo/project", "a" * 40, 14
        merged_at = "2026-10-20T12:00:00Z"
        params = {"state": "all", "labels": "bug", "since": merged_at}
        bug_endpoint = f"repos/{repo}/issues?{urllib.parse.urlencode(params)}&per_page=100&page=1"
        defect_params = {"state": "all", "labels": "defect", "since": merged_at}
        defect_endpoint = f"repos/{repo}/issues?{urllib.parse.urlencode(defect_params)}&per_page=100&page=1"
        fixtures = {
            f"repos/{repo}/commits/{sha}/pulls?per_page=100&page=1": [
                {"number": number, "html_url": f"https://github.com/{repo}/pull/{number}"}],
            f"repos/{repo}/pulls/{number}": {"number": number, "merged": True, "merged_at": merged_at,
                                                   "html_url": f"https://github.com/{repo}/pull/{number}"},
            f"repos/{repo}/pulls/{number}/comments?per_page=100&page=1": [
                {"user": {"login": "chatgpt-codex-connector[bot]"},
                 "body": "P0 Badge\nP1 Badge"},
                {"user": {"login": "reviewer"}, "body": "P2 Badge"}],
            f"repos/{repo}/pulls/{number}/reviews?per_page=100&page=1": [
                {"user": {"login": "NorbiAI"}, "body": "P1 Badge\nP2 Badge"}],
            bug_endpoint: [{"number": 99, "created_at": "2026-10-23T12:00:00Z",
                            "labels": [{"name": "bug"}], "body": "Fixes #14"}],
            defect_endpoint: [],
        }
        calls = self.make_gh(fixtures)
        path = self.put_fleet_ledger({"id": run_id, "run_id": run_id, "ts": xs.NOW(), "commit": sha,
                                      "future_field": {"kept": True}, "pr": None,
                                      "review_findings": None, "merged": None, "defect_within_7d": None})

        self.run_ingest("--repo", repo)
        result = json.loads(path.read_text())
        self.assertEqual(result["pr"], {"repo": repo, "number": number,
                                        "url": f"https://github.com/{repo}/pull/{number}"})
        self.assertEqual(result["review_findings"], {"p0": 1, "p1": 2, "p2": 1})
        self.assertTrue(result["merged"])
        self.assertTrue(result["defect_within_7d"])
        self.assertEqual(result["future_field"], {"kept": True})

        first_contents = path.read_bytes()
        first_calls = calls.read_text().splitlines()
        self.run_ingest("--repo", repo)
        self.assertEqual(path.read_bytes(), first_contents)
        self.assertEqual(calls.read_text().splitlines(), first_calls)

    def test_ledger_ingest_continues_after_missing_local_commit_and_counts_unlinked(self):
        repo = "octo/project"
        missing_run, missing_sha, branch = "local-only-run", "a" * 40, "openswarm/local-only-run"
        linked_run, linked_sha, number = "published-run", "b" * 40, 44
        branch_query = urllib.parse.urlencode({"state": "all", "head": f"octo:{branch}"})
        body_query = "repo:octo/project is:pr in:body local-only-run"
        comment_query = "repo:octo/project is:pr in:comments local-only-run"
        search_body = "search/issues?" + urllib.parse.urlencode({"q": body_query}) + "&per_page=100&page=1"
        search_comment = "search/issues?" + urllib.parse.urlencode({"q": comment_query}) + "&per_page=100&page=1"
        fixtures = {
            f"repos/{repo}/commits/{missing_sha}/pulls?per_page=100&page=1": {
                "__http_status__": 422, "message": "No commit found for SHA"},
            f"repos/{repo}/pulls?{branch_query}&per_page=100&page=1": [],
            search_body: {"items": []},
            search_comment: {"items": []},
            f"repos/{repo}/commits/{linked_sha}/pulls?per_page=100&page=1": [{"number": number}],
            f"repos/{repo}/pulls/{number}": {"number": number, "merged": False},
            f"repos/{repo}/pulls/{number}/comments?per_page=100&page=1": [],
            f"repos/{repo}/pulls/{number}/reviews?per_page=100&page=1": [],
        }
        calls = self.make_gh(fixtures)
        path = self.put_fleet_ledger(
            {"id": missing_run, "ts": xs.NOW(), "branch": branch, "commit": missing_sha},
            {"id": linked_run, "ts": xs.NOW(), "commit": linked_sha},
        )

        output = self.run_ingest("--repo", repo)

        entries = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertIsNone(entries[0].get("pr"))
        self.assertEqual(entries[1]["pr"]["number"], number)
        self.assertIn("ingested 1 PR-linked runs", output)
        self.assertIn("1 unlinked", output)
        called = calls.read_text().splitlines()
        self.assertIn(f"repos/{repo}/pulls?{branch_query}&per_page=100&page=1", called)
        self.assertIn(search_body, called)
        self.assertIn(search_comment, called)

    def test_ledger_ingest_does_not_suppress_other_github_errors(self):
        repo, run_id, sha = "octo/project", "server-error-run", "c" * 40
        endpoint = f"repos/{repo}/commits/{sha}/pulls?per_page=100&page=1"
        self.make_gh({endpoint: {"__http_status__": 500, "message": "Internal Server Error"}})
        with mock.patch.dict(os.environ, self.gh_env):
            with self.assertRaisesRegex(xs.BusError, "HTTP 500"):
                xs.linked_pr_for_run({"id": run_id, "commit": sha}, repo)

    def test_ledger_ingest_links_by_branch(self):
        repo, branch, run_id, number = "octo/project", "openswarm/branch-run", "branch-run", 22
        query = urllib.parse.urlencode({"state": "all", "head": f"octo:{branch}"})
        fixtures = {
            f"repos/{repo}/pulls?{query}&per_page=100&page=1": [{"number": number}],
            f"repos/{repo}/pulls/{number}": {"number": number, "merged": False},
            f"repos/{repo}/pulls/{number}/comments?per_page=100&page=1": [],
            f"repos/{repo}/pulls/{number}/reviews?per_page=100&page=1": [],
        }
        self.make_gh(fixtures)
        path = self.put_fleet_ledger({"id": run_id, "ts": xs.NOW(), "branch": branch})
        self.run_ingest("--repo", repo)
        result = json.loads(path.read_text())
        self.assertEqual(result["pr"]["number"], number)
        self.assertEqual(result["review_findings"], {"p0": 0, "p1": 0, "p2": 0})
        self.assertFalse(result["merged"])
        self.assertIsNone(result["defect_within_7d"])

    def test_ledger_ingest_links_by_run_id_in_issue_comment(self):
        repo, run_id, number = "octo/project", "comment-run", 31
        body_query = "repo:octo/project is:pr in:body comment-run"
        comment_query = "repo:octo/project is:pr in:comments comment-run"
        search_body = "search/issues?" + urllib.parse.urlencode({"q": body_query}) + "&per_page=100&page=1"
        search_comment = "search/issues?" + urllib.parse.urlencode({"q": comment_query}) + "&per_page=100&page=1"
        fixtures = {
            search_body: {"items": []},
            search_comment: {"items": [{"number": number, "pull_request": {"url": "fixture"}}]},
            f"repos/{repo}/issues/{number}/comments?per_page=100&page=1": [{"body": f"Run {run_id}"}],
            f"repos/{repo}/pulls/{number}": {"number": number, "merged": False},
            f"repos/{repo}/pulls/{number}/comments?per_page=100&page=1": [],
            f"repos/{repo}/pulls/{number}/reviews?per_page=100&page=1": [],
        }
        self.make_gh(fixtures)
        path = self.put_fleet_ledger({"id": run_id, "ts": xs.NOW()})
        self.run_ingest("--repo", repo)
        self.assertEqual(json.loads(path.read_text())["pr"]["number"], number)


if __name__ == "__main__":
    unittest.main(verbosity=2)
