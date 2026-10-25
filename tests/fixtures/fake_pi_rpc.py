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


for line in sys.stdin:
    try:
        req = json.loads(line)
    except ValueError:
        continue
    kind = req.get("type")
    if kind == "new_session":
        response(req, kind)
    elif kind == "prompt":
        prompt = req.get("message", "")
        response(req, kind)
        steered = ""
        aborted = False
        wait_until = time.time() + (20 if "LANE_TEST_WAIT" in prompt else 0.25)
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
