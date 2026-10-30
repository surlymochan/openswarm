import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import _load

xs = _load.load()
STUB = Path(_load.HERE) / "fixtures" / "fake_fleet_openswarm.py"


class FakeFleetFixture:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="openswarm-batch-")
        self.root = Path(self.tmp.name)
        self.data = self.root / "planner"
        self.cards = self.root / "cards"
        self.cards.mkdir()
        self.repo = self.root / "local"
        self.repo.mkdir()
        self.git(self.repo, "init", "-q")
        # Git may detach automatic maintenance after a commit or fetch. These short-lived
        # fixture repos are deleted immediately after the test and need no background work.
        self.git(self.repo, "config", "maintenance.auto", "false")
        self.git(self.repo, "config", "gc.auto", "0")
        (self.repo / "base").write_text("base")
        self.git(self.repo, "add", "base")
        self.git(self.repo, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "base")
        self.initial = self.git(self.repo, "rev-parse", "HEAD")
        self.hosts = {"local": {"name": "local", "kind": "local", "repos": {"project": str(self.repo)}}}
        for name in ("b1", "b2"):
            repo = self.root / name / "repo"
            repo.mkdir(parents=True)
            self.git(repo, "init", "-q")
            self.git(repo, "config", "maintenance.auto", "false")
            self.git(repo, "config", "gc.auto", "0")
            bundle = self.root / "initial.bundle"
            bundle.write_bytes(subprocess.check_output(["git", "-C", str(self.repo), "bundle", "create", "-", "HEAD"]))
            self.git(repo, "fetch", str(bundle), "HEAD")
            self.git(repo, "checkout", "-q", "--detach", "FETCH_HEAD")
            self.hosts[name] = {"name": name, "kind": "instabox", "box": name + "-box",
                                "openswarm": str(STUB), "repos": {"project": str(repo)}}
        (self.repo / "new-base").write_text("local only")
        self.git(self.repo, "add", "new-base")
        self.git(self.repo, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "new base")
        self.base = self.git(self.repo, "rev-parse", "HEAD")
        STUB.chmod(0o755)
        self.data.mkdir()
        (self.data / "fleet.json").write_text(json.dumps({"hosts": list(self.hosts.values())}))
        self.calls = []
        self.patches = [mock.patch.object(xs, "DATA", str(self.data)),
                        mock.patch.object(xs, "host_run", side_effect=self.transport),
                        mock.patch.object(xs, "sh", side_effect=self.shell)]
        for patch in self.patches:
            patch.start()
        self.addCleanup(self.tmp.cleanup)
        for patch in self.patches:
            self.addCleanup(patch.stop)

    def git(self, repo, *args):
        return subprocess.check_output(["git", "-C", str(repo), *args], stderr=subprocess.PIPE).decode().strip()

    def shell(self, command, **kwargs):
        if command[:2] == ["instabox", "ls"]:
            states = [{"name": host["box"], "status": "Running"} for host in self.hosts.values()
                      if host["kind"] == "instabox"]
            return 0, json.dumps(states), ""
        return 0, "", ""

    def transport(self, host, command, timeout=60, input=None):
        self.calls.append((host["name"], command))
        home = self.data if host["name"] == "local" else self.root / host["name"] / "data"
        proc = subprocess.run(["sh", "-c", command], capture_output=True, input=input, timeout=timeout,
                              env={**os.environ, "OPENSWARM_HOME": str(home)})
        return proc.returncode, proc.stdout, proc.stderr

    def args(self, **fields):
        values = dict(batch_action=None, id=None, batch_id=None, repo="project", cards=str(self.cards), hosts="b1,b2",
                      per_host=1, backend="pi", model="cheap", effort=None, verify="true", budget_minutes=1,
                      base=None, detach=False, poll_seconds=0.001)
        values.update(fields)
        return argparse.Namespace(**values)

    def run_batch(self, **fields):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as exited:
            xs.cmd_batch(self.args(**fields), xs.load_policy())
        paths = list((self.data / "batches").glob("*/summary.json"))
        self.assertEqual(len(paths), 1)
        return exited.exception.code, json.loads(paths[0].read_text()), output.getvalue()


class BatchTests(FakeFleetFixture, unittest.TestCase):
    def test_batch_launch_passes_path_hints_to_remote_run(self):
        card = self.cards / "one.md"
        card.write_text("Update src/widget.py")
        spec = {"hosts": {"b1": self.hosts["b1"]}, "repos": {"b1": str(self.repo)},
                "base": self.base, "paths": ["src/widget.py"], "card_type": "fix"}
        row = {"host": "b1", "path": str(card), "run_id": "run1"}
        with mock.patch.object(xs, "host_python", side_effect=[b"remote-card.md", b""]), \
                mock.patch.object(xs, "host_require", side_effect=lambda host, output, context: output), \
                mock.patch.object(xs, "host_json", return_value={"id": "run1"}) as host_json:
            self.assertEqual(xs.batch_launch(spec, row), "run1")
        remote_args = host_json.call_args.args[1]
        self.assertEqual(remote_args[remote_args.index("--paths") + 1:], ["src/widget.py"])

    def test_budget_refuses_batch_before_launch(self):
        (self.cards / "one.md").write_text("one")
        policy = xs.deep_merge(xs.load_policy(), {"budget": {"daily_executor_minutes": 0}})
        output = io.StringIO()
        with contextlib.redirect_stdout(output), mock.patch.object(xs, "execute_batch") as execute, \
                self.assertRaises(SystemExit) as e:
            xs.cmd_batch(self.args(), policy)
        self.assertEqual(e.exception.code, 3)
        self.assertEqual(json.loads(output.getvalue())["error"], "budget_exceeded_daily")
        execute.assert_not_called()

    def test_markdown_summary_preserves_zero_numeric_cells(self):
        summary = {"id": "zeros", "state": "finished", "cards": [{
            "card": "zero.md", "host": "b1", "status": "done", "verify_rc": 0,
            "branch": "branch", "commit": "commit", "seconds": 0, "summary": "ok",
        }]}
        directory = self.data / "batches" / "zeros"
        directory.mkdir(parents=True)
        xs.batch_save(summary)
        markdown = (directory / "summary.md").read_text()
        self.assertIn("| zero.md | b1 | done | 0 | branch | commit | 0 | ok |", markdown)

    def test_distribution_queueing_bundle_fetch_failure_and_summaries(self):
        for i, text in enumerate(("card zero", "FAIL card one", "card two", "card three", "card four")):
            (self.cards / f"{i}.md").write_text(text)
        rc, summary, output = self.run_batch()
        self.assertEqual(rc, 1)
        self.assertEqual(summary["state"], "finished")
        self.assertEqual([r["host"] for r in summary["cards"]], ["b1", "b2", "b1", "b2", "b1"])
        self.assertEqual([r["status"] for r in summary["cards"]], ["done", "failed", "done", "done", "done"])
        self.assertEqual(summary["cards"][1]["verify_rc"], 1)
        for host in ("b1", "b2"):
            calls = [json.loads(line) for line in (self.root / host / "data" / "calls.jsonl").read_text().splitlines()]
            for call in calls:
                if call["event"] == "start":
                    self.assertEqual(call["active_before"], 0)
                    self.assertEqual(call["args"]["base"], self.base)
                    self.assertEqual(call["args"]["backend"], "pi")
            self.assertEqual(self.git(self.root / host / "repo", "cat-file", "-t", self.base), "commit")
        for row in summary["cards"]:
            self.assertEqual(self.git(self.repo, "rev-parse", row["branch"]), row["commit"])
            self.assertEqual(self.git(self.repo, "show", row["branch"] + ":new-base"), "local only")
            remote_branches = self.git(self.root / row["host"] / "repo", "branch", "--list", row["branch"])
            self.assertEqual(remote_branches, "")
            self.assertEqual(output.count(f"{row['card']} @{row['host']}: running (running)"), 1)
        markdown = (self.data / "batches" / summary["id"] / "summary.md").read_text()
        self.assertIn("FAIL card one", markdown)
        self.assertIn("Verify rc", markdown)
        wakes = [call for call in xs.sh.call_args_list if call.args[0][:2] == ["instabox", "wake"]]
        self.assertEqual(len(wakes), 2)
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD"), self.base)
        self.assertEqual(self.git(self.repo, "remote"), "")

    def test_multiple_slots_and_explicit_base(self):
        for i in range(6):
            (self.cards / f"{i}.md").write_text(f"card {i}")
        rc, summary, _ = self.run_batch(per_host=2, base=self.initial)
        self.assertEqual(rc, 0)
        for name in ("b1", "b2"):
            calls = [json.loads(line) for line in (self.root / name / "data" / "calls.jsonl").read_text().splitlines()]
            starts = [call for call in calls if call["event"] == "start"]
            self.assertEqual([call["active_before"] for call in starts], [0, 1, 0])
            self.assertTrue(all(call["args"]["base"] == self.initial for call in starts))
        self.assertEqual(summary["base"], self.initial)

    def test_batch_provider_cap_queues_across_fleet_hosts(self):
        for name in ("b1", "b2"):
            self.hosts[name]["kind"] = "local"
        (self.data / "fleet.json").write_text(json.dumps({"hosts": list(self.hosts.values())}))
        (self.data / "policy.local.json").write_text(json.dumps({"concurrency": {"pi": 1}}))
        (self.cards / "one.md").write_text("first card")
        (self.cards / "two.md").write_text("second card")
        rc, summary, _ = self.run_batch()
        self.assertEqual(rc, 0)
        self.assertEqual([row["host"] for row in summary["cards"]], ["b1", "b2"])
        starts, finishes = [], []
        for name in ("b1", "b2"):
            rows = [json.loads(line) for line in (self.root / name / "data" / "calls.jsonl").read_text().splitlines()]
            starts.extend(row["ts"] for row in rows if row["event"] == "start")
            finishes.extend(row["ts"] for row in rows if row["event"] == "finish")
        self.assertEqual(len(starts), 2)
        self.assertEqual(len(finishes), 2)
        self.assertLess(finishes[0], starts[1])

    def test_batch_total_cap_queues_across_fleet_hosts(self):
        for name in ("b1", "b2"):
            self.hosts[name]["kind"] = "local"
        (self.data / "fleet.json").write_text(json.dumps({"hosts": list(self.hosts.values())}))
        (self.data / "policy.local.json").write_text(json.dumps({"concurrency": {"total": 1}}))
        (self.cards / "one.md").write_text("first card")
        (self.cards / "two.md").write_text("second card")
        rc, summary, _ = self.run_batch()
        self.assertEqual(rc, 0)
        starts, finishes = [], []
        for name in ("b1", "b2"):
            rows = [json.loads(line) for line in (self.root / name / "data" / "calls.jsonl").read_text().splitlines()]
            starts.extend(row["ts"] for row in rows if row["event"] == "start")
            finishes.extend(row["ts"] for row in rows if row["event"] == "finish")
        self.assertEqual(len(starts), 2)
        self.assertEqual(len(finishes), 2)
        self.assertLess(finishes[0], starts[1])

    def test_unreachable_active_run_does_not_release_slot_for_queued_card(self):
        for i in range(3):
            (self.cards / f"{i}.md").write_text(f"card {i}")
        def offline(host, command, **kwargs):
            if host["name"] == "b1" and " _run-info " in command:
                return 255, b"", b"offline"
            return self.transport(host, command, **kwargs)
        with mock.patch.object(xs, "host_run", side_effect=offline):
            rc, summary, _ = self.run_batch()
        self.assertEqual(rc, 1)
        starts = [json.loads(line) for line in (self.root / "b1" / "data" / "calls.jsonl").read_text().splitlines()]
        self.assertEqual(sum(row["event"] == "start" for row in starts), 1)
        self.assertEqual(summary["cards"][1]["status"], "done")
        self.assertEqual(summary["cards"][2]["status"], "failed")
        self.assertNotIn("run_id", summary["cards"][2])

    def test_detach_spawns_batch_runner_and_status_reads_summary(self):
        (self.cards / "card.md").write_text("card")
        output = io.StringIO()
        real_popen = subprocess.Popen
        spawned = []
        def spawn_batch(argv, **kwargs):
            if "--batch-id" in argv:
                spawned.append((argv, kwargs))
                return mock.Mock(pid=os.getpid())
            return real_popen(argv, **kwargs)
        with mock.patch.object(xs.subprocess, "Popen", side_effect=spawn_batch), \
                contextlib.redirect_stdout(output):
            xs.cmd_batch(self.args(detach=True), xs.load_policy())
        bid = json.loads(output.getvalue())["id"]
        self.assertIn("--batch-id", spawned[0][0])
        self.assertTrue(spawned[0][1]["start_new_session"])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            xs.cmd_batch(self.args(batch_action="status", id=bid), xs.load_policy())
        self.assertEqual(json.loads(output.getvalue())["state"], "queued")
        # Exercise the exact worker path used by the detached child with the fake transport.
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as exited:
            xs.cmd_batch(self.args(batch_id=bid), xs.load_policy())
        self.assertEqual(exited.exception.code, 0)
        self.assertEqual(json.loads((self.data / "batches" / bid / "summary.json").read_text())["state"], "finished")

    def test_detached_cli_runs_entire_batch_in_background(self):
        (self.cards / "card.md").write_text("background card")
        for name in ("b1", "b2"):
            self.hosts[name]["kind"] = "local"
        (self.data / "fleet.json").write_text(json.dumps({"hosts": list(self.hosts.values())}))
        proc = subprocess.run([sys.executable, str(Path(_load.ROOT) / "bin" / "openswarm"), "batch",
                               "--repo", "project", "--cards", str(self.cards), "--hosts", "b1,b2", "--detach",
                               "--poll-seconds", "0.01"], capture_output=True, text=True, timeout=10,
                              env={**os.environ, "OPENSWARM_HOME": str(self.data)})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        reply = json.loads(proc.stdout)
        path = self.data / "batches" / reply["id"] / "summary.json"
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            summary = json.loads(path.read_text())
            if summary["state"] in ("finished", "failed"):
                break
            time.sleep(0.05)
        else:
            self.fail("detached batch did not finish")
        self.assertEqual(summary["state"], "finished", summary)
        self.assertEqual(summary["cards"][0]["status"], "done", summary)
        self.assertEqual(self.git(self.repo, "rev-parse", summary["cards"][0]["branch"]), summary["cards"][0]["commit"])


if __name__ == "__main__":
    unittest.main()
