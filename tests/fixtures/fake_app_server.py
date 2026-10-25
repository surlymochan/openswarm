#!/usr/bin/env python3
"""Small JSON-RPC app-server subset for the resident lane tests."""
import json
import os
import select
import sys
import time


def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def response(req, result=None):
    send({"jsonrpc": "2.0", "id": req.get("id"), "result": result or {}})


thread_id = "fake-thread"
turn_id = "fake-turn"
cwd = os.getcwd()

for line in sys.stdin:
    try:
        req = json.loads(line)
    except ValueError:
        continue
    method = req.get("method")
    params = req.get("params") or {}
    if method == "initialize":
        response(req, {"serverInfo": {"name": "fake-app-server", "version": "1"}})
    elif method == "initialized":
        continue
    elif method == "thread/start":
        cwd = params.get("cwd", cwd)
        response(req, {"thread": {"id": thread_id}, "model": params.get("model")})
    elif method == "turn/start":
        prompt = (params.get("input") or [{}])[0].get("text", "")
        if "LANE_TEST_CRASH" in prompt:
            os._exit(17)
        response(req, {"turn": {"id": turn_id, "status": "inProgress"}})
        send({"jsonrpc": "2.0", "method": "item/started", "params": {
            "threadId": thread_id, "turnId": turn_id,
            "item": {"id": "fake-command", "type": "commandExecution", "command": "python fake task"}}})
        steered = ""
        aborted = False
        wait_until = time.time() + (20 if "LANE_TEST_WAIT" in prompt else 0.25)
        while time.time() < wait_until:
            ready, _, _ = select.select([sys.stdin], [], [], 0.1)
            if not ready:
                continue
            control_line = sys.stdin.readline()
            if not control_line:
                os._exit(0)
            control = json.loads(control_line)
            if control.get("method") == "turn/steer":
                steered = (control.get("params", {}).get("input") or [{}])[0].get("text", "")
                response(control)
                break
            if control.get("method") == "turn/interrupt":
                aborted = True
                response(control)
                break
        if aborted:
            send({"jsonrpc": "2.0", "method": "turn/completed", "params": {
                "threadId": thread_id, "turn": {"id": turn_id, "status": "interrupted"}}})
            continue
        if "LANE_TEST_NO_CHANGE" not in prompt:
            with open(os.path.join(cwd, "lane_result.txt"), "w") as f:
                f.write(steered or "completed")
        result = {"status": "done", "summary": "fake app-server completed", "files_changed": ["lane_result.txt"],
                  "tests": "fake executor", "followups": []}
        send({"jsonrpc": "2.0", "method": "item/completed", "params": {
            "threadId": thread_id, "turnId": turn_id, "completedAtMs": int(time.time() * 1000),
            "item": {"id": "fake-message", "type": "agentMessage", "text": json.dumps(result)}}})
        send({"jsonrpc": "2.0", "method": "turn/completed", "params": {
            "threadId": thread_id, "turn": {"id": turn_id, "status": "completed", "usage": {"inputTokens": 2}}}})
