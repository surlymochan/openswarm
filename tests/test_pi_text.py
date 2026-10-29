"""Text-only Pi subprocess contract; no provider calls."""
import contextlib
import io
import importlib.machinery
import importlib.util
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "bin"))
import openswarm_consult as consult

FAKE = '''#!/usr/bin/env python3
import json, os, sys, time
args = sys.argv[1:]
if args == ["--help"]:
    print(os.environ.get("FAKE_HELP", ""))
    sys.exit(int(os.environ.get("FAKE_HELP_RC", "0")))
if args == ["--version"]:
    print(os.environ.get("FAKE_VERSION", "0.73.1"))
    sys.exit(0)
prompt = sys.stdin.buffer.read()
with open(os.environ["FAKE_LOG"], "w") as f:
    json.dump({"args": args, "prompt": prompt.decode("utf-8")}, f)
if os.environ.get("FAKE_SLEEP"):
    time.sleep(float(os.environ["FAKE_SLEEP"]))
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
        self.home = os.path.join(self.tmp.name, "home")
        loader = importlib.machinery.SourceFileLoader("openswarm_pi_text_test", os.path.join(os.path.dirname(os.path.dirname(__file__)), "bin", "openswarm"))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        self.core = importlib.util.module_from_spec(spec)
        loader.exec_module(self.core)
        self.core.DATA, self.core.RUNS = self.home, os.path.join(self.home, "runs")
        self.core.pi_bin = lambda: self.binary
        self.env = {"FAKE_LOG": self.log, "FAKE_HELP": " ".join(consult.PI_TEXT_FLAGS),
                    "FAKE_EVENTS": events(), "FAKE_RC": "0", "FAKE_HELP_RC": "0",
                    "FAKE_STDERR": "", "EXAMPLE_SECRET": "do-not-log-this"}

    def invoke(self, text="public", approved=True):
        output, error = io.StringIO(), io.StringIO()
        args = types.SimpleNamespace(text=text, public_input_approved=approved)
        with mock.patch.dict(os.environ, self.env), contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            code = consult.cmd_pi_text(args, self.core, self.core.load_policy())
        return code, output.getvalue(), error.getvalue()

    def test_exact_invocation_and_text_response(self):
        prompt = " \n@file --tools bash\n第二行"
        code, output, error = self.invoke(prompt)
        self.assertEqual((code, json.loads(output), error), (0, {"text": "hello"}, ""))
        with open(self.log) as f:
            captured = json.load(f)
        argv = captured["args"]
        self.assertEqual(argv, ["-p", "--mode", "json", *consult.PI_TEXT_FLAGS,
                                "--system-prompt", consult.PI_TEXT_SYSTEM])
        self.assertEqual(captured["prompt"], "Public input:\n" + prompt)
        self.assertNotIn(prompt, json.dumps(argv) + output + error)
        self.assertNotIn("do-not-log-this", json.dumps(argv) + output + error)

    def test_missing_flags_and_failures_are_closed_and_redacted(self):
        for settings in ({"FAKE_HELP": "--no-tools"}, {"FAKE_HELP_RC": "1"}, {"FAKE_VERSION": "0.74.0"},
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
                                 "FAKE_VERSION": "0.73.1", "FAKE_RC": "0", "FAKE_STDERR": "", "FAKE_EVENTS": events()})

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

    def test_child_inherits_original_signal_mask(self):
        original = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGUSR1})
        try:
            for operation in (signal.SIG_UNBLOCK, signal.SIG_BLOCK):
                with self.subTest(operation=operation):
                    signal.pthread_sigmask(operation, {signal.SIGTERM})
                    expected = signal.pthread_sigmask(signal.SIG_BLOCK, set())
                    child = ("import json,signal; print(json.dumps(sorted(int(s) for s in "
                             "signal.pthread_sigmask(signal.SIG_BLOCK, set()))))")
                    code, output = consult._pi_run([sys.executable, "-c", child], 5)
                    self.assertEqual(code, 0)
                    self.assertEqual(json.loads(output), sorted(int(s) for s in expected))
                    self.assertEqual(signal.pthread_sigmask(signal.SIG_BLOCK, set()), expected)
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, original)

    def test_child_terminates_normally_on_sigterm(self):
        original = signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGTERM})
        try:
            child = "import os,signal; os.kill(os.getpid(), signal.SIGTERM); print('still running')"
            code, output = consult._pi_run([sys.executable, "-c", child], 5)
            self.assertEqual((code, output), (-signal.SIGTERM, ""))
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, original)

    def test_timeout_and_overflow_kill_child_and_grandchild(self):
        marker = os.path.join(self.tmp.name, "grandchild")
        child = '''import subprocess, sys, time
p = subprocess.Popen([sys.executable, "-c", "import os,sys,time; open(sys.argv[1], 'w').write(str(os.getpid())); time.sleep(30)", sys.argv[1]])
while not __import__('os').path.exists(sys.argv[1]): time.sleep(0.01)
if sys.argv[2] == 'overflow': print('x' * 4097, flush=True)
time.sleep(30)
'''
        for mode, error in (("timeout", subprocess.TimeoutExpired), ("overflow", ValueError)):
            with self.subTest(mode=mode):
                if os.path.exists(marker):
                    os.unlink(marker)
                with self.assertRaises(error):
                    consult._pi_run([sys.executable, "-c", child, marker, mode], 1, limit=4096)
                with open(marker) as f:
                    pid = int(f.read())
                with self.assertRaises(ProcessLookupError):
                    os.kill(pid, 0)

    def test_unknown_identity_never_signals_and_retains_slot(self):
        process = types.SimpleNamespace(pid=12345)
        with mock.patch.object(consult.os, "waitid", side_effect=ChildProcessError), \
                mock.patch.object(consult.os, "killpg") as killpg:
            with self.assertRaises(consult._PiCleanupUncertain):
                consult._pi_cleanup(process)
            killpg.assert_not_called()
        original = consult._pi_run
        def fail_cleanup(command, timeout, **kwargs):
            if command[-1] not in ("--help", "--version"):
                kwargs["cleanup_state"][0] = False
                raise consult._PiCleanupUncertain()
            return original(command, timeout, **kwargs)
        with mock.patch.object(consult, "_pi_run", side_effect=fail_cleanup):
            code, _, error = self.invoke()
        self.assertEqual(code, 1)
        self.assertIn("slot retained", error)
        slots = os.listdir(self.core.RUNS)
        self.assertEqual(len(slots), 1)
        self.assertEqual(self.core.jload(os.path.join(self.core.RUNS, slots[0], "status.json"))["state"], "running")

    def test_cleanup_leader_exits_between_waitid_and_getpgid(self):
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                                   start_new_session=True)
        waitid = os.waitid
        calls = [0]
        def delayed_waitid(*args):
            calls[0] += 1
            if calls[0] == 2:
                return None  # Model the transient exit state observed on macOS.
            return waitid(*args)
        def exit_before_getpgid(pid):
            os.kill(pid, signal.SIGKILL)
            deadline = time.monotonic() + 5
            while waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is None:
                if time.monotonic() >= deadline:
                    self.fail("child did not exit")
                time.sleep(0.01)
            raise ProcessLookupError()
        try:
            with mock.patch.object(consult.os, "waitid", side_effect=delayed_waitid), \
                    mock.patch.object(consult.os, "getpgid", side_effect=exit_before_getpgid):
                consult._pi_cleanup(process)
            self.assertIsNotNone(process.returncode)
            self.assertGreaterEqual(calls[0], 3)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()

    def test_cleanup_missing_group_with_live_or_unknown_child_is_uncertain(self):
        process = types.SimpleNamespace(pid=12345)
        for second in (None, ChildProcessError()):
            with self.subTest(second=second), \
                    mock.patch.object(consult.os, "waitid", side_effect=[None, second]), \
                    mock.patch.object(consult.os, "getpgid", side_effect=ProcessLookupError), \
                    mock.patch.object(consult.time, "monotonic", side_effect=[0, 3]), \
                    mock.patch.object(consult.os, "killpg") as killpg:
                with self.assertRaises(consult._PiCleanupUncertain):
                    consult._pi_cleanup(process)
                killpg.assert_not_called()

    def test_cancellation_kills_grandchild(self):
        marker = os.path.join(self.tmp.name, "cancel-grandchild")
        runner = '''import signal, sys
sys.path.insert(0, sys.argv[1])
import openswarm_consult as c
def stop(*args): raise KeyboardInterrupt()
signal.signal(signal.SIGTERM, stop)
child = "import subprocess,sys,time; subprocess.Popen([sys.executable, '-c', \\\"import os,sys,time; open(sys.argv[1], 'w').write(str(os.getpid())); time.sleep(30)\\\", sys.argv[1]]); time.sleep(30)"
try: c._pi_run([sys.executable, '-c', child, sys.argv[2]], 20)
except KeyboardInterrupt: pass
'''
        bin_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "bin")
        process = subprocess.Popen([sys.executable, "-c", runner, bin_dir, marker],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 5
            while not os.path.exists(marker) and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(os.path.exists(marker))
            with open(marker) as f:
                pid = int(f.read())
            process.terminate()
            _, stderr = process.communicate(timeout=8)
            self.assertEqual(process.returncode, 0, stderr)
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()

    def test_cancelled_waiter_is_not_admitted_when_capacity_is_free(self):
        slot = os.path.join(self.core.RUNS, "cancelled")
        self.core.set_status(slot, state="queued", stop_requested=1)
        self.assertFalse(self.core.reserve_provider_slot(slot, "pi", None, {"concurrency": {"total": 1}},
                                                        keep_waiting=lambda: False))
        self.assertEqual(self.core.jload(os.path.join(slot, "status.json"))["state"], "queued")

    def test_dead_runner_reservation_is_not_discarded(self):
        holder = os.path.join(self.core.RUNS, "dead-runner")
        waiter = os.path.join(self.core.RUNS, "waiter")
        self.core.set_status(holder, id="dead-runner", state="running", runner_pid=99999999,
                             backend="pi", concurrency_keys=["total", "pi"])
        checks = iter((True, False))
        self.assertFalse(self.core.reserve_provider_slot(
            waiter, "pi", None, {"concurrency": {"total": 1}}, keep_waiting=lambda: next(checks)))
        self.assertEqual(self.core.jload(os.path.join(waiter, "status.json"))["state"], "queued")
        self.assertEqual(self.core.jload(os.path.join(holder, "status.json"))["state"], "running")

    def test_cli_path_uses_fake_pi_and_releases_slot(self):
        home = os.path.join(self.tmp.name, "unused-home")
        env = os.environ.copy()
        env.update(self.env, OPENSWARM_PI_BIN=self.binary, OPENSWARM_HOME=home)
        cli = os.path.join(os.path.dirname(os.path.dirname(__file__)), "bin", "openswarm")
        result = subprocess.run([sys.executable, cli, "pi-text", "--public-input-approved", "--text", "public"],
                                env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"text": "hello"})
        self.assertEqual(os.listdir(os.path.join(home, "runs")), [])

    def test_process_leases_cross_backends_failure_stop_and_unknown(self):
        cli = os.path.join(os.path.dirname(os.path.dirname(__file__)), "bin", "openswarm")
        os.makedirs(self.home)
        env = os.environ.copy()
        env.update(self.env, OPENSWARM_PI_BIN=self.binary, OPENSWARM_HOME=self.home)
        def policy(concurrency):
            with open(os.path.join(self.home, "policy.local.json"), "w") as f:
                json.dump({"concurrency": concurrency, "pi": {"provider": "synthetic"}}, f)
        def wait_for(predicate):
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                value = predicate()
                if value:
                    return value
                time.sleep(0.02)
            self.fail("timed out waiting for process state")
        def rows():
            root = os.path.join(self.home, "runs")
            if not os.path.isdir(root):
                return []
            result = []
            for name in os.listdir(root):
                try:
                    with open(os.path.join(root, name, "status.json")) as f:
                        result.append(json.load(f))
                except (FileNotFoundError, json.JSONDecodeError):
                    pass
            return result
        def start_text(**settings):
            run_env = dict(env, **settings)
            process = subprocess.Popen([sys.executable, cli, "pi-text", "--public-input-approved", "--text", "public"],
                                       env=run_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.addCleanup(lambda: process.poll() is None and process.kill())
            return process
        holder_code = '''
import importlib.machinery, importlib.util, os, sys, time
loader = importlib.machinery.SourceFileLoader("core", sys.argv[1])
spec = importlib.util.spec_from_loader(loader.name, loader)
core = importlib.util.module_from_spec(spec); loader.exec_module(core)
path = os.path.join(core.RUNS, "fixture-holder")
core.set_status(path, id="fixture-holder", runner_pid=os.getpid())
core.reserve_provider_slot(path, sys.argv[2], sys.argv[3] or None, core.load_policy())
while not os.path.exists(sys.argv[4]): time.sleep(0.02)
core.set_status(path, state="finished")
'''
        for limits, backend, provider in (({"total": 1}, "agy", "other"),
                                           ({"pi": 1}, "pi", "other"),
                                           ({"synthetic": 1}, "agy", "synthetic")):
            with self.subTest(limits=limits):
                policy(limits)
                release = os.path.join(self.tmp.name, "release")
                if os.path.exists(release):
                    os.unlink(release)
                holder = subprocess.Popen([sys.executable, "-c", holder_code, cli, backend, provider, release], env=env)
                self.addCleanup(lambda p=holder: p.poll() is None and p.kill())
                wait_for(lambda: any(r.get("id") == "fixture-holder" and r.get("state") == "running" for r in rows()))
                text = start_text(FAKE_RC="1")
                wait_for(lambda: any(r.get("id", "").startswith("pi-text-slot-") and r.get("state") == "queued" for r in rows()))
                self.assertFalse(os.path.exists(self.log))
                open(release, "w").close()
                self.assertEqual(holder.wait(timeout=5), 0)
                self.assertEqual(text.communicate(timeout=5)[0], "")
                self.assertEqual(text.returncode, 1)
                self.assertFalse(any(r.get("id", "").startswith("pi-text-slot-") for r in rows()))
                os.unlink(self.log)
        policy({"total": 1})
        unknown_dir = os.path.join(self.home, "runs", "unknown")
        os.makedirs(unknown_dir)
        with open(os.path.join(unknown_dir, "status.json"), "w") as f:
            json.dump({"id": "unknown", "state": "running", "backend": "agy"}, f)
        text = start_text()
        queued = wait_for(lambda: next((r for r in rows() if r.get("id", "").startswith("pi-text-slot-") and r.get("state") == "queued"), None))
        stop = subprocess.run([sys.executable, cli, "stop", queued["id"]], env=env, capture_output=True, text=True, timeout=5)
        self.assertEqual(stop.returncode, 0, stop.stderr)
        text.communicate(timeout=5)
        self.assertNotEqual(text.returncode, 0)
        self.assertFalse(os.path.exists(self.log))
        self.assertFalse(any(r.get("id", "").startswith("pi-text-slot-") for r in rows()))
        self.assertTrue(any(r.get("id") == "unknown" and r.get("state") == "running" for r in rows()))
        with open(os.path.join(unknown_dir, "status.json"), "w") as f:
            json.dump({"id": "unknown", "state": "finished"}, f)
        text = start_text(FAKE_SLEEP="30")
        running = wait_for(lambda: next((r for r in rows() if r.get("id", "").startswith("pi-text-slot-") and r.get("state") == "running"), None))
        wait_for(lambda: os.path.exists(self.log))
        stop = subprocess.run([sys.executable, cli, "stop", running["id"]], env=env, capture_output=True, text=True, timeout=5)
        self.assertEqual(stop.returncode, 0, stop.stderr)
        _, stop_error = text.communicate(timeout=5)
        self.assertNotEqual(text.returncode, 0)
        self.assertFalse(any(r.get("id", "").startswith("pi-text-slot-") for r in rows()), stop_error)

    def test_input_bounds_and_approval_before_launch(self):
        for text, approved in (("", True), ("x", False), ("é" * 8001, True), ("x\x00", True),
                               ("x\n", True), ("x\ufeff", True)):
            with self.subTest(text=text[:5], approved=approved):
                self.assertEqual(self.invoke(text, approved)[0], 2)
                self.assertFalse(os.path.exists(self.log))
        self.assertEqual(self.invoke("é" * 8000)[0], 0)
        with open(self.log) as f:
            self.assertEqual(json.load(f)["prompt"], "Public input:\n" + "é" * 8000)

    def test_stdin_timeout_and_failure_do_not_disclose_input(self):
        secret = "synthetic-public-marker"
        original = consult._pi_run
        for settings in ({"FAKE_RC": "1", "FAKE_STDERR": secret}, {"FAKE_SLEEP": "30"}):
            with self.subTest(settings=settings), mock.patch.object(consult, "_pi_run") as run:
                self.env.update(settings)
                def short_timeout(command, timeout, **kwargs):
                    return original(command, min(timeout, 0.5) if kwargs.get("input_bytes") else timeout, **kwargs)
                run.side_effect = short_timeout
                code, output, error = self.invoke(secret)
                self.assertEqual(code, 1)
                self.assertNotIn(secret, output + error)
                with open(self.log) as f:
                    self.assertNotIn(secret, json.dumps(json.load(f)["args"]))
                self.env.update({"FAKE_RC": "0", "FAKE_STDERR": "", "FAKE_SLEEP": ""})

    def test_stdin_blocked_reader_and_closed_pipe_fail(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            consult._pi_run([sys.executable, "-c", "import time; time.sleep(30)"],
                            0.5, input_bytes=b"x" * (2 * 1024 * 1024))
        with self.assertRaises(BrokenPipeError):
            consult._pi_run([sys.executable, "-c", "import os,time; os.close(0); time.sleep(1)"],
                            5, input_bytes=b"x" * (2 * 1024 * 1024))


if __name__ == "__main__":
    unittest.main()
