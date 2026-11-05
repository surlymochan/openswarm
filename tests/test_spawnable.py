"""bin/_spawnable.py — the shared Windows spawn fix used by all bin/ scripts.

POSIX behaviour is a pure passthrough; the Windows branch is exercised by
patching os.name (the helper reads it at call time). Run:
python3 -m unittest discover -s tests   (or: python3 tests/test_spawnable.py)
"""
import importlib.util
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def load_helper():
    path = os.path.join(ROOT, "bin", "_spawnable.py")
    spec = importlib.util.spec_from_file_location("_spawnable", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sp = load_helper()


def write(directory, name, first_line):
    p = os.path.join(directory, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(first_line + "\n")
    return p


class Spawnable(unittest.TestCase):
    def test_posix_passthrough(self):
        cmd = ["/nonexistent/whatever", "arg"]
        self.assertIs(sp.spawnable(cmd), cmd)
        self.assertEqual(sp.spawnable([]), [])

    def test_windows_python_shebang_reinvokes(self):
        with tempfile.TemporaryDirectory() as d:
            tool = write(d, "tool", "#!/usr/bin/env python3")
            with mock.patch.object(os, "name", "nt"):
                self.assertEqual(sp.spawnable([tool, "x"]), [sys.executable, tool, "x"])

    def test_windows_native_extensions_untouched(self):
        with tempfile.TemporaryDirectory() as d:
            exe = write(d, "tool.exe", "#!/usr/bin/env python3")
            with mock.patch.object(os, "name", "nt"):
                self.assertEqual(sp.spawnable([exe]), [exe])

    def test_windows_non_python_shebang_untouched(self):
        with tempfile.TemporaryDirectory() as d:
            sh_tool = write(d, "tool", "#!/bin/sh")
            with mock.patch.object(os, "name", "nt"):
                self.assertEqual(sp.spawnable([sh_tool]), [sh_tool])

    def test_windows_missing_file_untouched(self):
        with mock.patch.object(os, "name", "nt"):
            cmd = ["/no/such/file", "x"]
            self.assertEqual(sp.spawnable(cmd), cmd)

if __name__ == "__main__":
    unittest.main(verbosity=2)
