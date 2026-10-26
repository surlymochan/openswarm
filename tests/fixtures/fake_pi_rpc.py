#!/usr/bin/env python3
"""Small Pi JSON-lines RPC subset for resident lane tests."""
import json
import os
import select
import sys
import time


def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def response(req, command, success=True):
    send({"id": req.get("id"), "type": "response", "command": command, "success": success})


def record(event):
    path = os.environ.get("FAKE_PI_RPC_LOG")
    if path:
        with open(path, "a") as stream:
            stream.write(json.dumps(event) + "\n")


prompt_count = 0
rate_mode = None
for line in sys.stdin:
    try:
        req = json.loads(line)
    except ValueError:
        continue
    kind = req.get("type")
    record(req)
    if kind == "new_session":
        if "--session-dir" not in sys.argv:
            raise RuntimeError("missing explicit session directory")
        sessions = sys.argv[sys.argv.index("--session-dir") + 1]
        if os.path.commonpath([os.path.realpath(sessions), os.getcwd()]) == os.getcwd():
            raise RuntimeError("session directory inside worktree")
        response(req, kind)
    elif kind == "prompt":
        prompt = req.get("message", "")
        prompt_count += 1
        if "LANE_TEST_RATE_LIMIT_ONCE" in prompt:
            rate_mode = "once"
        elif "LANE_TEST_RATE_LIMIT_ALWAYS" in prompt:
            rate_mode = "always"
        elif "LANE_TEST_RATE_LIMIT_PARTIAL" in prompt:
            rate_mode = "partial"
        if rate_mode in ("always", "partial") or (rate_mode == "once" and prompt_count == 1):
            if rate_mode == "partial" and prompt_count == 1:
                with open("rate_limited_partial.txt", "w") as f:
                    f.write("partial work\n")
            message = {"role": "assistant", "content": [], "usage": {"input": 1, "output": 0},
                       "stopReason": "error", "errorMessage": "429 rate_limit_error: rate limit exceeded"}
            send({"type": "message_end", "message": message})
            send({"type": "agent_end", "messages": [message]})
            continue
        if "LANE_TEST_SESSION_LEAK" in prompt:
            with open("2026-10-26T10-17-04-024Z_12345678-1234-1234-1234-123456789abc.jsonl", "w") as f:
                f.write("private session")
            os.system("git add -- *.jsonl")
        response(req, kind)
        steered = ""
        aborted = False
        if "LANE_TEST_RISK" in prompt:
            send({"type": "tool_execution_start", "toolName": "bash", "args": {"command": "git push secret-value"}})
        wait_seconds = 1 if "LANE_TEST_WAIT_SHORT" in prompt else 20 if "LANE_TEST_WAIT" in prompt else 0.25
        wait_until = time.time() + wait_seconds
        while time.time() < wait_until:
            ready, _, _ = select.select([sys.stdin], [], [], 0.1)
            if not ready:
                continue
            control_line = sys.stdin.readline()
            if not control_line:
                break
            control = json.loads(control_line)
            if control.get("type") == "steer":
                steered = control.get("message", "")
                response(control, "steer")
                break
            if control.get("type") == "abort":
                aborted = True
                response(control, "abort")
                break
        if aborted:
            send({"type": "agent_end", "messages": []})
            continue
        if "LANE_TEST_NO_CHANGE" not in prompt:
            with open("lane_result.txt", "w") as f:
                f.write(steered or "completed")
        result = {"status": "done", "summary": "fake Pi completed", "files_changed": ["lane_result.txt"],
                  "tests": "fake executor", "followups": []}
        message = {"role": "assistant", "content": [{"type": "text", "text": json.dumps(result)}],
                   "usage": {"input": 3, "output": 2}, "stopReason": "stop"}
        send({"type": "message_end", "message": message})
        send({"type": "agent_end", "messages": [message]})
