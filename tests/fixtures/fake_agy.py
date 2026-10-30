#!/usr/bin/env python3
"""Small Antigravity CLI subset used by the fake-agy tests and the resident agy lane tests.

Headless mode: `agy -p <prompt> --model <slug> --output-format stream-json --dangerously-skip-permissions`
emits NDJSON events (init, step_update, result). The result envelope carries conversation_id, status,
response, error and usage.
"""
import json
import os
import sys
import time

argv = sys.argv[1:]
mode = os.environ.get("FAKE_AGY_MODE", "ok")
log = os.environ.get("FAKE_AGY_LOG")


def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


if log:
    with open(log, "a") as stream:
        stream.write(json.dumps(argv) + "\n")

if argv[:1] == ["models"]:
    if mode == "auth":
        sys.stderr.write("authentication required: run `agy login` first\n")
        sys.exit(1)
    for slug in ("gemini-3.8-flash-high", "gemini-3.8-flash-medium", "gemini-3.8-flash-low",
                 "claude-sonnet-4-6", "claude-opus-4-6-thinking", "gpt-oss-120b-medium"):
        print(slug)
    sys.exit(0)

if mode == "auth":
    sys.stderr.write("FatalAuthenticationError: authentication required\n")
    sys.exit(1)
if mode == "sleep":
    time.sleep(30)
    sys.exit(0)
if mode == "print-timeout":
    partial = {"status":"done", "summary":"partial", "files_changed":[], "tests":"none", "followups":[]}
    send({"event":"result", "result":{"status":"SUCCESS", "finished_reason":"print_timeout",
                                        "response":json.dumps(partial)}})
    time.sleep(30)  # An early result can arrive before the process actually exits.
    sys.exit(0)
if mode == "forbidden-once":
    marker = os.environ["FAKE_AGY_ONCE_MARKER"]
    if not os.path.exists(marker):
        open(marker, "w").close()
        mode = "forbidden-exit"
if mode == "rate-then-forbidden":
    marker = os.environ["FAKE_AGY_ONCE_MARKER"]
    try:
        with open(marker) as stream:
            count = int(stream.read())
    except FileNotFoundError:
        count = 0
    with open(marker, "w") as stream:
        stream.write(str(count + 1))
    mode = "rate" if count == 0 else "forbidden-exit" if count == 1 else "ok"
if mode in ("forbidden", "forbidden-exit"):
    send({"type": "result", "status": "ERROR", "error": 'request failed (code 403): HTML response "Error 403 (Forbidden)!!1"'})
    if mode == "forbidden":
        time.sleep(30)  # The result may arrive before the headless process exits.
    sys.exit(1)

prompt = argv[argv.index("-p") + 1] if "-p" in argv else ""
conversation = argv[argv.index("--conversation") + 1] if "--conversation" in argv else "conv-1"

if mode == "rate":
    send({"type": "init", "conversation_id": conversation})
    send({"type": "result", "conversation_id": conversation, "status": "ERROR",
          "error": "RESOURCE_EXHAUSTED: quota exceeded (429)",
          "usage": {"input_tokens": 1, "output_tokens": 0}})
    sys.stderr.write("429 rate limit exceeded\n")
    sys.exit(1)

send({"type": "init", "conversation_id": conversation})

if "LANE_TEST_WAIT" in prompt and "Correction to the interrupted task" not in prompt:
    time.sleep(20)

if "LANE_TEST_NO_CHANGE" not in prompt:
    with open("lane_result.txt", "w") as stream:
        stream.write("completed by the fake Antigravity CLI\n")

body = {"status": "done", "summary": "fake agy completed", "files_changed": ["lane_result.txt"],
        "tests": "fake executor", "followups": []}
send({"type": "step_update", "text": "writing the report"})
send({"type": "result", "conversation_id": conversation, "status": "SUCCESS",
      "response": json.dumps(body), "duration_seconds": 1.5, "num_turns": 2,
      "usage": {"input_tokens": 120, "output_tokens": 45, "thinking_tokens": 3,
                "cache_read_tokens": 8, "total_tokens": 176}})
