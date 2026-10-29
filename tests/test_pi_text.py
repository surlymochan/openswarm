"""Text-only Pi subprocess contract; no provider calls."""
import contextlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "bin"))
import openswarm_consult as consult

FAKE = '''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
if args == ["--help"]:
    print(os.environ.get("FAKE_HELP", ""))
    sys.exit(int(os.environ.get("FAKE_HELP_RC", "0")))
with open(os.environ["FAKE_LOG"], "w") as f:
    json.dump(args, f)
print(os.environ.get("FAKE_EVENTS", ""))
print(os.environ.get("FAKE_STDERR", ""), file=sys.stderr)
sys.exit(int(os.environ.get("FAKE_RC", "0")))
'''


def events(content=None, reason="stop"):
    return "\n".join(json.dumps(x) for x in [
        {"type": "agent_start"},
        {"type": "message_end", "message": {"role": "assistant", "stopReason": reason,
         "content": content if content is not None else [{"type": "text", "text": "hello"}]}},
        {"type": "agent_end"}])


class PiTextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix=".pi-text-test-", dir=os.path.dirname(os.path.dirname(__file__)))
        self.addCleanup(self.tmp.cleanup)
        self.binary = os.path.join(self.tmp.name, "pi")
        with open(self.binary, "w") as f:
            f.write(FAKE)
        os.chmod(self.binary, os.stat(self.binary).st_mode | stat.S_IEXEC)
        self.log = os.path.join(self.tmp.name, "argv.json")
        self.env = {"FAKE_LOG": self.log, "FAKE_HELP": " ".join(consult.PI_TEXT_FLAGS),
                    "FAKE_EVENTS": events(), "FAKE_RC": "0", "FAKE_HELP_RC": "0",
                    "FAKE_STDERR": "", "EXAMPLE_SECRET": "do-not-log-this"}

    def invoke(self, text="public", approved=True):
        output, error = io.StringIO(), io.StringIO()
        args = types.SimpleNamespace(text=text, public_input_approved=approved)
        with mock.patch.dict(os.environ, self.env), contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            code = consult.cmd_pi_text(args, types.SimpleNamespace(pi_bin=lambda: self.binary))
        return code, output.getvalue(), error.getvalue()

    def test_exact_invocation_and_text_response(self):
        code, output, error = self.invoke("@file --tools bash")
        self.assertEqual((code, json.loads(output), error), (0, {"text": "hello"}, ""))
        with open(self.log) as f:
            argv = json.load(f)
        self.assertEqual(argv, ["-p", "--mode", "json", *consult.PI_TEXT_FLAGS,
                                "--system-prompt", consult.PI_TEXT_SYSTEM, "Public input:\n@file --tools bash"])
        self.assertNotIn("do-not-log-this", json.dumps(argv) + output + error)

    def test_missing_flags_and_failures_are_closed_and_redacted(self):
        for settings in ({"FAKE_HELP": "--no-tools"}, {"FAKE_HELP_RC": "1"},
                         {"FAKE_RC": "1", "FAKE_STDERR": "do-not-log-this"},
                         {"FAKE_EVENTS": "not-json"}, {"FAKE_EVENTS": '{"type":"future"}'},
                         {"FAKE_EVENTS": events(reason="error")},
                         {"FAKE_EVENTS": events([{"type": "toolCall", "name": "bash"}])},
                         {"FAKE_EVENTS": events().replace('{"type": "agent_end"}', '')}):
            with self.subTest(settings=settings):
                self.env.update(settings)
                code, output, error = self.invoke()
                self.assertEqual(code, 1)
                self.assertEqual(output, "")
                self.assertNotIn("do-not-log-this", error)
                self.env.update({"FAKE_HELP": " ".join(consult.PI_TEXT_FLAGS), "FAKE_HELP_RC": "0",
                                 "FAKE_RC": "0", "FAKE_STDERR": "", "FAKE_EVENTS": events()})

    def test_cli_path_uses_fake_pi_without_creating_openswarm_home(self):
        home = os.path.join(self.tmp.name, "unused-home")
        env = os.environ.copy()
        env.update(self.env, OPENSWARM_PI_BIN=self.binary, OPENSWARM_HOME=home)
        cli = os.path.join(os.path.dirname(os.path.dirname(__file__)), "bin", "openswarm")
        result = subprocess.run([sys.executable, cli, "pi-text", "--public-input-approved", "--text", "public"],
                                env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"text": "hello"})
        self.assertFalse(os.path.exists(home))

    def test_input_bounds_and_approval_before_launch(self):
        for text, approved in (("", True), ("x", False), ("é" * 8001, True), ("x\x00", True)):
            with self.subTest(text=text[:5], approved=approved):
                self.assertEqual(self.invoke(text, approved)[0], 2)
                self.assertFalse(os.path.exists(self.log))
        self.assertEqual(self.invoke("é" * 8000)[0], 0)


if __name__ == "__main__":
    unittest.main()
