"""Host-only checks for Pi's unverified capability boundary."""
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

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

    def test_executor_startup_exception_is_recorded(self):
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as run, \
             mock.patch.object(xs, "reserve_provider_slot", return_value=True), \
             mock.patch.object(xs, "set_status"), mock.patch.object(xs, "quota_used", return_value=None), \
             mock.patch.dict(xs.EXECUTORS, {"pi": mock.Mock(side_effect=OSError("startup failed"))}):
            attempts, final, error = xs.run_executor_with_rate_limit(
                "pi", {"model": None}, {}, "review", run, run, xs.NOW() + 30, {})
        self.assertIsNone(final)
        self.assertEqual(error, "exec_error")
        self.assertIn("startup failed", attempts[0]["error_tail"])
