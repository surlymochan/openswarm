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
    "allowed_planners": ["<planner-github-login>"],
    "worker_repos": ["<target-owner>/<target-repo>"],
    "worker_verify_allow": ["<exact-verify-command>"],
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

Pass `--repos owner/repo[,owner/repo]` to set a worker-local target repository allowlist, or configure `bus.worker_repos`; with neither configured the worker refuses every task. Omit `--once` for a resident worker. `--backend` may be `codex`, `ocgo`, `deepseek`, `cursor`, or `pi`; `--model` is optional. A task's `verify` command runs only when it exactly matches `worker_verify_allow` or the worker's own `--verify`; other task commands are ignored and reported in the result followups. Set `open_pr` to `true` to have the worker open a draft PR after pushing its branch.

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

The state labels are `openswarm:queued`, `openswarm:claimed`, `openswarm:done`, and `openswarm:failed`; they are display labels and do not authorize planner state transitions. The planner acts on `openswarm:cancelled` only as a stop signal. An optional `executor:<name>` label routes a card to one worker name. A worker claims by adding `openswarm:claimed` and commenting `/claim <executor-name>`. The earliest `/claim` comment authored by an allowlisted GitHub account wins. The planner posts an `openswarm-task-digest: sha256:<hex>` first comment over the canonical task JSON.

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

`status`, `summary`, `files_changed`, `tests`, and `followups` follow openswarm's regular result schema. The bus result adds `branch`, `commit`, and optional `pr_url`. The planner takes the terminal status from a valid `openswarm-result` comment authored by the winning claimer, regardless of done or failed labels. It fetches the reported result branch, verifies it locally when requested, writes the usual run result and ledger entry, and never merges or pushes to the target repository. The worker always pushes `openswarm/<run_id>`; `branch_hint` is informational and ignored.

## Trust and cancellation

Issue content and comments are untrusted input. The worker accepts a card only when its issue author and first digest comment are both from `bus.allowed_planners` and the digest matches the task JSON in the current body. Workers require a target repository in their worker-local `--repos` or `bus.worker_repos` allowlist. The planner accepts claims and results only from `bus.allowed_executors`; only the earliest allowed claimer's result is used. The planner treats comments as data, never runs comment text, validates the result fields and branch name, and checks the fetched commit against the reported SHA. Workers must stop when an issue is labelled `openswarm:cancelled`.

Do not put secrets in issue bodies or comments. Do not grant bus workers more target repository access than needed to push their task branch. The planner never merges PRs and never pushes to the target repository itself. Revoke executor access when a worker is retired.
