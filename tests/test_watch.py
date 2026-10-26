import argparse
import contextlib
import io
import json
import os
import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock

import _load

xs = _load.load()


def update(kind, **fields):
    return {"method": "session/update", "params": {"update": {"sessionUpdate": kind, **fields}}}


class WatchTests(unittest.TestCase):
    def test_pi_only_emits_completed_assistant_messages(self):
        messages = xs.WatchMessages()
        self.assertEqual(messages.feed({"type": "message_update", "assistantMessageEvent": {
            "type": "text_delta", "delta": "hello"}}), [])
        self.assertEqual(messages.feed({"type": "message_end", "message": {
            "role": "user", "content": [{"type": "text", "text": "ignore"}]}}), [])
        self.assertEqual(messages.feed({"type": "message_end", "message": {
            "role": "assistant", "content": [{"type": "text", "text": "hello world"}]}}),
            [("msg", "hello world")])

    def test_cursor_merges_at_tools_and_prompt_end(self):
        messages = xs.WatchMessages()
        for chunk in ("hello", " world"):
            self.assertEqual(messages.feed(update("agent_message_chunk", content={"type": "text", "text": chunk})), [])
        self.assertEqual(messages.feed(update("tool_call", toolCallId="one", title="edit")),
                         [("msg", "hello world"), ("cmd", "edit")])
        self.assertEqual(messages.feed(update("tool_call_update", toolCallId="one", title="edit")), [])
        messages.feed(update("agent_message_chunk", content={"type": "text", "text": "finished"}))
        self.assertEqual(messages.feed({"id": "prompt-1", "result": {"stopReason": "end_turn"}}),
                         [("msg", "finished")])
        self.assertEqual(messages.flush(), [])

    def test_cursor_one_shot_and_codex_messages(self):
        messages = xs.WatchMessages()
        for text in ("first", " second"):
            self.assertEqual(messages.feed({"type": "assistant", "message": {"content": [
                {"type": "text", "text": text}]}}), [])
        self.assertEqual(messages.feed({"type": "result", "result": "first second"}), [("msg", "first second")])
        self.assertEqual(messages.feed({"type": "item.completed", "item": {
            "type": "agent_message", "text": "codex"}}), [("msg", "codex")])

    def test_quiet_keeps_alerts_and_heartbeat_has_lane_diff_and_last_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            repo = root / "lanes" / "L1" / "wt"
            repo.mkdir(parents=True)
            def git(*args):
                return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
            git("init", "-q")
            (repo / "a").write_text("base\n")
            git("add", "a")
            git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "base")
            (repo / "a").write_text("base\nchange\n")
            rdir = root / "runs" / "r1"
            rdir.mkdir(parents=True)
            events = rdir / "events.jsonl"
            events.write_text("\n".join(json.dumps(e) for e in [
                {"type": "message_end", "message": {"role": "assistant", "content": [
                    {"type": "text", "text": "last message " + "x" * 250}]}},
                {"type": "tool_execution_start", "args": {"command": "git push"}},
                {"type": "error", "error": "expected test error"}]) + "\n")
            xs.jsave(str(rdir / "status.json"), {"state": "running", "backend": "pi", "lane": "L1",
                                                "events": str(events), "started": xs.NOW() - 120})
            args = argparse.Namespace(run_id=["r1"], label=None, quiet=True, risk=[], from_end=False,
                                      heartbeat_minutes=-1, stall_minutes=-1, max_minutes=-1)
            output = io.StringIO()
            with mock.patch.object(xs, "DATA", tmp), mock.patch.object(xs, "RUNS", str(root / "runs")), \
                    contextlib.redirect_stdout(output):
                xs.cmd_watch(args, {})
            lines = output.getvalue().splitlines()
            self.assertTrue(any("RISK[" in line for line in lines))
            self.assertTrue(any("ERROR:" in line for line in lines))
            beat = next(line for line in lines if "HEARTBEAT:" in line)
            self.assertIn("2m/", beat)
            self.assertIn("cmds=1", beat)
            self.assertIn("1 file changed", beat)
            self.assertTrue(beat.endswith(xs.short("last message " + "x" * 250, 200)))
            self.assertFalse(any(token in output.getvalue() for token in ("MSG:", "CMD:", "WATCH-EXPIRED:")))


if __name__ == "__main__":
    unittest.main()
