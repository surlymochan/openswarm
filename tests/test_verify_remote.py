import argparse
import contextlib
import io
import json
from pathlib import Path
import unittest

import _load
from test_batch import FakeFleetFixture, xs


class VerifyRemoteTests(FakeFleetFixture, unittest.TestCase):
    def start_source_run(self):
        card = self.cards / "card.md"
        card.write_text("verify this change")
        source = self.hosts["b1"]
        rid = xs.host_json(source, ["run", "--detach", "--cwd", source["repos"]["project"],
                                    "--task-file", str(card), "--base", self.initial])["id"]
        xs.host_json(source, ["fleet", "_run-info", rid])
        result = xs.host_json(source, ["fleet", "_run-info", rid])["result"]
        return rid, result

    def verify(self, rid, target, command):
        args = argparse.Namespace(from_host="b1", run_id=rid, on_host=target, verify=command, timeout=10)
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as exited:
            xs.cmd_verify_remote(args)
        return exited.exception.code, json.loads(output.getvalue())

    def test_verify_locally_links_dependencies_and_preserves_checkouts(self):
        rid, original = self.start_source_run()
        (self.repo / "node_modules").mkdir()
        (self.repo / "node_modules" / "dependency").write_text("dependency")
        before = self.git(self.repo, "worktree", "list", "--porcelain")
        rc, reply = self.verify(rid, "local", f"test -f {rid}.txt && test -f node_modules/dependency && "
                               "test -z \"$(git status --porcelain)\"")
        self.assertEqual(rc, 0, reply)
        result = json.loads((self.data / "remote-results" / (rid + ".json")).read_text())
        self.assertEqual(result["commit"], original["commit"])
        self.assertEqual(result["verify_remote"]["host"], "local")
        self.assertEqual(result["verify_remote"]["rc"], 0)
        self.assertEqual(result["verify_remote"]["failing"], [])
        self.assertGreater(result["verify_remote"]["seconds"], 0)
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD"), self.base)
        self.assertFalse((self.repo / (rid + ".txt")).exists())
        self.assertEqual(self.git(self.repo, "worktree", "list", "--porcelain"), before)
        self.assertEqual(self.git(self.repo, "for-each-ref", "refs/openswarm-verify"), "")
        source = self.root / "b1" / "repo"
        self.assertEqual(self.git(source, "rev-parse", "openswarm/" + rid), original["commit"])
        self.assertEqual(json.loads((self.root / "b1" / "data" / "runs" / rid / "result.json").read_text()), original)

    def test_verify_on_second_host_records_failure_without_printing_output(self):
        rid, original = self.start_source_run()
        self.hosts["b2"]["openswarm"] = str(Path(_load.ROOT) / "bin" / "openswarm")
        (self.data / "fleet.json").write_text(json.dumps({"hosts": list(self.hosts.values())}))
        target = self.root / "b2" / "repo"
        before = self.git(target, "worktree", "list", "--porcelain")
        rc, reply = self.verify(rid, "b2", "printf 'FAILED test_case\nprivate-output\n'; exit 3")
        self.assertEqual(rc, 1)
        self.assertEqual(reply["verify_remote"]["rc"], 3)
        self.assertEqual(reply["verify_remote"]["host"], "b2")
        self.assertEqual(reply["verify_remote"]["failing"], ["test_case"])
        self.assertNotIn("private-output", json.dumps(reply))
        self.assertEqual(self.git(target, "cat-file", "-t", original["commit"]), "commit")
        self.assertEqual(self.git(target, "rev-parse", "HEAD"), self.initial)
        self.assertEqual(self.git(target, "worktree", "list", "--porcelain"), before)
        self.assertEqual(self.git(target, "for-each-ref", "refs/openswarm-verify"), "")
        self.assertEqual(xs.sh.call_count, 0)

    def test_branchless_run_is_rejected_without_creating_result(self):
        rid, _ = self.start_source_run()
        path = self.root / "b1" / "data" / "runs" / rid / "result.json"
        result = json.loads(path.read_text())
        result["branch"] = None
        result["commit"] = None
        path.write_text(json.dumps(result))
        with self.assertRaisesRegex(ValueError, "no completed branch"):
            xs.cmd_verify_remote(argparse.Namespace(from_host="b1", run_id=rid, on_host="local", verify="true", timeout=10))
        self.assertFalse((self.data / "remote-results" / (rid + ".json")).exists())

    def test_verify_after_batch_has_removed_source_branch(self):
        (self.cards / "card.md").write_text("collected card")
        rc, summary, _ = self.run_batch()
        self.assertEqual(rc, 0)
        row = summary["cards"][0]
        self.assertEqual(self.git(self.root / "b1" / "repo", "branch", "--list", row["branch"]), "")
        rc, reply = self.verify(row["run_id"], "local", f"test -f {row['run_id']}.txt")
        self.assertEqual(rc, 0, reply)


if __name__ == "__main__":
    unittest.main()
