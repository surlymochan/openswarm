#!/usr/bin/env python3
"""Small ACP v1 subset used by resident Cursor lane tests."""
import json
import os
import select
import sys
import time

# The prompt asks for a JSON report with this wording; a prompt without it gets no report.
REPORT_MARKER = "your FINAL message must be ONLY a JSON object"


def send(message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def response(request, result=None):
    send({"jsonrpc": "2.0", "id": request.get("id"), "result": result or {}})


def record(message):
    path = os.environ.get("FAKE_CURSOR_ACP_LOG")
    if path:
        with open(path, "a") as stream:
            stream.write(json.dumps(message) + "\n")


def read_message(timeout=0.2):
    ready, _, _ = select.select([sys.stdin], [], [], timeout)
    if not ready:
        return None
    line = sys.stdin.readline()
    if not line:
        os._exit(0)
    try:
        message = json.loads(line)
        record(message)
        return message
    except ValueError:
        return {}


session_id = "fake-cursor-session"
cwd = os.getcwd()
record({"argv": sys.argv})
config_options = [{"id": "model", "name": "Model", "category": "model", "type": "select",
                   "currentValue": "other-model", "options": [
                       {"value": "composer-2.5", "name": "Composer 2.5"},
                       {"value": "other-model", "name": "Other model"}]}]
if os.environ.get("FAKE_CURSOR_ACP_MODELS"):
    config_options[0]["options"] = [{"value": value, "name": value}
                                    for value in json.loads(os.environ["FAKE_CURSOR_ACP_MODELS"])]
for line in sys.stdin:
    try:
        request = json.loads(line)
    except ValueError:
        continue
    method = request.get("method")
    params = request.get("params") or {}
    record(request)
    if method == "initialize":
        send({"jsonrpc": "2.0", "method": "fixture/unrelated", "params": {"before": method}})
        response(request, {"protocolVersion": 1, "agentCapabilities": {},
                           "agentInfo": {"name": "fake-cursor-agent", "version": "1"}})
    elif method == "session/new":
        cwd = params.get("cwd", cwd)
        response(request, {"sessionId": session_id, "configOptions": config_options})
    elif method == "session/set_config_option":
        config_options[0]["currentValue"] = params.get("value")
        response(request, {"configOptions": config_options})
    elif method == "session/prompt":
        prompt = "".join(block.get("text", "") for block in params.get("prompt", [])
                        if isinstance(block, dict) and block.get("type") == "text")
        if "LANE_TEST_CRASH" in prompt:
            os._exit(17)
        send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": session_id,
              "update": {"sessionUpdate": "plan", "entries": [{"content": "Apply the requested change"}]}}})
        send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": session_id,
              "update": {"sessionUpdate": "tool_call", "toolCallId": "tool-1", "title": "Edit lane file",
                         "kind": "edit", "status": "pending"}}})
        if "LANE_TEST_PERMISSION" in prompt:
            permission = {"jsonrpc": "2.0", "id": "permission-1", "method": "session/request_permission",
                          "params": {"sessionId": session_id,
                                     "toolCall": {"toolCallId": "tool-1", "title": "Edit lane file", "kind": "edit"},
                                     "options": [{"optionId": "once", "name": "Allow once", "kind": "allow_once"},
                                                 {"optionId": "always", "name": "Allow always", "kind": "allow_always"}]}}
            send(permission)
            while True:
                reply = read_message(5)
                if reply is None:
                    continue
                if reply.get("id") == permission["id"]:
                    record({"permission_response": reply})
                    break
        if "LANE_TEST_WAIT" in prompt:
            while True:
                message = read_message(0.2)
                if not message:
                    continue
                if message.get("method") == "session/cancel":
                    response(request, {"stopReason": "cancelled"})
                    break
                if message.get("id") == request.get("id"):
                    continue
            continue
        if "LANE_TEST_NO_CHANGE" not in prompt:
            with open(os.path.join(cwd, "lane_result.txt"), "w") as stream:
                stream.write(prompt)
        if "LANE_TEST_NO_REPORT" in prompt or REPORT_MARKER not in prompt:
            # The live agent ends the turn without a JSON report when the prompt does not ask for one.
            response(request, {"stopReason": "end_turn"})
            continue
        result = {"status": "done", "summary": "fake Cursor ACP completed", "files_changed": ["lane_result.txt"],
                  "tests": "fake ACP executor", "followups": []}
        report = json.dumps(result)
        middle = max(1, len(report) // 2)
        for chunk in (report[:middle], report[middle:]):
            send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": session_id,
                  "update": {"sessionUpdate": "agent_message_chunk", "messageId": "answer-1",
                             "content": {"type": "text", "text": chunk}}}})
        response(request, {"stopReason": "end_turn"})
    elif method == "session/cancel":
        continue
    elif method:
        send({"jsonrpc": "2.0", "id": request.get("id"),
              "error": {"code": -32601, "message": "method not found"}})
