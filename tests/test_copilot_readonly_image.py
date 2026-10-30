"""Exercise a Copilot lane against an actual macOS read-only image."""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
CLI = ROOT / "bin/openswarm"
FAKE = ROOT / "tests/fixtures/fake_cursor_acp.py"


@unittest.skipUnless(sys.platform == "darwin" and shutil.which("hdiutil"), "requires macOS hdiutil")
class CopilotReadOnlyImageTests(unittest.TestCase):
    def test_lane_runs_on_readonly_snapshot_and_retains_resident(self):
        with tempfile.TemporaryDirectory(prefix="os-ro-", dir="/tmp") as temp:
            root = pathlib.Path(temp)
            repo, home = root / "repo", root / "home"
            repo.mkdir()
            home.mkdir()
            def git(*args):
                return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout
            git("init", "-q")
            git("config", "user.name", "Test")
            git("config", "user.email", "test@example.invalid")
            (repo / "README.md").write_text("synthetic\n")
            git("add", "README.md")
            git("commit", "-qm", "base")
            FAKE.chmod(0o755)
            (home / "lanes.json").write_text(json.dumps({"lanes": [{"name": "C1", "backend": "copilot",
                "model": "claude-haiku-5.5", "repo": str(repo), "binary": str(FAKE)}]}))
            env = dict(os.environ, OPENSWARM_HOME=str(home), OPENSWARM_FLEET_GUARD_CONFIG=str(root / "no-guard"),
                       FAKE_CURSOR_ACP_MODELS=json.dumps(["claude-haiku-5.5"]), FAKE_CURSOR_READONLY_PROBE="1")
            def cli(*args, check=True):
                result = subprocess.run([sys.executable, str(CLI), *args], env=env, capture_output=True,
                                        text=True, timeout=45)
                if check and result.returncode:
                    self.fail(result.stdout[-500:] + result.stderr[-500:])
                return result
            try:
                cli("lane", "up", "C1")
                state_path = home / "lanes/C1/state.json"
                resident = json.loads(state_path.read_text())["child_pid"]
                run_id = json.loads(cli("lane", "assign", "C1", "--read-only", "--prompt", "Review README.md").stdout)["id"]
                run = home / "runs" / run_id
                deadline = time.monotonic() + 90
                while time.monotonic() < deadline:
                    if (run / "result.json").exists():
                        result = json.loads((run / "result.json").read_text())
                        break
                    time.sleep(.1)
                else:
                    self.fail("read-only Copilot run did not finish")
                self.assertEqual(result["status"], "done", result)
                self.assertEqual(result["result"]["summary"], "write blocked")
                self.assertIsNone(result["commit"])
                self.assertFalse((home / "lanes/C1/wt-readonly/unexpected-write").exists())
                self.assertFalse(list(run.glob("copilot-readonly-*")))
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    state = json.loads(state_path.read_text())
                    if state.get("state") == "idle":
                        break
                    time.sleep(.1)
                self.assertEqual(state["state"], "idle")
                self.assertEqual(state["child_pid"], resident)
            finally:
                cli("lane", "down", "C1", check=False)


if __name__ == "__main__":
    unittest.main()
