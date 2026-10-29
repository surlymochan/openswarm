"""Cross-process admission and detached stop regressions, with isolated local data."""
import json
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
CLI = ROOT / "bin" / "openswarm"


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="admission-test-", dir=ROOT)
        self.root = pathlib.Path(self.tmp.name)
        self.data = self.root / "data"
        self.runs = self.data / "runs"
        self.runs.mkdir(parents=True)
        self.env = dict(os.environ, OPENSWARM_HOME=str(self.data),
                        OPENSWARM_STATE_FILE=str(ROOT / "tests/fixtures/state.json"))

    def tearDown(self):
        self.tmp.cleanup()

    def wait_for(self, predicate, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(.05)
        self.fail("timed out waiting for state")

    def test_simultaneous_backends_share_host_cap_and_unknown_reserves(self):
        code = '''import importlib.machinery, importlib.util, os, sys, time
loader = importlib.machinery.SourceFileLoader("xs", sys.argv[1])
spec = importlib.util.spec_from_loader("xs", loader)
xs = importlib.util.module_from_spec(spec); loader.exec_module(xs)
rdir = os.path.join(xs.RUNS, sys.argv[2]); os.makedirs(rdir)
xs.set_status(rdir, id=sys.argv[2], state="queued", runner_pid=os.getpid())
while not os.path.exists(os.path.join(xs.DATA, "go")): time.sleep(.01)
deadline = time.monotonic() + .7
print(xs.reserve_provider_slot(rdir, sys.argv[3], None, {"concurrency":{"hosts":{sys.argv[4]:2}}}, keep_waiting=lambda: time.monotonic() < deadline), flush=True)
'''
        host = socket.gethostname().split(".")[0].lower()
        procs = [subprocess.Popen([sys.executable, "-c", code, str(CLI), str(i), backend, host],
                                  env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for i, backend in enumerate(("pi", "codex", "agy", "cursor"))]
        try:
            self.wait_for(lambda: len(list(self.runs.glob('*/status.json'))) == 4)
            (self.data / "go").touch()
            results = [p.communicate(timeout=10) for p in procs]
            self.assertEqual([p.returncode for p in procs], [0] * 4, results)
            self.assertEqual(sum(out.strip() == "True" for out, _ in results), 2, results)
            # Even a dead runner and unknown state retain the slot.
            running = [p for p in self.runs.glob('*/status.json') if json.loads(p.read_text())['state'] == 'running']
            row = json.loads(running[0].read_text()); row['state'] = 'unknown'; row['runner_pid'] = 99999999
            running[0].write_text(json.dumps(row))
            fifth = subprocess.run([sys.executable, "-c", code, str(CLI), "fifth", "hermes", host],
                                   env=self.env, capture_output=True, text=True, timeout=10)
            self.assertEqual(fifth.stdout.strip(), "False", fifth.stderr)
        finally:
            for p in procs:
                if p.poll() is None:
                    p.terminate(); p.wait(timeout=5)

    def test_stop_queued_detached_run_writes_result_and_exits(self):
        host = socket.gethostname().split(".")[0].lower()
        (self.data / 'policy.local.json').write_text(json.dumps({'concurrency': {'hosts': {host: 1}}}))
        holder = self.runs / 'holder'; holder.mkdir()
        (holder / 'status.json').write_text(json.dumps({'id': 'holder', 'state': 'running',
                                                        'backend': 'codex', 'runner_pid': 99999999}))
        command = [sys.executable, str(CLI), 'run', '--backend', 'pi', '--prompt', 'queued stop',
                   '--cwd', str(self.root), '--no-worktree', '--detach']
        started = subprocess.run(command, env=self.env, capture_output=True, text=True, timeout=15)
        self.assertEqual(started.returncode, 0, started.stderr)
        run = json.loads(started.stdout); rid = run['id']; rdir = self.runs / rid
        def status():
            try: return json.loads((rdir / 'status.json').read_text())
            except (OSError, ValueError): return {}
        self.wait_for(lambda: status().get('queue_reason'))
        stopped = subprocess.run([sys.executable, str(CLI), 'stop', rid], env=self.env,
                                 capture_output=True, text=True, timeout=10)
        self.assertEqual(stopped.returncode, 0, stopped.stderr)
        self.wait_for(lambda: status().get('state') == 'finished')
        self.assertEqual(json.loads((rdir / 'result.json').read_text())['status'], 'stopped')
        self.assertEqual(status()['status'], 'stopped')
        def runner_gone():
            try: os.kill(run['runner_pid'], 0); return False
            except ProcessLookupError: return True
        self.wait_for(runner_gone)

    def test_stop_running_detached_run_reaps_executor(self):
        binary = self.root / "fake-pi"
        binary.write_text('#!/usr/bin/env python3\nimport time\ntime.sleep(60)\n')
        binary.chmod(0o755)
        self.env['OPENSWARM_PI_BIN'] = str(binary)
        command = [sys.executable, str(CLI), 'run', '--backend', 'pi', '--prompt', 'running stop',
                   '--cwd', str(self.root), '--no-worktree', '--detach']
        started = subprocess.run(command, env=self.env, capture_output=True, text=True, timeout=15)
        self.assertEqual(started.returncode, 0, started.stderr)
        run = json.loads(started.stdout); rid = run['id']; rdir = self.runs / rid
        def status():
            try: return json.loads((rdir / 'status.json').read_text())
            except (OSError, ValueError): return {}
        executor_pid = self.wait_for(lambda: status().get('executor_pid'))
        stopped = subprocess.run([sys.executable, str(CLI), 'stop', rid], env=self.env,
                                 capture_output=True, text=True, timeout=10)
        self.assertEqual(stopped.returncode, 0, stopped.stderr)
        self.wait_for(lambda: status().get('state') == 'finished')
        self.assertEqual(json.loads((rdir / 'result.json').read_text())['status'], 'stopped')
        def gone(pid):
            try: os.kill(pid, 0); return False
            except ProcessLookupError: return True
        self.wait_for(lambda: gone(executor_pid) and gone(run['runner_pid']))


if __name__ == '__main__':
    unittest.main()
