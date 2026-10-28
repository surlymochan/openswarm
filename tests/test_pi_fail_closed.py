"""Host-only checks for Pi's unverified capability boundary."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"))
import _load

xs = _load.load()


class PiFailClosed(unittest.TestCase):
    def test_kill_tree_stops_process_and_descendant(self):
        code = ("import signal, subprocess, sys, time\n"
                "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                "def stop(*args):\n child.terminate(); child.wait(); sys.exit(0)\n"
                "signal.signal(signal.SIGTERM, stop)\n"
                "print(child.pid, flush=True)\n"
                "time.sleep(60)\n")
        proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE,
                                text=True, start_new_session=True)
        try:
            child_pid = int(proc.stdout.readline())
            xs.kill_tree(proc.pid, grace=0.2)
            proc.wait(timeout=3)
            self.assertFalse(xs.pid_alive(proc.pid))
            self.assertFalse(xs.pid_alive(child_pid))
        finally:
            if proc.poll() is None:
                xs.kill_tree(proc.pid, grace=0.2)
                proc.wait(timeout=3)
            proc.stdout.close()

    def test_uncooperative_descendant_is_killed_and_unconfirmed_blocks_retry(self):
        proc = mock.Mock(pid=100)
        with mock.patch.object(xs, "descendants", return_value=[200]), \
             mock.patch.object(xs.os, "killpg") as kill_group, \
             mock.patch.object(xs.os, "kill") as kill_process, \
             mock.patch.object(xs.time, "sleep"), \
             mock.patch.object(xs, "pid_alive", side_effect=lambda pid: pid == 200):
            self.assertFalse(xs.terminate_executor(proc, grace=0))
        kill_group.assert_any_call(200, xs.signal.SIGTERM)
        kill_process.assert_any_call(200, xs.signal.SIGKILL)

    def test_normal_exit_stops_observed_independent_session_child(self):
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as run:
            marker = os.path.join(run, "child")
            code = ("import subprocess, sys, time\n"
                    "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], start_new_session=True)\n"
                    "open(sys.argv[1], 'w').write(str(p.pid))\n"
                    "time.sleep(0.4)\n")
            try:
                rc = xs._run_proc([sys.executable, "-c", code, marker], run, os.environ.copy(), 5,
                                  os.path.join(run, "events"))
                self.assertEqual(rc, 0)
                pid = int(Path(marker).read_text())
                self.assertFalse(xs.pid_alive(pid))
            finally:
                if os.path.exists(marker):
                    pid = int(Path(marker).read_text())
                    if xs.pid_alive(pid):
                        os.kill(pid, 9)

    def test_unconfirmed_normal_exit_raises_and_preserves_temp(self):
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as run, \
             mock.patch.object(xs, "terminate_executor", return_value=False), \
             mock.patch.object(xs, "executor_worktree_root", return_value=run), \
             mock.patch.object(xs, "git", return_value=(1, "", "")):
            with self.assertRaisesRegex(xs.TerminationUnconfirmed, "quarantined"):
                xs.run_proc([sys.executable, "-c", "pass"], run, os.environ.copy(), 5,
                            os.path.join(run, "events"))
            self.assertTrue(os.path.isdir(os.path.join(run, ".openswarm-tmp")))

    def test_codex_read_only_start_uses_read_only_sandbox(self):
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as run, \
             mock.patch.object(xs, "run_proc", return_value=1) as launch:
            open(os.path.join(run, "events.err"), "w").close()
            open(os.path.join(run, "events"), "w").close()
            xs.exec_codex({"model": "test", "effort": "low", "read_only": True},
                          {"codex": {"binary": "codex"}}, "review", run, run, 5,
                          os.path.join(run, "events"))
            command = launch.call_args.args[0]
            self.assertEqual(command[command.index("-s") + 1], "read-only")

    def test_read_only_never_launches_one_shot(self):
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as run:
            event = os.path.join(run, "events")
            with mock.patch.object(xs, "pi_bin", return_value="pi"), \
                 mock.patch.object(xs, "run_proc") as launch:
                rc, result, _, error = xs.exec_pi({"read_only": True}, {}, "review", run, run, 5, event)
            self.assertNotEqual(rc, 0)
            self.assertIsNone(result)
            self.assertIn("cannot be verified", error)
            launch.assert_not_called()

    def test_type_and_card_reject_without_launch(self):
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as run, \
             mock.patch.object(xs, "pi_bin", return_value="pi"), \
             mock.patch.object(xs, "run_proc") as launch:
            for decision in ({"type": "review"}, {"card_type": "review"}):
                with self.subTest(decision=decision):
                    rc, _, _, error = xs.exec_pi(decision, {}, "review", run, run, 5,
                                                  os.path.join(run, "events"))
                    self.assertNotEqual(rc, 0)
                    self.assertIn("cannot be verified", error)
            launch.assert_not_called()

    def test_read_only_task_type_rejects_live_lane(self):
        child = mock.Mock(pi_tools=None)
        with mock.patch.object(xs, "LaneChild") as launch:
            with self.assertRaisesRegex(RuntimeError, "cannot be verified"):
                xs.lane_refresh_pi_child_for_tools({"backend": "pi"}, child, {}, {"type": "review"}, None)
        child.close.assert_not_called()
        launch.assert_not_called()

    def test_consult_and_review_reject_before_launch(self):
        import openswarm_consult as consult
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as run:
            profile = {"backend": "pi", "binary": "pi", "cwd": run}
            with mock.patch.object(xs, "RUNS", run), mock.patch.object(consult.subprocess, "Popen") as launch, \
                 mock.patch.object(xs, "reserve_provider_slot", return_value=True), \
                 mock.patch.object(xs, "set_status"):
                row = consult._ask_one(xs, profile, "review", run, xs.NOW() + 5, {}, "test")
                self.assertIn("cannot be verified", row["error"])
                launch.assert_not_called()
            with mock.patch.object(xs.subprocess, "run") as launch:
                with self.assertRaisesRegex(RuntimeError, "cannot be verified"):
                    xs.invoke_reviewer(profile, "review", run, run, {})
                launch.assert_not_called()

    def test_monitor_exception_stops_started_process_and_forbids_fallback(self):
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as run:
            event = os.path.join(run, "events")
            real_popen = subprocess.Popen
            started = []
            def spawn(*args, **kwargs):
                proc = real_popen(*args, **kwargs)
                started.append(proc)
                return proc
            with mock.patch.object(xs.subprocess, "Popen", side_effect=spawn), \
                 mock.patch.object(xs, "set_status", side_effect=RuntimeError("monitor failed")):
                with self.assertRaisesRegex(RuntimeError, "monitor failed"):
                    xs._run_proc([sys.executable, "-c", "import time; time.sleep(60)"],
                                 run, os.environ.copy(), 5, event)
            self.assertIsNotNone(started[0].poll())

    def test_unconfirmed_descendant_termination_is_terminal(self):
        proc = mock.Mock(pid=123)
        with mock.patch.object(xs, "kill_tree", return_value=[123, 456]), \
             mock.patch.object(xs, "pid_alive", side_effect=lambda pid: pid == 456):
            self.assertFalse(xs.terminate_executor(proc, grace=0))

    def test_lane_rejects_before_restart_or_prompt(self):
        child = mock.Mock(pi_tools=None)
        with mock.patch.object(xs, "LaneChild") as launch:
            with self.assertRaisesRegex(RuntimeError, "cannot be verified"):
                xs.lane_refresh_pi_child_for_tools({"backend": "pi"}, child, {}, {"read_only": True}, None)
        child.close.assert_not_called()
        launch.assert_not_called()

    def test_lane_does_not_replace_live_child(self):
        child = mock.Mock(pi_tools="old")
        child.alive.return_value = True
        with mock.patch.object(xs, "lane_root", return_value="."), mock.patch.object(xs, "LaneChild") as launch:
            with self.assertRaisesRegex(RuntimeError, "did not terminate"):
                xs.lane_refresh_pi_child_for_tools({"backend": "pi", "name": "a"}, child, {}, {}, None)
        launch.assert_not_called()

    def test_lane_start_failure_does_not_return_replacement(self):
        child = mock.Mock(pi_tools="old")
        child.alive.return_value = False
        with mock.patch.object(xs, "lane_root", return_value="."), \
             mock.patch.object(xs, "LaneChild", side_effect=OSError("startup failed")) as launch, \
             mock.patch.object(xs, "lane_write_state") as state:
            with self.assertRaisesRegex(OSError, "startup failed"):
                xs.lane_refresh_pi_child_for_tools({"backend": "pi", "name": "a"}, child, {}, {}, None)
        child.close.assert_called_once()
        launch.assert_called_once()
        state.assert_not_called()

    def test_read_only_card_type_also_rejects_live_child(self):
        child = mock.Mock(pi_tools=None)
        with mock.patch.object(xs, "LaneChild") as launch:
            with self.assertRaisesRegex(RuntimeError, "cannot be verified"):
                xs.lane_refresh_pi_child_for_tools({"backend": "pi"}, child, {}, {"card_type": "review"}, None)
        child.close.assert_not_called()
        launch.assert_not_called()

    def test_spawn_failure_is_a_pre_start_error(self):
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as run, \
             mock.patch.object(xs.subprocess, "Popen", side_effect=OSError("missing")):
            with self.assertRaisesRegex(xs.ExecutorStartError, "missing"):
                xs._run_proc(["missing"], run, {}, 5, os.path.join(run, "events"))

    def test_descendant_discovery_failure_is_uncertain(self):
        with mock.patch.object(xs, "sh", return_value=(2, "", "failed")):
            with self.assertRaises(xs.TerminationUnconfirmed):
                xs.descendants(123)

    def test_executor_startup_exception_is_recorded(self):
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as run, \
             mock.patch.object(xs, "reserve_provider_slot", return_value=True), \
             mock.patch.object(xs, "set_status"), mock.patch.object(xs, "quota_used", return_value=None), \
             mock.patch.dict(xs.EXECUTORS, {"pi": mock.Mock(side_effect=OSError("startup failed"))}):
            attempts, final, error = xs.run_executor_with_rate_limit(
                "pi", {"model": None}, {}, "review", run, run, xs.NOW() + 30, {})
        self.assertIsNone(final)
        self.assertEqual(error, "executor_exception")
        self.assertIn("startup failed", attempts[0]["error_tail"])
