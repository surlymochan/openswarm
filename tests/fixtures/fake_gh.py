#!/usr/bin/env python3
"""Offline subset of gh api backed by a JSON file for issue-bus tests."""
import base64
import fcntl
import hashlib
import json
import os
import sys
import urllib.parse
from datetime import datetime, timezone


def main():
    args = sys.argv[1:]
    if not args or args[0] != "api":
        print("fake gh only supports api", file=sys.stderr)
        return 2
    endpoint = args[1]
    method = "GET"
    payload = None
    if "--method" in args:
        method = args[args.index("--method") + 1]
    if "--input" in args:
        payload = json.loads(sys.stdin.read() or "{}")
    state_path = os.environ["OPENSWARM_FAKE_GH_STATE"]
    with open(state_path + ".lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with open(state_path) as stream:
            state = json.load(stream)
        parsed = urllib.parse.urlsplit(endpoint)
        parts = [urllib.parse.unquote(part) for part in parsed.path.strip("/").split("/") if part]
        query = urllib.parse.parse_qs(parsed.query)
        answer = route(state, parts, query, method, payload)
        if answer is None:
            print("HTTP 404 Not Found", file=sys.stderr)
            return 1
        temp_path = state_path + ".tmp"
        with open(temp_path, "w") as stream:
            json.dump(state, stream)
        os.replace(temp_path, state_path)
        print(json.dumps(answer))
    return 0


def route(state, parts, query, method, payload):
    if parts == ["user"] and method == "GET":
        return {"login": os.environ.get("OPENSWARM_FAKE_GH_LOGIN", "executor")}
    if len(parts) < 3 or parts[0] != "repos":
        return None
    repo = "/".join(parts[1:3])
    bucket = state.setdefault("repos", {}).setdefault(repo, {"labels": [], "issues": [], "files": {}})
    tail = parts[3:]
    if not tail and method == "GET":
        return {"full_name": repo, "default_branch": "main"}
    if tail == ["labels"]:
        if method == "GET":
            return bucket["labels"]
        if method == "POST":
            label = dict(payload)
            if not any(row.get("name") == label["name"] for row in bucket["labels"]):
                bucket["labels"].append(label)
            return label
    if len(tail) == 2 and tail[0] == "labels" and method == "GET":
        return next((row for row in bucket["labels"] if row.get("name") == tail[1]), None)
    if tail and tail[0] == "contents":
        path = "/".join(tail[1:])
        if method == "GET":
            content = bucket["files"].get(path)
            return {"path": path, "sha": content["sha"], "content": content["content"]} if content else None
        if method == "PUT":
            raw = base64.b64decode(payload["content"])
            entry = {"sha": hashlib.sha1(raw).hexdigest(), "content": payload["content"]}
            bucket["files"][path] = entry
            return {"content": {"path": path, "sha": entry["sha"]}, "commit": {"message": payload["message"]}}
    if tail == ["issues"] and method == "GET":
        rows = [issue for issue in bucket["issues"] if query.get("state", ["open"])[0] == "all"
                or issue.get("state", "open") == query.get("state", ["open"])[0]]
        return rows
    if tail == ["issues"] and method == "POST":
        number = max([issue["number"] for issue in bucket["issues"]] or [0]) + 1
        issue = {"number": number, "id": number, "title": payload["title"], "body": payload["body"],
                 "labels": [{"name": label} for label in payload.get("labels", [])], "comments": [],
                 "user": {"login": os.environ.get("OPENSWARM_FAKE_GH_ISSUE_LOGIN",
                                                   os.environ.get("OPENSWARM_FAKE_GH_LOGIN", "planner"))},
                 "created_at": datetime.now(timezone.utc).isoformat(), "state": "open",
                 "html_url": f"https://github.com/{repo}/issues/{number}"}
        bucket["issues"].append(issue)
        return issue
    if len(tail) >= 2 and tail[0] == "issues":
        try:
            issue = next(row for row in bucket["issues"] if row["number"] == int(tail[1]))
        except (StopIteration, ValueError):
            return None
        if len(tail) == 2 and method == "GET":
            return {key: value for key, value in issue.items() if key != "comments"}
        if tail[2:] == ["comments"]:
            if method == "GET":
                return issue["comments"]
            if method == "POST":
                comment_id = max([row["id"] for row in issue["comments"]] or [0]) + 1
                comment = {"id": comment_id, "body": payload["body"],
                           "user": {"login": os.environ.get("OPENSWARM_FAKE_GH_LOGIN", "executor")},
                           "created_at": datetime.now(timezone.utc).isoformat()}
                issue["comments"].append(comment)
                return comment
        if tail[2:] == ["labels"] and method == "PUT":
            issue["labels"] = [{"name": name} for name in payload.get("labels", [])]
            return issue["labels"]
    if tail == ["pulls"] and method == "POST":
        number = len(bucket.get("pulls", [])) + 1
        pr = {**payload, "number": number, "html_url": f"https://github.com/{repo}/pull/{number}"}
        bucket.setdefault("pulls", []).append(pr)
        return pr
    return None


if __name__ == "__main__":
    raise SystemExit(main())
