import argparse
import contextlib
import io
import json
import pathlib
import tempfile
import unittest
from unittest import mock

import _load

xs = _load.load()


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="openswarm-resume-")
        self.root = pathlib.Path(self.tmp.name)
        self.runs = self.root / "runs"
        self.runs.mkdir()
        self.constants = {key: getattr(xs, key) for key in ("RUNS", "NOW")}
        xs.RUNS = str(self.runs)
        xs.NOW = lambda: 1_800_000_000

    def tearDown(self):
        for key, value in self.constants.items():
            setattr(xs, key, value)
        self.tmp.cleanup()

    def run_record(self, run_id, **status):
        directory = self.runs / run_id
        directory.mkdir()
        (directory / "status.json").write_text(json.dumps(status))
        if status.get("state") == "finished":
            (directory / "result.json").write_text(json.dumps({"status": status.get("status")}))

    def test_resume_lists_live_runs_busy_lanes_and_recent_finished(self):
        now = xs.NOW()
        self.run_record("live-running", state="running", runner_pid=101, started=now - 60,
                        title="active task", backend="codex", model="m")
        self.run_record("live-queued", state="queued", runner_pid=102, started=now - 30,
                        title="queued task", backend="pi", model="p")
        self.run_record("dead", state="running", runner_pid=103, started=now - 30)
        self.run_record("recent", state="finished", finished=now - 5 * 3600, status="done")
        self.run_record("stale", state="finished", finished=now - 7 * 3600, status="failed")
        lanes = [{"name": "L1", "state": "busy", "current_run": "live-running"},
                 {"name": "L2", "state": "idle", "current_run": None}]
        output = io.StringIO()
        with mock.patch.object(xs, "pid_alive", side_effect=lambda pid: pid in (101, 102)), \
                mock.patch.object(xs, "lane_status_rows", return_value=lanes), \
                contextlib.redirect_stdout(output):
            xs.cmd_resume(argparse.Namespace(since_hours=6, json=True))
        result = json.loads(output.getvalue())
        self.assertEqual([row["id"] for row in result["active_runs"]], ["live-running", "live-queued"])
        self.assertEqual([row["watch"] for row in result["active_runs"]],
                         ["openswarm watch live-running", "openswarm watch live-queued"])
        self.assertEqual(result["busy_lanes"][0]["watch"], "openswarm watch live-running")
        self.assertEqual([row["id"] for row in result["recent_finished"]], ["recent"])
        self.assertEqual(result["recent_finished"][0]["status"], "done")
        self.assertEqual(result["recent_finished"][0]["result_path"],
                         str(self.runs / "recent" / "result.json"))

    def test_resume_text_includes_watch_commands_and_result_paths(self):
        now = xs.NOW()
        self.run_record("finished", state="finished", finished=now - 120, status="partial")
        with mock.patch.object(xs, "lane_status_rows", return_value=[
                {"name": "C1", "state": "busy", "current_run": "active"}]), \
                mock.patch.object(xs, "pid_alive", return_value=True):
            self.run_record("active", state="queued", runner_pid=101, started=now, title="task")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                xs.cmd_resume(argparse.Namespace(since_hours=6, json=False))
        self.assertIn("openswarm watch active", output.getvalue())
        self.assertIn("C1 run=active", output.getvalue())
        self.assertIn(str(self.runs / "finished" / "result.json"), output.getvalue())
        self.assertIn("partial", output.getvalue())


if __name__ == "__main__":
    unittest.main()
