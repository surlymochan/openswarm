# openswarm GitHub issue bus

Use the issue bus only for executors outside the controlled fleet, such as another person's agent, a cloud agent, or an agent on an unmanaged host. For hosts you control, use SSH fleet lanes so execution stays on the known machines.

## Setup

Create a dedicated GitHub repository for task cards. The planner host and every worker need GitHub CLI (`gh`) authentication with permission to read and comment on bus issues. Workers also need permission to clone the target repository and push result branches there.

Initialize labels, an issue template, and this protocol README:

```sh
openswarm bus init <bus-owner>/<bus-repo>
```

Configure `policy.local.json` under `$OPENSWARM_HOME`:

```json
{
  "bus": {
    "repo": "<bus-owner>/<bus-repo>",
    "allowed_executors": ["<github-login>"],
    "poll_seconds": 60,
    "labels_prefix": "openswarm",
    "open_pr": false
  }
}
```

`allowed_executors` is the allowlist of GitHub logins. Workers can be started on an executor host with:

```sh
openswarm bus-worker --bus <bus-owner>/<bus-repo> --name <executor-name> --backend codex --once
```

Omit `--once` for a resident worker. `--backend` may be `codex`, `ocgo`, `deepseek`, `cursor`, or `pi`; `--model` is optional. Set `open_pr` to `true` to have the worker open a draft PR after pushing its branch.

Dispatch a task from a target repository whose `origin` is its GitHub remote. The selected base commit must already be advertised by a remote branch or tag; push it first if necessary:

```sh
openswarm run --backend github-issue --cwd <target-repo> --base <commit> \
  --budget-minutes 40 --verify '<acceptance-command>' --task-file task.md
```

Use `--detach` for a background planner run and `openswarm watch <run-id>` to follow it. `openswarm bus status` shows open issue state, claimant, and age.

## Task card protocol

An issue is one task card. Its title is `[openswarm] <run title>`. The task description is followed by a fenced `openswarm-task` JSON block:

```json
{
  "run_id": "<run-id>",
  "repo": "<target-owner>/<target-repo>",
  "base": "<base-commit-sha>",
  "branch_hint": "openswarm/<run-id>",
  "verify": "<optional acceptance command>",
  "budget_minutes": 40,
  "report_schema": {"type": "object"}
}
```

The state labels are `openswarm:queued` → `openswarm:claimed` → `openswarm:done` or `openswarm:failed`. The planner may instead set `openswarm:cancelled`. An optional `executor:<name>` label routes a card to one worker name. A worker claims by adding `openswarm:claimed` and commenting `/claim <executor-name>`. The earliest `/claim` comment authored by an allowlisted GitHub account wins.

The worker checks out the requested base, runs openswarm's regular local run machinery with a hard budget and optional verify command, pushes `openswarm/<run-id>` to the target repository, then posts a fenced `openswarm-result` JSON comment:

```json
{
  "status": "done",
  "summary": "<summary>",
  "files_changed": ["<path>"],
  "tests": "<commands and outcomes>",
  "followups": [],
  "branch": "openswarm/<run-id>",
  "commit": "<commit-sha>",
  "pr_url": null
}
```

`status`, `summary`, `files_changed`, `tests`, and `followups` follow openswarm's regular result schema. The bus result adds `branch`, `commit`, and optional `pr_url`. `done` and `failed` labels mark the terminal result. The planner fetches the reported result branch, verifies it locally when requested, writes the usual run result and ledger entry, and never merges or pushes to the target repository.

## Trust and cancellation

Issue comments are untrusted input. The planner accepts claims and results only from `bus.allowed_executors`; only the earliest allowed claimer's result is used. The planner treats comments as data, never runs comment text, validates the result fields and branch name, and checks the fetched commit against the reported SHA. Workers must stop when an issue is labelled `openswarm:cancelled`.

Do not put secrets in issue bodies or comments. Do not grant bus workers more target repository access than needed to push their task branch. The planner never merges PRs and never pushes to the target repository itself. Revoke executor access when a worker is retired.
