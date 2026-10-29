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


def event_list(content=None, reason="stop"):
    user = {"role": "user", "content": "Public input:\npublic", "timestamp": 1700000000000}
    usage = {"input": 12, "output": 3, "cacheRead": 0, "cacheWrite": 0, "totalTokens": 15,
             "cost": {"input": 0.0, "output": 0.0, "cacheRead": 0.0, "cacheWrite": 0.0, "total": 0.0}}
    assistant = {"role": "assistant", "api": "anthropic-messages", "provider": "anthropic",
                 "model": "example", "usage": usage, "timestamp": 1700000000001,
                 "stopReason": reason, "content": content if content is not None else [{"type": "text", "text": "hello"}]}
    partial = dict(assistant, content=[{"type": "text", "text": "hello"}])
    return [{"type": "agent_start"}, {"type": "turn_start"},
            {"type": "message_start", "message": user}, {"type": "message_end", "message": user},
            {"type": "message_start", "message": dict(assistant, content=[])},
            *[{"type": "message_update", "message": partial, "assistantMessageEvent":
               dict(type="text_" + suffix, contentIndex=0, partial=partial, **extra)}
              for suffix, extra in (("start", {}), ("delta", {"delta": "hello"}),
                                    ("end", {"content": "hello"}))],
            {"type": "message_end", "message": assistant},
            {"type": "turn_end", "message": assistant, "toolResults": []},
            {"type": "agent_end", "messages": [user, assistant]}]


def events(content=None, reason="stop"):
    return "\n".join(json.dumps(x) for x in event_list(content, reason))


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
                         {"FAKE_EVENTS": "\n".join(events().splitlines()[:-1])}):
            with self.subTest(settings=settings):
                self.env.update(settings)
                code, output, error = self.invoke()
                self.assertEqual(code, 1)
                self.assertEqual(output, "")
                self.assertNotIn("do-not-log-this", error)
                self.env.update({"FAKE_HELP": " ".join(consult.PI_TEXT_FLAGS), "FAKE_HELP_RC": "0",
                                 "FAKE_RC": "0", "FAKE_STDERR": "", "FAKE_EVENTS": events()})

    def test_rejects_hidden_activity_and_malformed_lifecycle(self):
        import copy
        base = event_list()
        variants = []
        def variant(change):
            items = copy.deepcopy(base)
            change(items)
            variants.append(items)
        variant(lambda x: x[5]["assistantMessageEvent"].update(type="toolcall_delta"))
        variant(lambda x: x[5]["assistantMessageEvent"]["partial"]["content"].append(
            {"type": "toolCall", "id": "1", "name": "bash", "arguments": {}}))
        variant(lambda x: x[9]["toolResults"].append({"role": "toolResult"}))
        variant(lambda x: x[10]["messages"].append({"role": "toolResult"}))
        variant(lambda x: x[8]["message"].pop("stopReason"))
        variant(lambda x: x.insert(6, x.pop(10)))
        variant(lambda x: x[8]["message"].update(content="hello"))
        variant(lambda x: x[8]["message"]["content"][0].update(text={"type": "toolCall"}))
        variant(lambda x: x[0].update(message={"role": "toolResult"}))
        variant(lambda x: x.append({"type": "agent_start"}))
        for items in variants:
            with self.subTest(items=items):
                with self.assertRaises(ValueError):
                    consult.pi_text_answer("\n".join(json.dumps(e) for e in items))
        with self.assertRaises(ValueError):
            consult.pi_text_answer('{"type":"agent_start","type":"agent_start"}')

    def test_valid_thinking_and_text_events(self):
        content = [{"type": "thinking", "thinking": "reasoning", "thinkingSignature": "sig"},
                   {"type": "text", "text": "hello", "textSignature": "sig"}]
        self.assertEqual(consult.pi_text_answer(events(content)), "hello")

    def test_bounded_stdout_and_stderr(self):
        for stream in ("stdout", "stderr"):
            with self.subTest(stream=stream), self.assertRaises(ValueError):
                consult._pi_run([sys.executable, "-c", "import sys; sys." + stream + ".write('x' * 4097)"], 5, limit=4096)

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
