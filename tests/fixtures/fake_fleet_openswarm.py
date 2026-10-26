#!/usr/bin/env python3
"""Detached-run stub for the local fake fleet transport. No network or executor."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

home = Path(os.environ["OPENSWARM_HOME"])
home.mkdir(parents=True, exist_ok=True)


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def git(repo, *args):
    return subprocess.check_output(["git", "-C", repo, *args], stderr=subprocess.PIPE).decode().strip()


def record(value):
    with (home / "calls.jsonl").open("a") as stream:
        stream.write(json.dumps(value) + "\n")


if sys.argv[1:3] == ["fleet", "_snapshot"]:
    print(json.dumps({"lanes": [], "active_runs": []}))
elif sys.argv[1:3] == ["fleet", "_run-info"]:
    rid = sys.argv[3]
    path = home / "runs" / rid / "stub.json"
    spec = json.loads(path.read_text())
    spec["polls"] += 1
    if spec["polls"] < 2:
        save(path, spec)
        print(json.dumps({"state": "running", "result": None}))
    else:
        result_path = path.with_name("result.json")
        if result_path.exists():
            result = json.loads(result_path.read_text())
        else:
            repo, branch = spec["repo"], "openswarm/" + rid
            git(repo, "checkout", "-q", "-b", branch, spec["base"])
            (Path(repo) / (rid + ".txt")).write_text(spec["card"])
            git(repo, "add", rid + ".txt")
            git(repo, "-c", "user.name=fleet", "-c", "user.email=fleet@example.invalid", "commit", "-qm", "card")
            commit = git(repo, "rev-parse", "HEAD")
            git(repo, "checkout", "-q", "--detach", commit)
            failed = "FAIL" in spec["card"]
            result = {"id": rid, "status": "failed" if failed else "done", "branch": branch, "commit": commit,
                      "seconds": 0.1, "verify": {"rc": 1 if failed else 0},
                      "workspace": {"repo": repo, "path": repo, "base": spec["base"]},
                      "result": {"summary": spec["card"].strip().splitlines()[0]}}
            save(result_path, result)
            save(path.with_name("status.json"), {"id": rid, "state": "finished", "status": result["status"]})
            record({"event": "finish", "id": rid})
        save(path, spec)
        print(json.dumps({"state": "finished", "result": result}))
elif sys.argv[1:2] == ["run"]:
    parser = argparse.ArgumentParser()
    parser.add_argument("--detach", action="store_true")
    for flag in ("cwd", "task-file", "base", "backend", "model", "effort", "verify", "budget-minutes"):
        parser.add_argument("--" + flag)
    args = parser.parse_args(sys.argv[2:])
    rid = "fake-" + uuid.uuid4().hex[:10]
    active = [path for path in (home / "runs").glob("*/stub.json") if not path.with_name("result.json").exists()]
    card = Path(args.task_file).read_text()
    record({"event": "start", "id": rid, "active_before": len(active), "args": vars(args), "card": card})
    save(home / "runs" / rid / "stub.json", {"repo": args.cwd, "base": args.base, "card": card, "polls": 0})
    save(home / "runs" / rid / "status.json", {"id": rid, "state": "running"})
    print(json.dumps({"id": rid, "state": "started"}))
else:
    sys.exit(2)
