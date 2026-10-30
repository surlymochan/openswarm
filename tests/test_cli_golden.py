"""CLI help and lane service definitions are kept stable with golden outputs."""
import contextlib
import io
import os
from pathlib import Path
import pwd
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

import _load


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "bin" / "openswarm"
GOLDEN = Path(__file__).with_name("golden")
xs = _load.load()


class CliGoldenTests(unittest.TestCase):
    def test_help_output_matches_goldens(self):
        commands = {
            "help-run.txt": ("run", "--help"),
            "help-batch.txt": ("batch", "--help"),
            "help-lane-assign.txt": ("lane", "assign", "--help"),
            "help-bus-worker.txt": ("bus-worker", "--help"),
        }
        for filename, command in commands.items():
            with self.subTest(command=" ".join(command)):
                result = subprocess.run([sys.executable, str(CLI), *command], capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
                # argparse renamed the heading in Python 3.10.
                self.assertEqual(result.stdout.replace(b"optional arguments:", b"options:"),
                                 (GOLDEN / filename).read_bytes())

    def test_lane_service_definitions_match_goldens(self):
        lane = {"name": "L1", "backend": "codex", "model": "gpt-6-luna", "effort": "low",
                "repo": "/srv/project"}
        commands = {
            "lane-launchd-plist.txt": xs.cmd_lane_plist,
            "lane-systemd-unit.txt": xs.cmd_lane_systemd,
        }
        for filename, command in commands.items():
            with self.subTest(service=filename):
                output = io.StringIO()
                with contextlib.ExitStack() as stack:
                    stack.enter_context(mock.patch.object(xs, "DATA", "/var/lib/openswarm"))
                    stack.enter_context(mock.patch.object(xs, "__file__", "/opt/openswarm/bin/openswarm"))
                    stack.enter_context(mock.patch.object(xs, "lanes_by_name", return_value=[lane]))
                    stack.enter_context(mock.patch.object(
                        xs, "envvar",
                        side_effect=lambda name, default=None: "/etc/openswarm/lanes.json"
                        if name == "LANES_FILE" else default))
                    stack.enter_context(mock.patch.dict(os.environ, {
                        "HOME": "/home/openswarm", "PATH": "/usr/bin:/bin",
                    }))
                    stack.enter_context(mock.patch.object(xs.sys, "executable", "/usr/bin/python3"))
                    stack.enter_context(mock.patch.object(xs.os, "getuid", return_value=1000))
                    stack.enter_context(mock.patch.object(
                        pwd, "getpwuid", return_value=SimpleNamespace(pw_name="openswarm")))
                    stack.enter_context(contextlib.redirect_stdout(output))
                    command(SimpleNamespace(name="L1"))
                self.assertEqual(output.getvalue().encode(), (GOLDEN / filename).read_bytes())


if __name__ == "__main__":
    unittest.main(verbosity=2)
