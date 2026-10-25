import json
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
CLI = ROOT / "bin" / "openswarm"
FIXTURES = ROOT / "tests" / "fixtures"


class LaneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        probe_path = f"/tmp/osl-probe-{os.getpid()}.sock"
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.bind(probe_path)
        except PermissionError as e:
            raise unittest.SkipTest(f"host denies AF_UNIX bind required by lane supervisors: {e}")
        finally:
            probe.close()
            try:
                os.unlink(probe_path)
            except OSError:
                pass

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="oslane-", dir="/tmp")
        self.root = pathlib.Path(self.tmp.name)
        self.data = self.root / "data"
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "Lane Test")
        self.git("config", "user.email", "lane-test@example.invalid")
        (self.repo / "README.md").write_text("base\n")
        self.git("add", "README.md")
        self.git("commit", "-q", "-m", "base")
        for path in FIXTURES.glob("fake_*py"):
            path.chmod(0o755)
        self.env = dict(os.environ, OPENSWARM_HOME=str(self.data))
        self.lane_name = "L1"
        self.backend = "codex"
        self.set_config()

    def tearDown(self):
        if self.data.exists():
            try:
                self.cli("lane", "down", self.lane_name, check=False, timeout=45)
            except (OSError, subprocess.TimeoutExpired):
                pass
        self.tmp.cleanup()

    def set_config(self, backend=None):
        if backend:
            self.backend = backend
        binary = FIXTURES / ("fake_app_server.py" if self.backend == "codex" else "fake_pi_rpc.py")
        (self.data / "lanes").mkdir(parents=True, exist_ok=True)
        (self.data / "lanes.json").write_text(json.dumps({"lanes": [{
            "name": self.lane_name, "backend": self.backend, "model": "fake-model",
            **({"effort": "xhigh"} if self.backend == "codex" else {"provider": "fake-provider"}),
            "repo": str(self.repo), "binary": str(binary),
        }]}))

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, check=True, capture_output=True, text=True).stdout.strip()

    def cli(self, *args, check=True, timeout=15):
        result = subprocess.run([sys.executable, str(CLI), *map(str, args)], env=self.env, text=True,
                                capture_output=True, timeout=timeout)
        if check and result.returncode:
            self.fail(f"CLI failed ({result.returncode}): {result.stdout}\n{result.stderr}")
        return result

    def start_lane(self):
        result = self.cli("lane", "up", self.lane_name)
        self.assertTrue(json.loads(result.stdout)["lanes"][0]["state"] in ("idle", "starting"))

    def assign(self, task, *extra):
        result = self.cli("lane", "assign", self.lane_name, "--prompt", task, *extra)
        return json.loads(result.stdout)["id"]

    def wait_for(self, predicate, timeout=12, description="condition"):
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            last = predicate()
            if last:
                return last
            time.sleep(0.05)
        self.fail(f"timed out waiting for {description}; last={last!r}")

    def run_state(self, run_id):
        path = self.data / "runs" / run_id / "status.json"
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return {}

    def wait_run(self, run_id, timeout=12):
        return self.wait_for(lambda: (s if (s := self.run_state(run_id)).get("state") == "finished" else None),
                             timeout=timeout, description=f"run {run_id} to finish")

    def wait_running(self, run_id):
        return self.wait_for(lambda: (s if (s := self.run_state(run_id)).get("state") == "running" else None),
                             description=f"run {run_id} to start")

    def lane_state(self):
        try:
            return json.loads((self.data / "lanes" / self.lane_name / "state.json").read_text())
        except (OSError, ValueError):
            return {}

    def test_assign_commits_and_writes_normal_result(self):
        self.set_config("pi")
        self.start_lane()
        run_id = self.assign("add the fixture change")
        state = self.wait_run(run_id)
        result = json.loads((self.data / "runs" / run_id / "result.json").read_text())
        self.assertEqual(state["status"], "done")
        self.assertEqual(result["lane"], self.lane_name)
        self.assertTrue(result["commit"])
        self.assertEqual(result["branch"], f"openswarm/{run_id}")
        self.assertEqual((self.data / "lanes" / self.lane_name / "wt" / "lane_result.txt").read_text(), "completed")
        self.assertEqual(self.git("show", f"openswarm/{run_id}:lane_result.txt"), "completed")
        self.assertFalse((self.repo / "lane_result.txt").exists())
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertEqual(self.git("-C", str(self.data / "lanes" / self.lane_name / "wt"), "branch", "--show-current"), "")

    def test_codex_assign_commits_and_writes_normal_result(self):
        self.start_lane()
        run_id = self.assign("add the fixture change")
        state = self.wait_run(run_id)
        result = json.loads((self.data / "runs" / run_id / "result.json").read_text())
        self.assertEqual(state["status"], "done")
        self.assertTrue(result["commit"])
        self.assertEqual(result["branch"], f"openswarm/{run_id}")
        self.assertEqual(self.git("show", f"openswarm/{run_id}:lane_result.txt"), "completed")

    def test_steer_reaches_active_codex_turn(self):
        self.start_lane()
        run_id = self.assign("LANE_TEST_WAIT steer me")
        self.wait_running(run_id)
        self.cli("lane", "steer", self.lane_name, "--text", "write steered instruction")
        self.wait_run(run_id)
        self.assertEqual((self.data / "lanes" / self.lane_name / "wt" / "lane_result.txt").read_text(),
                         "write steered instruction")

    def test_steer_reaches_active_pi_turn(self):
        self.set_config("pi")
        self.start_lane()
        run_id = self.assign("LANE_TEST_WAIT steer me")
        self.wait_running(run_id)
        self.cli("lane", "steer", self.lane_name, "--text", "write steered instruction")
        self.wait_run(run_id)
        self.assertEqual((self.data / "lanes" / self.lane_name / "wt" / "lane_result.txt").read_text(),
                         "write steered instruction")

    def test_abort_marks_lane_run_stopped(self):
        self.set_config("pi")
        self.start_lane()
        run_id = self.assign("LANE_TEST_WAIT abort this")
        self.wait_running(run_id)
        self.cli("lane", "abort", self.lane_name)
        state = self.wait_run(run_id)
        self.assertEqual(state["status"], "stopped")
        result = json.loads((self.data / "runs" / run_id / "result.json").read_text())
        self.assertEqual(result["status"], "stopped")

    def test_abort_marks_codex_lane_run_stopped(self):
        self.start_lane()
        run_id = self.assign("LANE_TEST_WAIT abort this")
        self.wait_running(run_id)
        self.cli("lane", "abort", self.lane_name)
        state = self.wait_run(run_id)
        self.assertEqual(state["status"], "stopped")
        result = json.loads((self.data / "runs" / run_id / "result.json").read_text())
        self.assertEqual(result["status"], "stopped")

    def test_stop_command_aborts_lane_run_without_killing_supervisor(self):
        self.set_config("pi")
        self.start_lane()
        run_id = self.assign("LANE_TEST_WAIT stop this")
        self.wait_running(run_id)
        supervisor_pid = self.lane_state()["supervisor_pid"]
        self.cli("stop", run_id)
        self.assertEqual(self.wait_run(run_id)["status"], "stopped")
        self.assertTrue(process_alive(supervisor_pid))

    def test_budget_interrupt_is_recorded(self):
        self.start_lane()
        run_id = self.assign("LANE_TEST_WAIT exceed the budget", "--budget-minutes", "0.02")
        state = self.wait_run(run_id)
        self.assertEqual(state["status"], "budget_exceeded")
        events = (self.data / "runs" / run_id / "events.1.codex.jsonl").read_text()
        self.assertIn("turn/completed", events)

    def test_crashed_child_fails_run_and_restarts_idle(self):
        self.start_lane()
        run_id = self.assign("LANE_TEST_CRASH")
        state = self.wait_run(run_id)
        self.assertEqual(state["status"], "failed")
        result = json.loads((self.data / "runs" / run_id / "result.json").read_text())
        self.assertEqual(result["attempts"][0]["error_class"], "exec_error")
        restarted = self.wait_for(lambda: (s if s.get("state") == "idle" and s.get("child_pid") else None)
                                   if (s := self.lane_state()) else None,
                                   timeout=8, description="lane child restart")
        self.assertTrue(restarted["child_pid"])

    def test_busy_lane_refuses_second_assignment_and_down_leaves_no_process(self):
        self.start_lane()
        first = self.assign("LANE_TEST_WAIT keep busy")
        self.wait_running(first)
        second = self.cli("lane", "assign", self.lane_name, "--prompt", "should be rejected", check=False)
        self.assertNotEqual(second.returncode, 0)
        self.assertIn("busy", json.loads(second.stdout)["error"])
        supervisor_pid = self.lane_state()["supervisor_pid"]
        child_pid = self.lane_state()["child_pid"]
        self.cli("lane", "down", self.lane_name, timeout=45)
        self.wait_run(first)
        for pid in (supervisor_pid, child_pid):
            with self.subTest(pid=pid):
                self.wait_for(lambda: not process_alive(pid), timeout=5, description=f"pid {pid} to exit")


def process_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


if __name__ == "__main__":
    unittest.main()
