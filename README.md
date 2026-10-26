# openswarm

[![CI](https://github.com/surlymochan/openswarm/actions/workflows/ci.yml/badge.svg)](https://github.com/surlymochan/openswarm/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-informational)
![deps: none](https://img.shields.io/badge/dependencies-stdlib%20only-brightgreen)

**Claude plans. Codex, OpenCode Go and DeepSeek write the code.**

## Benchmarks: openswarm vs oh-my-openagent (2026-10-17)

Compared with [oh-my-openagent](https://github.com/code-yeongyu/oh-my-openagent) (OmO) v5.1.35 (`0777686`), whose
`model-core` package resolves a delegated task to a model through per-category fallback chains. Scripts, raw results and
methodology: [`bench/`](bench/README.md) and [`bench/e2e/`](bench/e2e/README.md). Where OmO comes out ahead, the table says so.

**Real GitHub issues (SWE-bench Verified), 2026-10-18.** Six hard instances from sympy, pytest and pylint; success
means the official FAIL_TO_PASS and PASS_TO_PASS tests all pass. Every arm runs on the Pi coding agent with
DeepSeek-V4.1-Flash (`deepseek-flash`), n=1 per cell, 20-minute cap; details and commands are in
[`bench/swebench/`](bench/swebench/README.md).

| Arm | Resolved (official tests) | Median wall-clock (IQR) | Median $ per task (DeepSeek list price) | Total $ (all runs) | n |
| --- | --- | --- | --- | --- | --- |
| Plain agent (baseline) | 3/6 (50%) | 124 s (110–175) | $0.0234 | $0.16 | 6 |
| superpowers v7.0.0 | 2/6 (33%) | 154 s (136–340) | $0.0285 | $0.31 | 6 |
| OpenFlow 0.2.1 | 4/6 (67%) | 230 s (200–266) | $0.0412 | $0.23 | 6 |
| openswarm (planner + Pi executor) | 5/6 (83%) | 228 s (187–254) | $0.0377 | $0.21 | 6 |
| oh-my-openagent v5.1.35 | not run ¹ | – | – | – | 0 |

**Caveats:** n=1 on 6 instances, so one or two resolves are within noise. The two workflow tools took about 1.8x the
baseline's median wall-clock and cost 76% (OpenFlow) / 61% (openswarm) more per task. The first published table undercounted
pytest because of a shared temp-dir race in grading; it is now fixed and regraded
([regrade notes](bench/swebench/README.md#regrade-2026-10-18)). On the MiniMax-M3.1-Flash-Preview subset, baseline
resolved 3/4 and superpowers 2/4; openswarm had no clean MiniMax run because the plan's usage cap was hit.

Raw per-run records and logs: [`bench/swebench/results/`](bench/swebench/results/). Optimization backlog from these
results: [#3](https://github.com/surlymochan/openswarm/issues/3).

**Synthetic end-to-end tasks.** 8 small real tasks (4 JS, 4 Python), hidden acceptance tests applied only after the agent exits.
For these synthetic tasks, every arm runs `grok-4.7-high` through the local `cursor-agent` (openswarm uses its new `--backend cursor` executor).

| Arm | Success (hidden tests) | Median wall time (IQR) | Median tokens: total / input+output | n |
| --- | --- | --- | --- | --- |
| Single agent (baseline) | 24/24 (100%) | 55 s (46–132) | 105k / 28k | 24 |
| openswarm (planner + worktree executor + host verify) | 24/24 (100%) | 220 s (137–356) | 207k / 64k | 24 |
| oh-my-openagent | not run ¹ | – | – | 0 |

On tasks this small the single agent already succeeds every time. openswarm's planner call and isolated, verified run
cost about 4× the time and 2× the tokens.
¹ OmO runs inside OpenCode/senpi with its own provider logins. Cursor's subscription can't serve it, and paid APIs were out of scope for
this benchmark. The estimated metered cost to run it on DeepSeek is about $1.3 ([details](bench/e2e/README.md#results-2026-10-17-first-round)).

**Routing under quota pressure.** A synthetic 80-task trace × 7 quota scenarios (560 decisions per router), with the same
providers for both routers (Codex subscription, OpenCode Go, DeepSeek). Costs are *computed* from the price tables in
`bench/prices.json` × estimated tokens. They are not bills.

| Metric | openswarm | OmO | Codex-only baseline |
| --- | --- | --- | --- |
| First attempt sent to an already-exhausted quota | **0.0%** | 23.4% (254 failed attempts) | 28.6% |
| Decisions with no usable model (this provider set) | **0.0%** | 19.8% ² | 28.6% |
| Review tasks sent to a different model family | 54.8% | **71.4%** | 0% |
| Codex 5h window used per trace pass (computed) | **0.48** | 0.85 | 1.78 |
| Metered spend, all scenarios (computed) | **$0.00** | $1.54 | $0.00 |
| Routing decision latency, in-process median | **0.008 ms** | 0.026 ms | – |
| Providers / model ids known to the router | 3 backends / 27 | **26 / 36** | – |

openswarm reads quota windows before it routes, so it never spends an attempt on an exhausted plan. By design, OmO
learns about an exhausted plan from the usage-limit error and then moves to the next rung. OmO keeps review work on a
different model family more often, and it covers far more providers.
² 49 of OmO's 111 unrouted decisions are `writing` tasks. In OmO that chain only names Google/Anthropic models, which this provider set does not connect.

openswarm is a quota-aware dispatcher for coding agents. Your planner (Claude Code) breaks work into tasks and
reviews the results. openswarm sends each task to the cheapest executor that can handle it, runs it in an
isolated git worktree with a hard time budget, checks the result on the host, and reports back. Nothing
runs forever.

![openswarm demo](docs/demo.gif)

<sub>The demo runs offline against a synthetic quota state (`examples/demo-state.json`), so you can reproduce
it without any backend installed.</sub>

## Why

If you pay for several AI subscriptions, the scarce one is usually the planner. Claude Pro runs out long
before a Codex or OpenCode Go plan does. Spending Claude tokens on typing out a refactor wastes the
expensive model.

openswarm splits the work:

- **Claude** decomposes the task, writes acceptance criteria, reviews diffs and integrates.
- **Executors** do the typing:
  - [Codex CLI](https://github.com/openai/codex) is the main executor.
  - [OpenCode](https://opencode.ai) Go handles cheap or mechanical work, non-GPT second opinions, overflow, and 1M context.
  - DeepSeek is the metered last resort.
  - Cursor CLI (`cursor-agent`) runs only when you pass `--backend cursor`, or when you add `cursor` to `routing.default_order`. It is not part of automatic routing.

Routing decisions use live quota data, not guesses.

## Features

- **Live model discovery.** Reads the current Codex and OpenCode model catalogues and sorts them into tiers (fast, balanced, deep). Legacy models go last.
- **Quota-aware routing.** Looks at 5-hour, 7-day and 30-day windows, burn pace and reset times:
  - At the soft limit, easy work spills to other backends.
  - A hard limit skips the backend.
  - Quota that is about to expire unused gets spent at higher effort.
- **Isolated execution.** Each run gets its own `git worktree` and `openswarm/<id>` branch.
  - Dependency dirs (`node_modules`, `.venv`) are symlinked in and never committed.
  - For a workspace that must install dependencies inside each worktree, set `workspace.prepare`; it runs before
    execution and again before verification when a lockfile changed, and replaces configured dependency links.
  - Nothing is pushed.
- **Hard budgets.** `--budget-minutes` kills the executor's whole process tree. Watchers expire too, and you re-arm them on purpose.
- **Host-side verification.** `--verify '<cmd>'` runs your test command on the committed worktree.
  - Its exit code overrides the executor's self-report.
  - `--verify-baseline` ignores failures that already exist on the base commit.
- **Automatic fallback.** One-shot Pi, Codex and Cursor runs retry visible 429s in the same session; other fallback-eligible rate limits and model-at-capacity errors can move to another backend.
- **Rate-limit recovery and provider caps.** Resident lanes retry visible provider 429s in the same session using `rate_limit.backoff_seconds` (default `[30, 60, 120, 240]`) and `rate_limit.max_retries` (default `4`). `concurrency` accepts provider/backend caps plus `total`; defaults are `{"codex": 4, "minimax-cn": 6, "total": 10}`. Excess local turns queue across lanes and runs.
- **Risk alerts.** `openswarm watch` streams progress and flags dangerous executor commands (push, `rm -rf ..`, secret prints, `sudo`, publish). It also reports stalls and heartbeats.
- **Planner-friendly.**
  - `SKILL.md` teaches Claude Code the whole loop.
  - `templates/openswarm-workflow.js` fans tasks out in parallel.
  - `openswarm report` gives scheduled check-ins.
- **GitHub issue bus.** Routes tasks to uncontrolled external executors through a dedicated repository, while controlled hosts continue to use SSH fleet lanes.
- **Zero dependencies.** One Python file using only the standard library.

## Quickstart

Requirements: Python 3.9+, git, and at least one executor: `codex` CLI logged in, `opencode` with an
OpenCode Go plan, or a DeepSeek API key.

```bash
# 1. Install as a Claude Code skill
git clone https://github.com/surlymochan/openswarm.git ~/.claude/skills/openswarm
alias openswarm=~/.claude/skills/openswarm/bin/openswarm

# 2. See what you have: plans, quotas, models, recommended concurrency
openswarm status --refresh

# 3. Ask where a task would go, without running anything
openswarm route --type impl --difficulty 3

# 4. Run a task in an isolated worktree with a 15-minute budget and a host-side test gate
cat > /tmp/task.md <<'TASK'
Add input validation to src/parse.ts: reject empty strings with a ParseError.
Acceptance: new unit test in test/parse.test.ts passes; existing tests still pass.
TASK
openswarm run --cwd ~/code/myrepo --type impl --difficulty 2 --budget-minutes 15 \
  --verify 'npm test' --task-file /tmp/task.md

# 5. Review and merge (openswarm never merges or pushes for you)
git -C ~/code/myrepo merge --no-ff openswarm/<run-id>
```

For longer work, detach and watch:

```bash
openswarm run --detach --cwd ~/code/myrepo --verify 'pytest -q' --task-file /tmp/task.md   # prints {"id": ...}
openswarm watch <id1> <id2>          # one line per actionable event; exits on DONE or after 28 min
openswarm watch <id1> <id2> --quiet  # alerts, heartbeats and terminal events only
openswarm report --since-minutes 60  # one-screen digest for scheduled check-ins
openswarm stop <id>                  # kill the executor; partial work is still committed to its branch
```

Pi messages appear once at `message_end`; Cursor chunks are merged until a prompt turn ends or a tool call starts.
Codex events keep their existing behavior. `--quiet` keeps only `DONE`, `EXITED`, `RISK`, `STALL`, `ERROR`,
`BUDGET`, `ATTEMPT`, and `HEARTBEAT`. Heartbeats include elapsed time, command count, the worktree's git diff
summary (including lane worktrees), and the last completed message truncated to 200 characters. The watch window
still expires with `--quiet`; re-arm with `--from-end` if needed.

## Resident lanes

Use lanes when you want a persistent Codex app-server, Cursor Agent ACP, or Pi RPC process that accepts one task at a time and can be
steered or aborted while working. Cursor lanes run the local `cursor-agent acp` JSON-RPC process over stdio; they do not use Cursor Cloud or HTTP. Configure `$OPENSWARM_HOME/lanes.json`:

```json
{"lanes":[
  {"name":"C1","backend":"codex","model":"gpt-6-luna","effort":"xhigh","repo":"/abs/repo"},
  {"name":"K1","backend":"cursor","model":"composer-2.5","repo":"/abs/repo"},
  {"name":"M1","backend":"pi","provider":"minimax-cn","model":"MiniMax-M3.1-Flash","repo":"/abs/repo"}
]}
```

Each lane keeps a fixed worktree under `$OPENSWARM_HOME/lanes/<name>/wt`. Start lanes and dispatch tasks with:

```bash
openswarm lane up C1 K1 M1               # no names starts every configured lane; safe to repeat
openswarm lane status
openswarm lane assign C1 --task-file /tmp/task.md --title "Parser fix" --verify 'pytest -q' --budget-minutes 30
openswarm watch <run-id>
openswarm lane steer C1 --text "Add a regression test before finishing"
openswarm lane abort C1                  # openswarm stop <run-id> also aborts a lane turn
openswarm lane down C1 K1 M1
openswarm lane launchd-plist C1           # prints a KeepAlive LaunchAgent plist; does not install it
openswarm lane systemd-unit K1             # prints a system systemd unit; does not install it
```

Lane runs use the normal `runs/<id>` files, `openswarm/<id>` commit branch, host verification, ledger, `watch`,
`runs`, and `report` paths. `--base REV` selects the commit used to reset a lane worktree for one assignment.
An optional lane `binary` absolute path overrides the detected executable, which is useful for wrappers and offline
protocol testing. Cursor defaults to model `composer-2.5`; its binary is `cursor-agent` on `PATH`, then
`~/.local/bin/cursor-agent`. ACP model selection first tries the exact configured value, then matches the part before
`[` (for example `composer-2.5` resolves to `composer-2.5[fast=true]`). A unique base-name match is accepted;
multiple matches fail with the candidate list. Lane state and the run result record the resolved value as `model`
and `resolved_model`. Codex app-server approval prompts receive a decline response. Cursor permission requests
receive the offered `allow_always` choice, falling back to `allow_once`. Cursor `lane steer` cancels the active prompt,
waits for its response, then continues in the same ACP session with the steer text as a correction.

On small Linux lane hosts, omit `--verify` so test suites and their dependencies do not compete with the agent for
memory and CPU. The planner can run acceptance checks on its host. `lane systemd-unit` prints a system service with
`MemoryMax=900M`, `CPUQuota=150%`, and `Nice=10`; review and install it separately if needed.

## Fleet and batch dispatch

Put named executor hosts in `$OPENSWARM_HOME/fleet.json`. Install the same openswarm version, Python 3.9+, git,
and an authenticated executor on each host. `repos` maps the same logical repo name to each host's checkout:

```json
{"hosts":[
  {"name":"local","kind":"local","repos":{"project":"~/code/project"}},
  {"name":"worker","kind":"ssh","ssh":"worker-alias","openswarm":"~/.local/bin/openswarm",
   "repos":{"project":"~/work/project"}},
  {"name":"sandbox","kind":"instabox","box":"task-box","openswarm":"~/.local/bin/openswarm",
   "repos":{"project":"~/work/project"}}
]}
```

Local hosts use subprocesses; remote commands use `ssh <alias> --` or `instabox ssh <box> --`, with timeouts.
Without a local repo mapping, the planner uses its current checkout. A missing fleet config defaults to local only.

```bash
openswarm fleet status                   # reachability, paused hosts, lanes and active runs
openswarm fleet status --json
openswarm fleet exec worker -- lane status
openswarm fleet exec --timeout 120 worker -- runs --active --json
```

Status uses `instabox ls` and never connects to a paused box. Unknown box states also remain uncontacted.
`fleet exec` forwards stdout, stderr and the exit code; it does not issue a separate wake command.

Write one self-contained task per regular file in a cards directory, then dispatch:

```bash
openswarm batch --repo project --cards ./cards --hosts worker,sandbox --per-host 1 \
  --backend pi --model cheap-model --verify 'python3 -m unittest discover -s tests' \
  --budget-minutes 20 --detach
openswarm batch status <batch-id>
```

The batch wakes each selected instabox once, checks openswarm and the checkout, and makes `--base REV` available
on every host (default: the planner checkout's `HEAD`). Sorted cards are assigned round-robin; `--per-host`
defaults to 1, with remaining cards queued until slots free. Backend, model and effort flags are optional and pass
through to each `run --detach`. One line is printed per state change; detached output goes to
`$OPENSWARM_HOME/batches/<id>/batch.out`. Polling works without Monitor.

Provider caps in `policy.local.json` also constrain batch dispatch across the hosts listed in `fleet.json`. The planner checks fleet status before each launch and leaves excess cards queued until a provider slot frees. This fleet-wide coordination is planner-owned and uses no `concurrency_url`; direct `fleet exec ... lane assign` only observes the target host's local cap, so use `batch` when multiple hosts share one provider account.

Base synchronization and result collection use git bundles through the transport: no git remotes or pushes.
Each completed run's `openswarm/<run-id>` branch is fetched into the planner checkout, then removed on the
executor host after its commit is confirmed locally. Local runs in the planner checkout retain their branches.
Failed runs with commits are collected too. The batch exits nonzero if any card is not `done`.
`summary.json` and `summary.md` under `$OPENSWARM_HOME/batches/<id>/` list card, host, status, verify exit code,
branch, commit, seconds and a one-line summary. Review branches before integrating; run a combined test suite
after integration. One-shot `run --base REV` is also supported.

## Verify a remote run elsewhere

```bash
openswarm verify-remote --from worker --run <run-id> --on local --verify 'pytest -q'
openswarm verify-remote --from worker --run <run-id> --on sandbox --verify 'npm test'
```

The source host's `repos` mapping identifies the logical repo for another executor; `--on local` uses the matching
local mapping or the current checkout. Bundles carry the recorded run commit into the target repo. A temporary
worktree checks out that commit, links dependency directories like ordinary runs, executes the verify command,
then removes its worktree and temporary ref. Existing checkouts stay on their current commits. Verification
assumes the target is available; arrange its wake-up if needed. The default verify timeout is 600 seconds (`--timeout`).

The planner saves a copy of the original result at `$OPENSWARM_HOME/remote-results/<run-id>.json`, adding
`verify_remote: {host, rc, failing, seconds}`. Verify output is not printed or included in that record.
A failed verify returns a nonzero CLI exit code. The recorded commit can still be verified after batch collection
has removed the source branch, as long as the source host retains the run and its git objects.

You can try routing with no backend installed:

```bash
OPENSWARM_HOME=$(mktemp -d) OPENSWARM_STATE_FILE=examples/demo-state.json bin/openswarm status
```

In Claude Code, just say things like *"dispatch this to Codex"*, *"swarm these five refactors"*, or *"use
openswarm"*. The skill takes it from there.

## GitHub issue bus

Use the issue bus for executors you do not control, such as a cloud agent, another person's agent, or an unmanaged host. For hosts you control, use [SSH fleet lanes](#fleet-and-batch-dispatch).

Initialize a dedicated bus repository and set its allowlist in `$OPENSWARM_HOME/policy.local.json`:

```sh
openswarm bus init <bus-owner>/<bus-repo>
```

```json
{"bus":{"repo":"<bus-owner>/<bus-repo>","allowed_executors":["<github-login>"],"allowed_planners":["<planner-login>"],"poll_seconds":60,"labels_prefix":"openswarm"}}
```

Both bus allowlists default to deny. `allowed_planners` must include the GitHub login used by the planner to create issues and digest comments.

Dispatch from a target GitHub repository after the base commit has been pushed:

```sh
openswarm run --backend github-issue --cwd <target-repo> --base <commit> \
  --budget-minutes 40 --verify '<acceptance-command>' --task-file task.md
openswarm bus status
openswarm bus-worker --bus <bus-owner>/<bus-repo> --name <executor-name> --backend codex --once
```

See [docs/BUS.md](docs/BUS.md) for the issue/result schema and worker setup. The planner accepts comments only from allowlisted GitHub logins, validates the reported branch and commit, fetches and verifies locally, and never pushes or merges into the target repository. Workers stop on the `openswarm:cancelled` label.

## How routing works

| Input | Effect |
| --- | --- |
| `--type review/audit` | Goes to a non-GPT family first (OpenCode Go) for an independent opinion |
| `--type mechanical/docs/test` at difficulty ≤ 2 | OpenCode Go first, to save Codex |
| `--difficulty 1..5` | Sets tier and effort: fast/low, fast/medium, balanced/medium, balanced/high, deep/high |
| `--context-tokens` > 220k | Prefers 1M-context backends |
| A window ≥ 95% used | Skips the backend |
| A window ≥ 85% used | Spills difficulty ≤ 3 to other backends; harder tasks stay with effort −1 |
| Burn pace > 1.25× even | Effort −1 |
| Resets within 24h with < 60% used | Effort +1, so quota isn't wasted |
| DeepSeek balance below floor | Skips DeepSeek |
| `--backend cursor` | Forces the Cursor CLI. Cursor is never chosen on its own unless `cursor` is listed in `routing.default_order` |

The full rules are in [`SKILL.md`](SKILL.md#7-routing-rules-what-route-does). Every threshold is in
[`policy.json`](policy.json).

## Architecture

```
        ┌──────────── planner (Claude Code + SKILL.md) ────────────┐
        │  decompose → acceptance criteria → review → integrate    │
        └───────┬──────────────────────────────────────▲───────────┘
                │ openswarm run / route / watch           │ result.json, diff, verify
        ┌───────▼──────────────────────────────────────┴───────────┐
        │ openswarm                                                   │
        │  probe ─► state.json (models, tiers, quotas, breakers)   │
        │  route ─► backend/model/effort + reasons + fallbacks     │
        │  run   ─► git worktree ─► executor ─► commit ─► verify   │
        │  watch ─► MSG / RISK / STALL / BUDGET / DONE             │
        └───────┬───────────────────┬───────────────────┬──────────┘
          codex exec          opencode run         opencode run
         (Codex plan)       (OpenCode Go plan)    (DeepSeek API)

Cursor CLI is a fourth executor, opt-in only (`--backend cursor`).
```

| Path | What |
| --- | --- |
| `bin/openswarm` | The CLI: `probe`, `status`, `route`, `run`, `lane`, `fleet`, `batch`, `verify-remote`, `watch`, `runs`, `report`, `stop`, `ingest-claude`, `ledger` |
| `SKILL.md` | Claude Code skill: the planner's operating manual |
| `policy.json` | Defaults: thresholds, difficulty map, model caps, local data sources |
| `backends/*.json` | One manifest per executor: invocation, quota source, data boundary |
| `templates/openswarm-workflow.js` | Parallel fan-out template for Claude Code workflows |
| `examples/demo-state.json` | Synthetic state for offline demos |
| `~/.claude/openswarm/` (`$OPENSWARM_HOME`) | Runtime data: `state.json`, `ledger.jsonl`, `breakers.json`, `lanes.json`, `lanes/<name>/`, `fleet.json`, `batches/<id>/`, `remote-results/<id>.json`, `runs/<id>/` |

## Configuration

Override any key in `$OPENSWARM_HOME/policy.local.json`. It is deep-merged over `policy.json`.

```json
{
  "codex": {"plan": "plus"},
  "claude": {"plan": "pro"},
  "thresholds": {"window_soft": 0.8},
  "deepseek": {"keychain_items": ["openswarm-deepseek-api-key"]},
  "workspace": {
    "sibling_worktrees": false,
    "link_dirs": ["node_modules", ".venv"],
    "prepare": {"my-bun-monorepo": "bun install --frozen-lockfile"},
    "prepare_replaces_links": ["node_modules"]
  }
}
```

`workspace.prepare` keys may be an absolute repository path or its basename. This is particularly useful for Bun
workspaces: linked `node_modules` can otherwise resolve workspace packages back to the base checkout. Without a policy,
openswarm only emits `workspace_note` when it detects `package.json` workspaces plus `bun.lock`; it preserves existing
link behavior.

| Environment variable | Purpose |
| --- | --- |
| `OPENSWARM_HOME` | Runtime data dir (default `~/.claude/openswarm`) |
| `OPENSWARM_CODEX_PLAN`, `OPENSWARM_CLAUDE_PLAN` | Declare your plans explicitly (recommended) |
| `DEEPSEEK_API_KEY` | DeepSeek key, used only for the balance and model-list API |
| `OPENSWARM_OPENCODE_BIN`, `OPENSWARM_OPENCODE_DB` | Non-standard OpenCode install locations |
| `OPENSWARM_CURSOR_BIN` | Cursor CLI binary. Otherwise `cursor-agent` on `PATH`, then `~/.local/bin/cursor-agent` |
| `OPENSWARM_STATE_FILE` | Offline mode: route against a saved or synthetic state; nothing is probed |
| `XSWARM_*` | Legacy names from before the rename (formerly *xswarm*); still honoured, `OPENSWARM_*` wins |

### What openswarm reads on your machine

openswarm reads other tools' local files only to measure quota. It never uploads them. The `status` output
starts with a `Sources` line showing what is enabled.

| Source | Default | What is read | Switch |
| --- | --- | --- | --- |
| `~/.codex/sessions/**/rollout-*.jsonl` | on | Only `rate_limits` events (used %, reset time); conversation content is skipped | `local_sources.codex_sessions` |
| `~/.codex/models_cache.json` | on (fallback) | Model catalogue, when `codex debug models` is unavailable | none |
| `~/.local/share/opencode/opencode.db` | on | Read-only: model id and cost per assistant message | `local_sources.opencode_db` |
| `~/.codex/auth.json` | **off** | Only the `chatgpt_plan_type` claim, decoded locally. The token is never sent or stored | `local_sources.codex_auth_plan` |
| `~/.claude.json` | **off** | Only the plan/tier fields of `oauthAccount` | `local_sources.claude_account_plan` |
| macOS Keychain | **off** | Only items you list in `deepseek.keychain_items` | `deepseek.keychain_items` |

Prompts and diffs go to the executor's model provider. Each backend's data boundary is listed in
`backends/*.json`. Don't put secrets in task prompts.

## Safety model

- Codex runs with `-s workspace-write`. OpenCode gets edit and bash permission scoped to the worktree with `external_directory: deny`.
- Executors never commit; openswarm commits on `openswarm/<id>`. Nothing is pushed or merged automatically.
- If a worktree can't be created, the run fails. It never falls back to editing your checkout in place.
- Codex `ultra` effort is never used, because it self-delegates and blows budgets.

## Executor guardrails

Executor sessions stay outside worktrees: Pi uses each run's `pi-sessions/` or
`$OPENSWARM_HOME/lanes/<name>/pi-sessions/`. `workspace.never_commit` overrides the
default executor session/log exclusions (Pi, Codex, Cursor); excluded changed files
are listed in `result.json` as `excluded_files`, including files staged by an executor.

Daily guardrails: set `budget.daily_executor_minutes`, optional `budget.per_backend`
(backend → minutes), `budget.per_run_max_minutes`, and `budget.timezone` (IANA name;
defaults to the local timezone) in policy. Caps are optional. `openswarm budget status --json`
shows today's ledger usage. Admission reserves requested minutes for live runs; runs,
lane assignments and batches refuse excess work with `budget_exceeded_daily` (exit 3).
Budgets apply to the ledger on each host; remote runs also check that host's policy.

`risk.enforce` defaults to `["push", "publish", "secret print", "rm outside worktree", "sudo"]`.
The runner stops one-shot executors or aborts lane turns on matching command events,
without requiring `watch`. Results include `status: stopped`, `error_class: risk_blocked`
and `risk_blocks` with redacted commands. An empty list disables enforcement.
This is event-based containment: backends that emit commands after execution cannot
prevent the command's initial side effects. Executor sandbox restrictions still matter.

`openswarm report --daily --date YYYY-MM-DD --html report.html --json` writes one
self-contained HTML page with light/dark styling and emits the same day's JSON.
Omit `--date` for today in the policy timezone. Reports read local ledger data without
probing providers: backend/lane outcomes, executor minutes, reported tokens, budgets,
risk blocks, slowest runs and failed-run summaries. Fallback usage is attributed to
each backend; a run can therefore appear in more than one backend row. Missing token
usage counts as zero; verification time is excluded from executor minutes.

## Status and limitations

- Developed and used on macOS; Linux is covered by CI; Windows is untested.
- Model names, plan caps and the OpenCode Go price table in `policy.json` change often, so PRs are welcome.
- The Claude quota comes from Claude Code's statusline hook or `openswarm ingest-claude`. Without either, reviews default to `light`.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Run the tests (stdlib only, no backends needed):

```bash
python3 -m unittest discover -s tests -v
```

## License

[Apache-2.0](LICENSE)
