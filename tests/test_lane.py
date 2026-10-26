import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
CLI = ROOT / "bin" / "openswarm"
FIXTURES = ROOT / "tests" / "fixtures"


class LaneTests(unittest.TestCase):
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

    def set_config(self, backend=None, model=None):
        if backend:
            self.backend = backend
        fixture = {"codex": "fake_app_server.py", "cursor": "fake_cursor_acp.py", "pi": "fake_pi_rpc.py"}[self.backend]
        binary = FIXTURES / fixture
        if model is None:
            model = "composer-2.5" if self.backend == "cursor" else "fake-model"
        (self.data / "lanes").mkdir(parents=True, exist_ok=True)
        (self.data / "lanes.json").write_text(json.dumps({"lanes": [{
            "name": self.lane_name, "backend": self.backend, "model": model,
            **({"effort": "xhigh"} if self.backend == "codex" else
               ({"provider": "fake-provider"} if self.backend == "pi" else {})),
            "repo": str(self.repo), "binary": str(binary),
        }]}))
        if self.backend == "cursor":
            self.env["FAKE_CURSOR_ACP_LOG"] = str(self.root / "fake-cursor-acp.jsonl")

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

    def cursor_log(self):
        path = pathlib.Path(self.env["FAKE_CURSOR_ACP_LOG"])
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines()]

    def test_cursor_assign_selects_model_and_commits_without_verify(self):
        self.set_config("cursor")
        config_path = self.data / "lanes.json"
        config = json.loads(config_path.read_text())
        config["lanes"][0].pop("model")
        config_path.write_text(json.dumps(config))
        self.start_lane()
        run_id = self.assign("add the cursor fixture change")
        state = self.wait_run(run_id)
        result = json.loads((self.data / "runs" / run_id / "result.json").read_text())
        self.assertEqual(state["status"], "done")
        self.assertIsNone(result["verify"])
        self.assertTrue(result["commit"])
        self.assertEqual(result["branch"], f"openswarm/{run_id}")
        self.assertIn("add the cursor fixture change",
                      (self.data / "lanes" / self.lane_name / "wt" / "lane_result.txt").read_text())
        methods = [entry.get("method") for entry in self.cursor_log()]
        self.assertIn("session/new", methods)
        self.assertIn("session/set_config_option", methods)
        self.assertIn("session/prompt", methods)
        argv = next(entry["argv"] for entry in self.cursor_log() if "argv" in entry)
        self.assertEqual(argv[1:], ["acp"])
        self.assertEqual(json.loads((self.data / "lanes" / "L1" / "state.json").read_text())["model"],
                         "composer-2.5")
        choice = next(entry for entry in self.cursor_log() if entry.get("method") == "session/set_config_option")
        self.assertEqual(choice["params"]["value"], "composer-2.5")
        initialize = next(entry for entry in self.cursor_log() if entry.get("method") == "initialize")
        self.assertEqual(initialize["params"]["clientCapabilities"], {})
        new_session = next(entry for entry in self.cursor_log() if entry.get("method") == "session/new")
        self.assertEqual(new_session["params"]["mcpServers"], [])
        self.assertEqual(self.git("show", f"openswarm/{run_id}:lane_result.txt"),
                         (self.data / "lanes" / self.lane_name / "wt" / "lane_result.txt").read_text().rstrip("\n"))
        watched = self.cli("watch", run_id).stdout
        self.assertIn("Plan: Apply the requested change", watched)
        self.assertIn("CMD: Edit lane file", watched)
        self.assertIn("MSG:", watched)
        ledger = [json.loads(line) for line in (self.data / "ledger.jsonl").read_text().splitlines()]
        self.assertTrue(any(entry["id"] == run_id and entry["status"] == "done" for entry in ledger))

    def test_cursor_systemd_unit_is_printed_not_installed(self):
        self.set_config("cursor")
        unit = self.cli("lane", "systemd-unit", self.lane_name).stdout
        self.assertIn("[Service]", unit)
        self.assertIn("User=", unit)
        self.assertIn("Restart=always", unit)
        self.assertIn("MemoryMax=900M", unit)
        self.assertIn("CPUQuota=150%", unit)
        self.assertIn("Nice=10", unit)
        self.assertIn(os.path.expanduser("~/.local/bin"), unit)
        self.assertIn("lane\" \"serve\" \"L1", unit)

    def test_cursor_model_missing_fails_with_available_values(self):
        self.set_config("cursor", model="missing-model")
        self.start_lane()
        run_id = self.assign("should fail model selection")
        state = self.wait_run(run_id)
        self.assertEqual(state["status"], "failed")
        events = (self.data / "runs" / run_id / "events.1.cursor.jsonl").read_text()
        self.assertIn("composer-2.5", events)
        self.assertIn("other-model", events)
        self.assertNotIn("session/prompt", [entry.get("method") for entry in self.cursor_log()])

    def test_cursor_base_model_resolves_parameterized_value(self):
        self.set_config("cursor")
        resolved = "composer-2.5[fast=true]"
        self.env["FAKE_CURSOR_ACP_MODELS"] = json.dumps([resolved, "other-model"])
        self.start_lane()
        run_id = self.assign("resolve the base model")
        self.assertEqual(self.wait_run(run_id)["status"], "done")
        result = json.loads((self.data / "runs" / run_id / "result.json").read_text())
        self.assertEqual(result["model"], resolved)
        self.assertEqual(result["resolved_model"], resolved)
        self.assertEqual(self.lane_state()["model"], resolved)
        choice = next(entry for entry in self.cursor_log() if entry.get("method") == "session/set_config_option")
        self.assertEqual(choice["params"]["value"], resolved)

    def test_cursor_exact_model_wins_over_base_matches(self):
        self.set_config("cursor")
        self.env["FAKE_CURSOR_ACP_MODELS"] = json.dumps(["composer-2.5[fast=true]", "composer-2.5"])
        self.start_lane()
        run_id = self.assign("prefer the exact model")
        self.assertEqual(self.wait_run(run_id)["status"], "done")
        choice = next(entry for entry in self.cursor_log() if entry.get("method") == "session/set_config_option")
        self.assertEqual(choice["params"]["value"], "composer-2.5")

    def test_cursor_ambiguous_base_model_fails_with_matches(self):
        self.set_config("cursor")
        values = ["composer-2.5[fast=true]", "composer-2.5[fast=false]"]
        self.env["FAKE_CURSOR_ACP_MODELS"] = json.dumps(values)
        self.start_lane()
        run_id = self.assign("reject ambiguous model")
        self.assertEqual(self.wait_run(run_id)["status"], "failed")
        events = (self.data / "runs" / run_id / "events.1.cursor.jsonl").read_text()
        self.assertIn("ambiguous", events)
        for value in values:
            self.assertIn(value, events)
        self.assertNotIn("session/prompt", [entry.get("method") for entry in self.cursor_log()])

    def test_cursor_permission_is_allowed_and_logged(self):
        self.set_config("cursor")
        self.start_lane()
        run_id = self.assign("LANE_TEST_PERMISSION make the change")
        self.assertEqual(self.wait_run(run_id)["status"], "done")
        events = [json.loads(line) for line in
                  (self.data / "runs" / run_id / "events.1.cursor.jsonl").read_text().splitlines()]
        permission = next(event for event in events if event.get("type") == "permission_request")
        self.assertEqual(permission["selected"], "allow_always")
        reply = next(event["permission_response"] for event in self.cursor_log() if "permission_response" in event)
        self.assertEqual(reply["result"]["outcome"], {"outcome": "selected", "optionId": "always"})

    def test_cursor_steer_cancels_then_continues_same_session(self):
        self.set_config("cursor")
        self.start_lane()
        run_id = self.assign("LANE_TEST_WAIT original task")
        self.wait_running(run_id)
        self.cli("lane", "steer", self.lane_name, "--text", "write steered instruction")
        self.assertEqual(self.wait_run(run_id)["status"], "done")
        log = self.cursor_log()
        sessions = [entry for entry in log if entry.get("method") == "session/new"]
        prompts = [entry for entry in log if entry.get("method") == "session/prompt"]
        self.assertEqual(len(sessions), 1)
        self.assertEqual(len(prompts), 2)
        self.assertEqual(prompts[0]["params"]["sessionId"], prompts[1]["params"]["sessionId"])
        correction = prompts[1]["params"]["prompt"][0]["text"]
        self.assertIn("Correction to the interrupted task", correction)
        self.assertIn("write steered instruction", correction)
        # The follow-up prompt replaces the cancelled one, so it has to restate the report
        # requirement; without it the agent ends the turn without printing the JSON report.
        self.assertIn("your FINAL message must be ONLY a JSON object", correction)
        self.assertLess(correction.index("write steered instruction"),
                        correction.index("your FINAL message must be ONLY a JSON object"))
        self.assertTrue(any(entry.get("method") == "session/cancel" for entry in log))
        events = (self.data / "runs" / run_id / "events.1.cursor.jsonl").read_text()
        self.assertIn('"mode": "cancel-and-continue"', events)
        self.assertIn("write steered instruction",
                      (self.data / "lanes" / self.lane_name / "wt" / "lane_result.txt").read_text())

    def test_cursor_turn_without_report_is_partial(self):
        self.set_config("cursor")
        self.start_lane()
        run_id = self.assign("LANE_TEST_NO_REPORT make the change")
        state = self.wait_run(run_id)
        result = json.loads((self.data / "runs" / run_id / "result.json").read_text())
        self.assertEqual(state["status"], "partial")
        self.assertEqual(result["status"], "partial")
        self.assertIsNone(result["result"])
        self.assertEqual(result["attempts"][0]["error_class"], "no_report")
        self.assertIn("executor ended without a final report", result["verify_note"])
        self.assertTrue(result["commit"])
        self.assertIn("lane_result.txt", self.git("ls-tree", "--name-only", f"openswarm/{run_id}"))
        ledger = [json.loads(line) for line in (self.data / "ledger.jsonl").read_text().splitlines()]
        self.assertTrue(any(entry["id"] == run_id and entry["status"] == "partial" for entry in ledger))

    def test_cursor_turn_without_report_honours_verify(self):
        self.set_config("cursor")
        self.start_lane()
        run_id = self.assign("LANE_TEST_NO_REPORT make the change", "--verify", "true")
        state = self.wait_run(run_id)
        result = json.loads((self.data / "runs" / run_id / "result.json").read_text())
        self.assertEqual(state["status"], "done")
        self.assertIn("executor ended without a final report", result["verify_note"])

    def test_cursor_turn_without_report_or_change_is_failed(self):
        self.set_config("cursor")
        self.start_lane()
        run_id = self.assign("LANE_TEST_NO_REPORT LANE_TEST_NO_CHANGE leave the tree alone")
        state = self.wait_run(run_id)
        result = json.loads((self.data / "runs" / run_id / "result.json").read_text())
        self.assertEqual(state["status"], "failed")
        self.assertEqual(result["status"], "failed")
        self.assertIsNone(result["commit"])

    def test_cursor_abort_marks_run_stopped(self):
        self.set_config("cursor")
        self.start_lane()
        run_id = self.assign("LANE_TEST_WAIT abort this")
        self.wait_running(run_id)
        self.cli("lane", "abort", self.lane_name)
        state = self.wait_run(run_id)
        result = json.loads((self.data / "runs" / run_id / "result.json").read_text())
        self.assertEqual(state["status"], "stopped")
        self.assertEqual(result["status"], "stopped")

    def test_cursor_budget_interrupt_is_recorded(self):
        self.set_config("cursor")
        self.start_lane()
        run_id = self.assign("LANE_TEST_WAIT exceed the budget", "--budget-minutes", "0.02")
        self.assertEqual(self.wait_run(run_id)["status"], "budget_exceeded")
        self.assertTrue(any(entry.get("method") == "session/cancel" for entry in self.cursor_log()))

    def test_cursor_crash_fails_run_and_restarts_child(self):
        self.set_config("cursor")
        self.start_lane()
        run_id = self.assign("LANE_TEST_CRASH")
        state = self.wait_run(run_id)
        result = json.loads((self.data / "runs" / run_id / "result.json").read_text())
        self.assertEqual(state["status"], "failed")
        self.assertEqual(result["attempts"][0]["error_class"], "exec_error")
        restarted = self.wait_for(lambda: (s if s.get("state") == "idle" and s.get("child_pid") else None)
                                   if (s := self.lane_state()) else None,
                                   timeout=8, description="Cursor ACP child restart")
        self.assertTrue(restarted["child_pid"])

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
