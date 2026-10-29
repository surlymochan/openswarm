"""Independent synthetic probes of the Pi text-mode audit counterexamples."""
import copy
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "bin"))
import openswarm_consult as consult
from test_pi_text import event_list

ROOT = os.path.dirname(os.path.dirname(__file__))


def parse(items):
    return consult.pi_text_answer("\n".join(json.dumps(item) for item in items))


class PiTextAuditTests(unittest.TestCase):
    def test_counterexamples_reject(self):
        base = event_list()
        cases = {}
        items = json.loads(json.dumps(base))
        items.insert(6, items.pop(10))
        cases["agent_end before answer"] = items
        items = json.loads(json.dumps(base))
        items[10]["messages"][1]["content"].append({"type": "toolResult", "content": "secret"})
        cases["nested toolResult in final messages"] = items
        items = json.loads(json.dumps(base))
        items[5]["assistantMessageEvent"]["type"] = "toolcall_start"
        cases["toolcall_start in update"] = items
        items = json.loads(json.dumps(base))
        items[8]["message"] = ["malformed"]
        cases["malformed message payload"] = items
        items = json.loads(json.dumps(base))
        del items[8]["message"]["stopReason"]
        cases["missing stopReason"] = items
        for name, items in cases.items():
            with self.subTest(name=name), self.assertRaises(ValueError):
                parse(items)

    def test_realistic_incremental_text_and_thinking(self):
        items = event_list()
        assistant = items[8]["message"]
        def partial(content):
            return dict(assistant, content=copy.deepcopy(content))
        thinking = {"type": "thinking", "thinking": "consider", "thinkingSignature": "sig"}
        text = {"type": "text", "text": "hello world", "textSignature": "sig"}
        assistant["content"] = [thinking, text]
        items[4]["message"] = partial([])
        updates = []
        for kind, index, content, extra in (
            ("thinking_start", 0, [{"type": "thinking", "thinking": ""}], {}),
            ("thinking_delta", 0, [thinking], {"delta": "consider"}),
            ("thinking_end", 0, [thinking], {"content": "consider"}),
            ("text_start", 1, [thinking, {"type": "text", "text": ""}], {}),
            ("text_delta", 1, [thinking, {"type": "text", "text": "hello "}], {"delta": "hello "}),
            ("text_delta", 1, [thinking, text], {"delta": "world"}),
            ("text_end", 1, [thinking, text], {"content": "hello world"}),
        ):
            message = partial(content)
            updates.append({"type": "message_update", "message": message,
                            "assistantMessageEvent": {"type": kind, "contentIndex": index,
                                                      "partial": copy.deepcopy(message), **extra}})
        items[5:8] = updates
        items[-2]["message"] = copy.deepcopy(assistant)
        items[-1]["messages"][1] = copy.deepcopy(assistant)
        self.assertEqual(parse(items), "hello world")
        self.assertEqual(parse(event_list()), "hello")

    def test_output_limit_boundary(self):
        with mock.patch.dict(os.environ, {"PATH": os.defpath}, clear=True):
            for stream in ("stdout", "stderr"):
                for size in (4096, 4097):
                    with self.subTest(stream=stream, size=size):
                        command = [sys.executable, "-I", "-c", f"import sys; sys.{stream}.write('x'*{size})"]
                        if size == 4096:
                            code, output = consult._pi_run(command, 5, limit=4096)
                            self.assertEqual(code, 0)
                            self.assertEqual(len(output), size if stream == "stdout" else 0)
                        else:
                            with self.assertRaisesRegex(ValueError, "output limit"):
                                consult._pi_run(command, 5, limit=4096)

    def test_timeout_and_overflow_reap_direct_fake_process(self):
        with tempfile.TemporaryDirectory(prefix=".pi-audit-", dir=ROOT) as directory:
            with mock.patch.dict(os.environ, {"PATH": os.defpath}, clear=True):
                for mode in ("timeout", "overflow"):
                    with self.subTest(mode=mode):
                        pidfile = os.path.join(directory, mode)
                        script = ("import os,sys,time; "
                                  "open(sys.argv[1], 'w').write(str(os.getpid())); "
                                  + ("sys.stdout.write('x'*8192); sys.stdout.flush(); " if mode == "overflow" else "")
                                  + "time.sleep(30)")
                        start = time.monotonic()
                        with self.assertRaises(subprocess.TimeoutExpired if mode == "timeout" else ValueError):
                            consult._pi_run([sys.executable, "-I", "-c", script, pidfile], 1, limit=4096)
                        self.assertLess(time.monotonic() - start, 5)
                        with open(pidfile) as file:
                            pid = int(file.read())
                        with self.assertRaises(ProcessLookupError):
                            os.kill(pid, 0)


if __name__ == "__main__":
    unittest.main()
