import argparse
import contextlib
import io
import json
import os
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
                mock.patch.object(xs, "host_run", return_value=(0, b'{"lanes": [], "active_runs": []}', b"")), \
                contextlib.redirect_stdout(output):
            xs.cmd_fleet(argparse.Namespace(fleet_cmd="status", json=False))
        self.assertEqual(output.getvalue(), "box  reachable  lanes=0 active=0\n")

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

    def test_ledger_pull_merges_redacts_deduplicates_and_skips_unreachable_hosts(self):
        fake_bin = self.root / "bin"
        fake_bin.mkdir()
        ssh = fake_bin / "ssh"
        ssh.write_text(
            f"#!{sys.executable}\n"
            "import os, pathlib, sys\n"
            "host = sys.argv[1]\n"
            "if host == 'down':\n"
            "    print('unreachable', file=sys.stderr)\n"
            "    raise SystemExit(255)\n"
            "assert sys.argv[2] == '--' and sys.argv[3].startswith('cat ')\n"
            "path = pathlib.Path(os.environ['OPENSWARM_TEST_SSH_ROOT']) / (host + '.jsonl')\n"
            "sys.stdout.buffer.write(path.read_bytes())\n"
        )
        ssh.chmod(0o755)
        self.config([{"name": "local", "kind": "local"},
                     {"name": "remote", "kind": "ssh", "ssh": "good"},
                     {"name": "down", "kind": "ssh", "ssh": "down"}])
        local_ledger = self.root / "local.jsonl"
        ledger_patch = mock.patch.object(xs, "LEDGER", str(local_ledger))
        ledger_patch.start()
        self.addCleanup(ledger_patch.stop)
        local_ledger.write_text(json.dumps({"run_id": "shared", "host": "same-host", "title": "local"}) + "\n")
        (self.root / "good.jsonl").write_text("\n".join([
            json.dumps({"run_id": "shared", "host": "same-host", "title": "duplicate"}),
            json.dumps({"run_id": "remote-run", "title": "token sk-12345678901234567890"}),
            "",
        ]))
        destination = self.root / "ledger-fleet.jsonl"
        original_argv = sys.argv
        output, errors = io.StringIO(), io.StringIO()
        ledger_output = io.StringIO()
        try:
            sys.argv = ["openswarm", "fleet", "ledger", "pull"]
            with mock.patch.dict(xs.os.environ, {
                "PATH": str(fake_bin) + os.pathsep + xs.os.environ.get("PATH", ""),
                "OPENSWARM_TEST_SSH_ROOT": str(self.root),
            }), contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                xs.main()
            rows = [json.loads(line) for line in destination.read_text().splitlines()]
            first_pull = destination.read_bytes()
            sys.argv = ["openswarm", "fleet", "ledger", "pull"]
            with mock.patch.dict(xs.os.environ, {
                "PATH": str(fake_bin) + os.pathsep + xs.os.environ.get("PATH", ""),
                "OPENSWARM_TEST_SSH_ROOT": str(self.root),
            }), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                xs.main()
            self.assertEqual(destination.read_bytes(), first_pull)

            custom_destination = self.root / "custom.jsonl"
            sys.argv = ["openswarm", "fleet", "ledger", "pull", "--hosts", "remote",
                        "--into", str(custom_destination)]
            with mock.patch.dict(xs.os.environ, {
                "PATH": str(fake_bin) + os.pathsep + xs.os.environ.get("PATH", ""),
                "OPENSWARM_TEST_SSH_ROOT": str(self.root),
            }), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                xs.main()
            self.assertEqual([json.loads(line)["run_id"] for line in custom_destination.read_text().splitlines()],
                             ["shared", "remote-run"])

            sys.argv = ["openswarm", "ledger", "--fleet", "--json"]
            with contextlib.redirect_stdout(ledger_output):
                xs.main()
        finally:
            sys.argv = original_argv

        self.assertEqual(len(rows), 2)
        self.assertEqual(sum(row["run_id"] == "shared" for row in rows), 1)
        remote_row = next(row for row in rows if row["run_id"] == "remote-run")
        self.assertEqual(remote_row["host"], "remote")
        self.assertEqual(remote_row["title"], "token [REDACTED]")
        self.assertIn("down: unreachable", errors.getvalue())
        # The --fleet reader uses the merged file, not the local source ledger.
        self.assertEqual(json.loads(ledger_output.getvalue()), rows)

    def test_ledger_pull_fails_when_every_host_is_unreachable(self):
        fake_bin = self.root / "bin"
        fake_bin.mkdir()
        ssh = fake_bin / "ssh"
        ssh.write_text(f"#!{sys.executable}\nraise SystemExit(255)\n")
        ssh.chmod(0o755)
        self.config([{"name": "down", "kind": "ssh", "ssh": "down"}])
        original_argv = sys.argv
        try:
            sys.argv = ["openswarm", "fleet", "ledger", "pull", "--into", str(self.root / "out.jsonl")]
            with mock.patch.dict(xs.os.environ, {"PATH": str(fake_bin) + os.pathsep + xs.os.environ.get("PATH", "")}):
                with self.assertRaises(SystemExit) as error, contextlib.redirect_stderr(io.StringIO()):
                    xs.main()
        finally:
            sys.argv = original_argv
        self.assertEqual(error.exception.code, 1)
        self.assertFalse((self.root / "out.jsonl").exists())

    def test_ledger_pull_enforces_line_and_record_size_limits(self):
        oversized = b"{" + (b" " * xs.LEDGER_MAX_LINE_BYTES) + b"}\n"
        valid = json.dumps({"run_id": "kept"}).encode() + b"\n"
        self.assertEqual([row["run_id"] for row in xs.ledger_records(oversized + valid, "h")], ["kept"])
        self.assertEqual(xs.ledger_records(b"\n" * xs.LEDGER_MAX_HOST_LINES + valid, "h"), [])


if __name__ == "__main__":
    unittest.main()
