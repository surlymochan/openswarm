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
        self.env["FAKE_PI_RPC_LOG"] = str(self.root / "fake-pi-rpc.jsonl")
        self.lane_name = "L1"
        self.backend = "codex"
        self.set_config()

    def tearDown(self):
        if self.data.exists():
            try:
                self.cli("lane", "down", check=False, timeout=45)
            except (OSError, subprocess.TimeoutExpired):
                pass
        self.tmp.cleanup()

    def set_config(self, backend=None, model=None, fallback_models=None):
        if backend:
            self.backend = backend
        fixture = {"codex": "fake_app_server.py", "cursor": "fake_cursor_acp.py",
                   "agy": "fake_agy.py", "pi": "fake_pi_rpc.py"}[self.backend]
        binary = FIXTURES / fixture
        if model is None:
            model = "composer-2.5" if self.backend == "cursor" else (
                "gemini-3.8-flash-medium" if self.backend == "agy" else "fake-model")
        (self.data / "lanes").mkdir(parents=True, exist_ok=True)
        (self.data / "lanes.json").write_text(json.dumps({"lanes": [{
            "name": self.lane_name, "backend": self.backend, "model": model,
            **({"fallback_models": fallback_models} if fallback_models is not None else {}),
            **({"effort": "xhigh"} if self.backend == "codex" else
               ({"provider": "fake-provider"} if self.backend == "pi" else {})),
            "repo": str(self.repo), "binary": str(binary),
        }]}))
        if self.backend == "cursor":
            self.env["FAKE_CURSOR_ACP_LOG"] = str(self.root / "fake-cursor-acp.jsonl")
        if self.backend == "agy":
            self.env["FAKE_AGY_LOG"] = str(self.root / "fake-agy.jsonl")

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
        knowledge = self.root / "knowledge"
        knowledge.mkdir()
        (knowledge / "card-types.yaml").write_text("card_types:\n  - feature\n")
        (knowledge / "lessons").mkdir()
        (knowledge / "lessons" / "L-0200.yaml").write_text(
            "id: L-0200\nrule: Keep lane edits covered by tests.\nguard_level: L2\n"
            "applies_to:\n  card_types: [feature]\n  paths: [src/*.py]\n  backends: [pi]\n")
        (self.data / "policy.local.json").write_text(json.dumps({"knowledge": {"repo": str(knowledge)}}))
        self.start_lane()
        run_id = self.assign("add the fixture change", "--card-type", "feature", "--paths", "src/lane.py",
                             "--rework-of", "parent-run")
        state = self.wait_run(run_id)
        result = json.loads((self.data / "runs" / run_id / "result.json").read_text())
        self.assertEqual(state["status"], "done")
        self.assertEqual(result["lane"], self.lane_name)
        self.assertTrue(result["commit"])
        self.assertEqual(result["branch"], f"openswarm/{run_id}")
        ledger = [json.loads(line) for line in (self.data / "ledger.jsonl").read_text().splitlines()]
        entry = next(row for row in ledger if row["id"] == run_id)
        self.assertEqual(entry["card_type"], "feature")
        self.assertEqual(entry["rework_of"], "parent-run")
        self.assertEqual(entry["lessons_injected"], ["L-0200"])
        self.assertEqual(entry["area"], ["lane_result.txt"])
        messages = [json.loads(line) for line in pathlib.Path(self.env["FAKE_PI_RPC_LOG"]).read_text().splitlines()]
        prompt = next(row["message"] for row in messages if row.get("type") == "prompt")
        self.assertIn("[L-0200] Keep lane edits covered by tests.", prompt)
        self.assertEqual((self.data / "lanes" / self.lane_name / "wt" / "lane_result.txt").read_text(), "completed")
        self.assertEqual(self.git("show", f"openswarm/{run_id}:lane_result.txt"), "completed")
        self.assertFalse((self.repo / "lane_result.txt").exists())
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertEqual(self.git("-C", str(self.data / "lanes" / self.lane_name / "wt"), "branch", "--show-current"), "")

    def test_assignment_refreshes_head_and_records_workspace_note(self):
        self.start_lane()
        (self.repo / "package.json").write_text('{"workspaces": ["packages/*"]}\n')
        (self.repo / "bun.lock").write_text("# test lock\n")
        (self.repo / "after-lane-start.txt").write_text("new repo head\n")
        self.git("add", "package.json", "bun.lock", "after-lane-start.txt")
        self.git("commit", "-q", "-m", "move lane base")
        head = self.git("rev-parse", "HEAD")

        run_id = self.assign("refresh to current repo head")
        self.wait_run(run_id)
        status = self.run_state(run_id)
        result = json.loads((self.data / "runs" / run_id / "result.json").read_text())
        self.assertEqual(result["workspace"]["base"], head)
        self.assertIn("Bun workspace detected", result["workspace_note"])
        self.assertEqual(status["workspace_note"], result["workspace_note"])
        self.assertTrue((self.data / "lanes" / self.lane_name / "wt" / "after-lane-start.txt").exists())

    def test_assignment_rebuilds_a_broken_lane_worktree(self):
        self.start_lane()
        wt = self.data / "lanes" / self.lane_name / "wt"
        (wt / ".git").unlink()
        (wt / "stale-worktree-file").write_text("discarded\n")
        expected_head = self.git("rev-parse", "HEAD")

        run_id = self.assign("rebuild broken lane worktree")
        self.assertEqual(self.wait_run(run_id)["status"], "done")
        result = json.loads((self.data / "runs" / run_id / "result.json").read_text())
        self.assertEqual(result["workspace"]["base"], expected_head)
        self.assertFalse((wt / "stale-worktree-file").exists())

    def test_assignment_rebuilds_a_missing_lane_worktree(self):
        self.start_lane()
        wt = self.data / "lanes" / self.lane_name / "wt"
        shutil.rmtree(wt)
        expected_head = self.git("rev-parse", "HEAD")

        run_id = self.assign("rebuild missing lane worktree")
        self.assertEqual(self.wait_run(run_id)["status"], "done")
        result = json.loads((self.data / "runs" / run_id / "result.json").read_text())
        self.assertEqual(result["workspace"]["base"], expected_head)
        self.assertTrue(wt.is_dir())

    def test_pi_rate_limit_retries_in_session_and_watch_shows_alert(self):
        self.set_config("pi")
        (self.data / "policy.local.json").write_text(json.dumps({"rate_limit": {
            "backoff_seconds": [0], "max_retries": 1}}))
        self.start_lane()
        run_id = self.assign("LANE_TEST_RATE_LIMIT_ONCE resume after provider limit")
        self.assertEqual(self.wait_run(run_id)["status"], "done")
        result = json.loads((self.data / "runs" / run_id / "result.json").read_text())
        events = [json.loads(line) for path in (self.data / "runs" / run_id).glob("events.*.jsonl")
                  for line in path.read_text().splitlines()]
        self.assertEqual(sum(event.get("type") == "RATELIMIT" for event in events), 1)
        messages = [json.loads(line) for line in pathlib.Path(self.env["FAKE_PI_RPC_LOG"]).read_text().splitlines()]
        self.assertEqual(sum(row.get("type") == "new_session" for row in messages), 1)
        prompts = [row["message"] for row in messages if row.get("type") == "prompt"]
        self.assertEqual(len(prompts), 2)
        self.assertEqual(prompts[1], "You were interrupted by a provider rate limit. Continue the task from where you stopped; finish with the final JSON report.")
        watched = self.cli("watch", run_id, "--quiet").stdout
        self.assertIn("RATELIMIT:", watched)
        self.assertEqual(result["status"], "done")

    def test_repeated_rate_limit_keeps_partial_work_with_note(self):
        self.set_config("pi")
        (self.data / "policy.local.json").write_text(json.dumps({"rate_limit": {
            "backoff_seconds": [0], "max_retries": 1}}))
        self.start_lane()
        run_id = self.assign("LANE_TEST_RATE_LIMIT_PARTIAL keep partial changes")
        self.assertEqual(self.wait_run(run_id)["status"], "partial")
        result = json.loads((self.data / "runs" / run_id / "result.json").read_text())
        self.assertEqual(result["verify_note"], "rate limited")
        self.assertEqual(result["error_class"], "rate_limit")
        self.assertIsNotNone(result["commit"])
        empty_run = self.assign("LANE_TEST_RATE_LIMIT_ALWAYS no changes")
        self.assertEqual(self.wait_run(empty_run)["status"], "failed")
        empty_result = json.loads((self.data / "runs" / empty_run / "result.json").read_text())
        self.assertEqual(empty_result["verify_note"], "rate limited")
        self.assertIsNone(empty_result["commit"])

    def test_provider_cap_queues_second_lane_until_slot_frees(self):
        self.set_config("pi")
        config_path = self.data / "lanes.json"
        config = json.loads(config_path.read_text())
        config["lanes"].append(dict(config["lanes"][0], name="L2"))
        config_path.write_text(json.dumps(config))
        (self.data / "policy.local.json").write_text(json.dumps({"concurrency": {"fake-provider": 1}}))
        self.cli("lane", "up", "L1")
        self.cli("lane", "up", "L2")
        first = self.assign("LANE_TEST_WAIT_SHORT first provider task")
        self.wait_running(first)
        second = json.loads(self.cli("lane", "assign", "L2", "--prompt", "second provider task").stdout)["id"]
        queued = self.wait_for(lambda: (state if (state := self.run_state(second)).get("queue_reason") else None),
                               description="second provider turn to queue")
        self.assertEqual(queued["queue_reason"], "provider concurrency limit")
        lanes = {row["name"]: row for row in json.loads(self.cli("lane", "status").stdout)}
        self.assertEqual(lanes["L2"]["run_state"], "queued")
        self.assertEqual(self.wait_run(first)["status"], "done")
        self.assertEqual(self.wait_run(second)["status"], "done")

    def test_total_cap_queues_different_backends_until_slot_frees(self):
        self.set_config("pi")
        config_path = self.data / "lanes.json"
        config = json.loads(config_path.read_text())
        second_lane = dict(config["lanes"][0], name="L2", backend="codex", model="fake-model", effort="xhigh")
        second_lane.pop("provider", None)
        second_lane["binary"] = str(FIXTURES / "fake_app_server.py")
        config["lanes"].append(second_lane)
        config_path.write_text(json.dumps(config))
        (self.data / "policy.local.json").write_text(json.dumps({"concurrency": {"total": 1}}))
        self.cli("lane", "up", "L1")
        self.cli("lane", "up", "L2")
        first = self.assign("LANE_TEST_WAIT_SHORT first total-cap task")
        self.wait_running(first)
        second = json.loads(self.cli("lane", "assign", "L2", "--prompt", "second backend task").stdout)["id"]
        queued = self.wait_for(lambda: (state if (state := self.run_state(second)).get("queue_reason") else None),
                               description="second backend turn to queue on total cap")
        self.assertEqual(queued["queue_reason"], "provider concurrency limit")
        self.assertEqual(self.wait_run(first)["status"], "done")
        self.assertEqual(self.wait_run(second)["status"], "done")

    def test_codex_assign_commits_and_writes_normal_result(self):
        self.start_lane()
        run_id = self.assign("add the fixture change")
        state = self.wait_run(run_id)
        result = json.loads((self.data / "runs" / run_id / "result.json").read_text())
        self.assertEqual(state["status"], "done")
        self.assertTrue(result["commit"])
        self.assertEqual(result["branch"], f"openswarm/{run_id}")
        self.assertEqual(self.git("show", f"openswarm/{run_id}:lane_result.txt"), "completed")

    def test_pi_session_leak_is_excluded_even_when_staged(self):
        self.set_config("pi")
        self.start_lane()
        run_id = self.assign("LANE_TEST_SESSION_LEAK")
        self.assertEqual(self.wait_run(run_id)["status"], "done")
        result = json.loads((self.data / "runs" / run_id / "result.json").read_text())
        artifact = "2026-10-26T10-17-04-024Z_12345678-1234-1234-1234-123456789abc.jsonl"
        self.assertEqual(result["excluded_files"], [artifact])
        self.assertNotIn(artifact, self.git("ls-tree", "-r", "--name-only", result["commit"]))
        self.assertTrue((self.data / "lanes" / self.lane_name / "pi-sessions").is_dir())

    def test_budget_refuses_lane_assignment(self):
        self.set_config("pi")
        (self.data / "policy.local.json").write_text(json.dumps({"budget": {"daily_executor_minutes": 0}}))
        self.start_lane()
        response = self.cli("lane", "assign", self.lane_name, "--prompt", "must not start", check=False)
        self.assertEqual(response.returncode, 3, response.stderr)
        self.assertEqual(json.loads(response.stdout)["error"], "budget_exceeded_daily")
        self.assertEqual(self.lane_state()["state"], "idle")

    def test_risk_aborts_lane_turns_without_watch(self):
        for backend in ("pi", "codex", "cursor"):
            with self.subTest(backend=backend):
                self.set_config(backend)
                self.start_lane()
                run_id = self.assign("LANE_TEST_RISK LANE_TEST_WAIT", "--verify", "true")
                self.assertEqual(self.wait_run(run_id)["status"], "stopped")
                result = json.loads((self.data / "runs" / run_id / "result.json").read_text())
                self.assertEqual(result["error_class"], "risk_blocked")
                self.assertIsNone(result["verify"])
                self.assertEqual(result["risk_blocks"][0]["risk"], "push")
                self.assertIn("git push", result["risk_blocks"][0]["command"])
                self.assertNotIn("secret-value", json.dumps(result))
                if backend == "cursor":
                    log = [json.loads(line) for line in (self.root / "fake-cursor-acp.jsonl").read_text().splitlines()]
                    reply = next(row["permission_response"] for row in log if "permission_response" in row)
                    self.assertEqual(reply["result"]["outcome"]["outcome"], "cancelled")
                    self.assertTrue(any(row.get("method") == "session/cancel" for row in log))
                self.cli("lane", "down", self.lane_name)

    def test_codex_retries_model_unavailable_turn_with_lane_fallback(self):
        self.set_config(model="fake-model", fallback_models=["gpt-6-luna"])
        self.env["FAKE_APP_SERVER_OVERLOAD_MODEL"] = "fake-model"
        self.start_lane()
        run_id = self.assign("retry the overloaded turn")
        self.assertEqual(self.wait_run(run_id)["status"], "done")
        result = json.loads((self.data / "runs" / run_id / "result.json").read_text())
        self.assertEqual(result["model"], "gpt-6-luna")
        self.assertEqual([attempt["model"] for attempt in result["attempts"]], ["fake-model", "gpt-6-luna"])
        self.assertEqual([attempt["error_class"] for attempt in result["attempts"]], ["model_unavailable", None])
        self.assertTrue((self.data / "lanes" / self.lane_name / "wt" / "lane_partial.txt").exists())
        self.assertTrue((self.data / "runs" / run_id / "events.2.codex.jsonl").exists())
        self.assertIn("Selected model is at capacity", result["attempts"][0]["error_tail"])
        self.assertIn("codex/fake-model", json.loads((self.data / "breakers.json").read_text()))

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

    def test_cursor_agent_commit_is_reported_with_branch_and_diffstat(self):
        self.set_config("cursor")
        self.start_lane()
        run_id = self.assign("LANE_TEST_AGENT_COMMIT commit the result in the worktree")
        state = self.wait_run(run_id)
        result = json.loads((self.data / "runs" / run_id / "result.json").read_text())
        self.assertEqual(state["status"], "done")
        self.assertEqual(result["branch"], f"openswarm/{run_id}")
        self.assertTrue(result["commit"])
        self.assertIn("lane_result.txt", result["diffstat"])
        self.assertEqual(self.git("show", f"openswarm/{run_id}:lane_result.txt"),
                         (self.data / "lanes" / self.lane_name / "wt" / "lane_result.txt").read_text().rstrip("\n"))

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

    def test_agy_assign_commits_and_writes_normal_result(self):
        self.set_config("agy")
        self.start_lane()
        run_id = self.assign("add the fixture change")
        state = self.wait_run(run_id)
        result = json.loads((self.data / "runs" / run_id / "result.json").read_text())
        self.assertEqual(state["status"], "done")
        self.assertEqual(result["backend"], "agy")
        self.assertEqual(result["model"], "gemini-3.8-flash-medium")
        self.assertEqual(result["usage"]["input_tokens"], 120)
        self.assertTrue(result["commit"])
        self.assertEqual(self.git("show", f"openswarm/{run_id}:lane_result.txt"),
                         "completed by the fake Antigravity CLI")
        calls = [json.loads(line) for line in pathlib.Path(self.env["FAKE_AGY_LOG"]).read_text().splitlines()]
        self.assertEqual(calls[0][calls[0].index("--model") + 1], "gemini-3.8-flash-medium")
        self.assertEqual(calls[0][calls[0].index("--output-format") + 1], "stream-json")

    def test_agy_steer_aborts_the_process_and_resumes_the_conversation(self):
        self.set_config("agy")
        self.start_lane()
        run_id = self.assign("LANE_TEST_WAIT original task")
        self.wait_running(run_id)
        self.cli("lane", "steer", self.lane_name, "--text", "write steered instruction")
        self.assertEqual(self.wait_run(run_id)["status"], "done")
        calls = [json.loads(line) for line in pathlib.Path(self.env["FAKE_AGY_LOG"]).read_text().splitlines()]
        self.assertEqual(len(calls), 2)
        self.assertIn("--conversation", calls[1])
        self.assertEqual(calls[1][calls[1].index("--conversation") + 1], "conv-1")
        self.assertIn("write steered instruction", calls[1][calls[1].index("-p") + 1])
        events = (self.data / "runs" / run_id / "events.1.agy.jsonl").read_text()
        self.assertIn('"mode": "abort-and-resume"', events)
        self.assertTrue((self.data / "lanes" / self.lane_name / "wt" / "lane_result.txt").exists())

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
        original_head = self.git("rev-parse", "HEAD")
        first = self.assign("LANE_TEST_WAIT keep busy")
        self.wait_running(first)
        (self.repo / "later-commit.txt").write_text("must not enter the running turn\n")
        self.git("add", "later-commit.txt")
        self.git("commit", "-q", "-m", "advance repo while lane is busy")
        second = self.cli("lane", "assign", self.lane_name, "--prompt", "should be rejected", check=False)
        self.assertNotEqual(second.returncode, 0)
        self.assertIn("busy", json.loads(second.stdout)["error"])
        wt = self.data / "lanes" / self.lane_name / "wt"
        self.assertEqual(subprocess.run(["git", "rev-parse", "HEAD"], cwd=wt, check=True,
                                        capture_output=True, text=True).stdout.strip(), original_head)
        self.assertFalse((wt / "later-commit.txt").exists())
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
