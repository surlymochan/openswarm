"""A one-shot review runs separately and discards accidental edits."""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class ReviewOneShotTests(unittest.TestCase):
    def test_copilot_review_discards_edits_even_when_keep_requested(self):
        with tempfile.TemporaryDirectory(prefix="review-run-") as temp:
            root = pathlib.Path(temp)
            repo = root / "repo"
            repo.mkdir()
            def git(*args):
                return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
            git("init", "-q")
            git("config", "user.name", "Test")
            git("config", "user.email", "test@example.invalid")
            (repo / "README.md").write_text("base\n")
            (repo / "verify.sh").write_text("exit 0\n")
            git("add", "README.md", "verify.sh")
            git("commit", "-qm", "base")
            base = git("rev-parse", "HEAD")
            fake = root / "copilot"
            fake.write_text("#!/usr/bin/env python3\nimport json,pathlib,subprocess\n"
                            "pathlib.Path('accidental.txt').write_text('test')\n"
                            "pathlib.Path('verify.sh').write_text('exit 1\\n')\n"
                            "subprocess.run(['git','init','-q','nested'],check=True)\n"
                            "report={'status':'done','summary':'reviewed','files_changed':['accidental.txt'],"
                            "'tests':'none','followups':[]}\n"
                            "print(json.dumps({'type':'assistant.message','data':{'content':json.dumps(report)}}))\n"
                            "print(json.dumps({'type':'result','exitCode':0}))\n")
            fake.chmod(0o755)
            (root / "data").mkdir()
            (root / "data/policy.local.json").write_text(json.dumps({"workspace": {
                "prepare": {"repo": "mkdir -p node_modules && touch node_modules/ready"},
                "prepare_replaces_links": ["node_modules"]}}))
            env = dict(os.environ, OPENSWARM_HOME=str(root / "data"), OPENSWARM_COPILOT_BIN=str(fake),
                       OPENSWARM_STATE_FILE=str(ROOT / "tests/fixtures/state.json"),
                       OPENSWARM_FLEET_GUARD_CONFIG=str(root / "no-guard"))
            run = subprocess.run([sys.executable, str(ROOT / "bin/openswarm"), "run", "--backend", "copilot",
                                  "--difficulty", "3", "--cwd", str(repo), "--read-only", "--no-worktree",
                                  "--keep-worktree", "--verify", "test -f node_modules/ready && test ! -d nested && sh verify.sh", "--prompt", "review"], env=env, capture_output=True,
                                 text=True, timeout=45)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            result = json.loads(run.stdout)
            wt = pathlib.Path(result["worktree"])
            self.assertEqual(result["status"], "done")
            self.assertIsNone(result["commit"])
            self.assertEqual(result["verify"]["rc"], 0)
            self.assertNotEqual(wt, repo)
            self.assertFalse((wt / "accidental.txt").exists())
            self.assertFalse((wt / "nested").exists())
            self.assertFalse((repo / "accidental.txt").exists())
            self.assertEqual(git("rev-parse", "HEAD"), base)


if __name__ == "__main__":
    unittest.main()
