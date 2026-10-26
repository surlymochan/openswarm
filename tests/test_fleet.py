import argparse
import contextlib
import io
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import _load

xs = _load.load()


class FleetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="openswarm-fleet-")
        self.root = pathlib.Path(self.tmp.name)
        self.patch = mock.patch.object(xs, "DATA", str(self.root))
        self.patch.start()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.patch.stop)

    def config(self, hosts):
        (self.root / "fleet.json").write_text(json.dumps({"hosts": hosts}))

    def test_status_does_not_contact_or_wake_paused_instabox(self):
        self.config([{"name": "local", "kind": "local"}, {"name": "box", "kind": "instabox", "box": "task-box"},
                     {"name": "ssh", "kind": "ssh", "ssh": "host-alias"}])
        def status(host, command, **kw):
            self.assertNotEqual(host["name"], "box")
            if host["name"] == "ssh":
                return 255, b"", b"unreachable"
            return 0, json.dumps({"lanes": [{"name": "L1", "state": "idle", "model": "m", "current_run": None}],
                                  "active_runs": [{"id": "r1", "state": "running"}]}).encode(), b""
        output = io.StringIO()
        with mock.patch.object(xs, "sh", return_value=(0, "task-box  Paused\n", "")) as ls, \
                mock.patch.object(xs, "host_run", side_effect=status) as transport, contextlib.redirect_stdout(output):
            xs.cmd_fleet(argparse.Namespace(fleet_cmd="status", json=True))
        rows = json.loads(output.getvalue())["hosts"]
        self.assertTrue(rows[0]["reachable"])
        self.assertEqual(rows[0]["lanes"][0]["model"], "m")
        self.assertTrue(rows[1]["paused"])
        self.assertFalse(rows[1]["reachable"])
        self.assertFalse(rows[2]["reachable"])
        self.assertEqual(transport.call_count, 2)
        ls.assert_called_once_with(["instabox", "ls"], timeout=20)

    def test_unknown_instabox_state_does_not_open_ssh(self):
        self.config([{"name": "box", "kind": "instabox", "box": "task-box"}])
        with mock.patch.object(xs, "sh", return_value=(1, "", "offline")), \
                mock.patch.object(xs, "host_run") as transport, contextlib.redirect_stdout(io.StringIO()):
            xs.cmd_fleet(argparse.Namespace(fleet_cmd="status", json=False))
        transport.assert_not_called()

    def test_text_status_uses_reachable_snapshot_without_lanes(self):
        self.config([{"name": "box", "kind": "instabox", "box": "task-box"}])
        output = io.StringIO()
        with mock.patch.object(xs, "sh", return_value=(0, "task-box  Running\n", "")), \
                mock.patch.object(xs, "host_run", return_value=(0, b'{"lanes": [], "active_runs": [], "queue_depth": 0}', b"")), \
                contextlib.redirect_stdout(output):
            xs.cmd_fleet(argparse.Namespace(fleet_cmd="status", json=False))
        self.assertEqual(output.getvalue(), "box  reachable  lanes=0 active=0 queue=0\n")

    def test_status_marks_invalid_lane_config_separately_from_unreachable(self):
        (self.root / "lanes.json").write_text('{"lanes": [')
        output = io.StringIO()
        with mock.patch.object(xs, "host_json", return_value=xs.fleet_snapshot()), contextlib.redirect_stdout(output):
            xs.cmd_fleet(argparse.Namespace(fleet_cmd="status", json=False))
        self.assertIn("local  config invalid", output.getvalue())
        self.assertNotIn("unreachable", output.getvalue())
        self.assertIn("queue=0", output.getvalue())

    def test_transport_argv_binary_input_and_timeout(self):
        for host, prefix in [({"kind": "ssh", "ssh": "alias"}, ["ssh", "alias", "--"]),
                             ({"kind": "instabox", "box": "task-box"}, ["instabox", "ssh", "task-box", "--"])]:
            with mock.patch.object(xs.subprocess, "run", return_value=subprocess.CompletedProcess([], 3, b"out", b"err")) as run:
                self.assertEqual(xs.host_run(host, "command", timeout=7, input=b"\x00bundle"), (3, b"out", b"err"))
                self.assertEqual(run.call_args.args[0], prefix + ["command"])
                self.assertEqual(run.call_args.kwargs["input"], b"\x00bundle")
        self.assertEqual(xs.host_run({"kind": "local"}, "exec sleep 5", timeout=0.01)[0], 124)

    def test_exec_passes_arguments_and_exit_code_without_shell_expansion(self):
        binary = self.root / "stub"
        binary.write_text("#!/usr/bin/env python3\nimport json, sys\nprint(json.dumps(sys.argv[1:]))\nsys.exit(7)\n")
        binary.chmod(0o755)
        self.config([{"name": "local", "kind": "local", "openswarm": str(binary)}])
        proc = subprocess.run([sys.executable, str(pathlib.Path(_load.ROOT) / "bin" / "openswarm"),
                               "fleet", "exec", "local", "--", "run", "--prompt", "$(false); literal"],
                              env={**xs.os.environ, "OPENSWARM_HOME": str(self.root)}, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 7)
        self.assertEqual(json.loads(proc.stdout), ["run", "--prompt", "$(false); literal"])

    def test_invalid_config_rejected(self):
        self.config([{"name": "x", "kind": "ssh", "ssh": "-unsafe"}])
        with self.assertRaises(ValueError):
            xs.fleet_config()


if __name__ == "__main__":
    unittest.main()
