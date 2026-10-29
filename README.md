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
  - [OpenCode](https://opencode.ai) Go and DeepSeek are opt-in backends: force either with `--backend`, or add it to `routing.default_order` to enable automatic routing.
  - Automatic routing defaults to Codex only; configured OpenCode Go and DeepSeek can handle mechanical work, non-GPT second opinions, overflow, and large context.
  - Cursor CLI (`cursor-agent`) runs only when you pass `--backend cursor`, or when you add `cursor` to `routing.default_order`. It is not part of automatic routing.

Routing decisions use live quota data, not guesses.

## Features

- **Live model discovery.** Reads the current Codex and OpenCode model catalogues and sorts them into tiers (fast, balanced, deep). Legacy models go last.
- **Quota-aware routing.** Looks at 5-hour, 7-day and 30-day windows, burn pace and reset times:
  - At the soft limit, easy work spills to other enabled backends.
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
- **Learning loop.** The ledger records the pass decision, the injected lessons and patterns, and the routing advice of every run. `lessons propose`, `patterns propose`, and `retro` turn those records into reviewable drafts.
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
openswarm resume                     # recover live runs, busy lanes and recent results after planner restart
openswarm resume --since-hours 12 --json
openswarm report --since-minutes 60  # one-screen digest for scheduled check-ins
openswarm stop <id>                  # kill the executor; partial work is still committed to its branch
```

Pi messages appear once at `message_end`; Cursor chunks are merged until a prompt turn ends or a tool call starts.
Codex events keep their existing behavior. `--quiet` keeps only `DONE`, `EXITED`, `RISK`, `STALL`, `ERROR`,
`BUDGET`, `ATTEMPT`, and `HEARTBEAT`. Heartbeats include elapsed time, command count, the worktree's git diff
summary (including lane worktrees), and the last completed message truncated to 200 characters. The watch window
still expires with `--quiet`; re-arm with `--from-end` if needed.

## Command reference

Every subcommand, grouped by what it does. `openswarm <command> --help` lists all flags.

### Execute

| Command | Purpose | Example |
| --- | --- | --- |
| `openswarm route` | Pick backend, model, and effort for a task profile without running anything. | `openswarm route --type impl --difficulty 3 --card-type fix` |
| `openswarm run` | Route a task, execute it in an isolated worktree, verify it on the host, and record the ledger entry. | `openswarm run --cwd ~/code/myrepo --type impl --difficulty 2 --budget-minutes 15 --verify 'pytest -q' --task-file /tmp/task.md` |
| `openswarm run --detach` | Run in the background and print the run id to watch. | `openswarm run --detach --cwd ~/code/myrepo --verify 'pytest -q' --task-file /tmp/task.md` |
| `openswarm stop` | Kill a running executor; partial work stays on its branch. | `openswarm stop <run-id>` |
| `openswarm lane up` | Start the configured lane supervisors. | `openswarm lane up C1 K1` |
| `openswarm lane assign` | Give one task to an idle lane; returns the run id. | `openswarm lane assign C1 --task-file /tmp/task.md --title "Parser fix" --verify 'pytest -q' --budget-minutes 30` |
| `openswarm lane steer` | Send a correction to the lane's active turn. | `openswarm lane steer C1 --text "Add a regression test before finishing"` |
| `openswarm lane abort` | Abort the lane's active turn. | `openswarm lane abort C1` |
| `openswarm lane status` | Show the configured lane state. | `openswarm lane status` |
| `openswarm lane down` | Stop lane turns, children, and supervisors. | `openswarm lane down C1 K1` |
| `openswarm lane launchd-plist` | Print an uninstalled LaunchAgent plist for a lane. | `openswarm lane launchd-plist C1` |
| `openswarm lane systemd-unit` | Print an uninstalled systemd unit for a lane. | `openswarm lane systemd-unit K1` |
| `openswarm batch` | Queue task cards across fleet hosts and collect their branches. | `openswarm batch --repo project --cards ./cards --hosts worker,sandbox --per-host 1 --backend pi --budget-minutes 20 --detach` |
| `openswarm batch status` | Show the state of one batch. | `openswarm batch status <batch-id>` |
| `openswarm bus init` | Create the bus labels, issue template, and protocol README. | `openswarm bus init acme/openswarm-bus` |
| `openswarm bus status` | Show open bus issues with their executor and age. | `openswarm bus status --json` |
| `openswarm bus-worker` | Run the reference bus executor; `--once` claims at most one queued issue. | `openswarm bus-worker --bus acme/openswarm-bus --name runner-1 --backend codex --once` |
| `openswarm verify-remote` | Verify a fleet run's commit in a temporary worktree on another host. | `openswarm verify-remote --from worker --run <run-id> --on local --verify 'pytest -q'` |

### Observe

| Command | Purpose | Example |
| --- | --- | --- |
| `openswarm watch` | Stream one line per actionable event for one or more runs. | `openswarm watch <run-id> --label parser-fix` |
| `openswarm watch --quiet` | Keep only terminal, risk, stall, error, budget, attempt, and heartbeat events. | `openswarm watch <run-id> --quiet` |
| `openswarm watch --from-end` | Resume a watch whose window expired; events already written are skipped. | `openswarm watch <run-id> --from-end` |
| `openswarm status` | Show plans, quota windows, models, breakers, and recommended concurrency. | `openswarm status --refresh` |
| `openswarm probe` | Discover models, plans, and quotas into `$OPENSWARM_HOME/state.json`. | `openswarm probe --quiet` |
| `openswarm runs` | Snapshot recent or active runs. | `openswarm runs --active --json` |
| `openswarm report` | One-screen check-in for scheduled updates: quotas, active runs, recently finished. | `openswarm report --since-minutes 60` |
| `openswarm budget status` | Show today's executor budget usage. | `openswarm budget status --json` |
| `openswarm ingest-claude` | Record Claude plan usage from the statusline hook or from flags. | `openswarm ingest-claude --five-hour 80 --seven-day 40` |
| `openswarm fleet status` | Show reachability, paused hosts, lanes, and active runs. | `openswarm fleet status --json` |
| `openswarm fleet exec` | Run an openswarm command on another host; stdout and the exit code are forwarded. | `openswarm fleet exec worker -- lane status` |
| `openswarm fleet ledger pull` | Merge the host ledgers into one fleet ledger file. | `openswarm fleet ledger pull --hosts worker,sandbox` |
| `openswarm run --rework-of` | Resume an earlier run's task as a rework, linked in the ledger as `rework_of`. | `openswarm run --rework-of <run-id> --cwd ~/code/myrepo --task-file /tmp/task.md` |

### Learn

| Command | Purpose | Example |
| --- | --- | --- |
| `openswarm ledger` | List recent ledger records. | `openswarm ledger -n 20 --json` |
| `openswarm ledger --fleet` | List the merged fleet ledger. | `openswarm ledger --fleet -n 50` |
| `openswarm ledger ingest` | Link GitHub PR reviews and defect results into the fleet ledger. | `openswarm ledger ingest --repo acme/project --since 30` |
| `openswarm ledger accept` | Record a manual accept or reject decision for a finished run. | `openswarm ledger accept <run-id> --reason "Verified on host"` |
| `openswarm ledger backfill-pass` | Compute `exec_pass` for older ledger records. | `openswarm ledger backfill-pass --fleet --recompute` |
| `openswarm lessons preview` | Print the lesson and pattern block a dispatch would inject. | `openswarm lessons preview --card-type fix --backend codex --paths src/file.py` |
| `openswarm lessons propose` | Draft lessons from linked P0/P1 findings of recent runs. | `openswarm lessons propose --fleet --since 30 --dry-run` |
| `openswarm lessons escalate` | Draft L3 guard proposals for recurring lessons. | `openswarm lessons escalate --dry-run` |
| `openswarm patterns propose` | Draft patterns from compare winners and repeated peer-review findings. | `openswarm patterns propose --since 30` |
| `openswarm patterns retire-check` | Propose retirement when active patterns do not improve outcomes. | `openswarm patterns retire-check` |
| `openswarm routing weights` | Compute per-backend routing metrics and write the weights file. | `openswarm routing weights --fleet` |
| `openswarm routing compare` | Compare recorded routing advice with the actual run outcomes. | `openswarm routing compare --days 30 --fleet` |
| `openswarm retro` | Run the weekly retrospective: pull, ingest, backfill, weights, proposals, report. | `openswarm retro --days 7 --repos acme/project` |
| `openswarm regress run` | Run the regression card suite on a backend, a lane, or a routing mode. | `openswarm regress run --backend codex --cards card-a,card-b --label nightly --parallel 2` |
| `openswarm regress compare` | Compare two regression result files card by card. | `openswarm regress compare before.json after.json` |

A ledger record carries the outcome and what the next run learns from it: `exec_pass` and `exec_pass_reason`
(change evidence plus host verification decide the pass, not the executor's own report), `accepted`,
`accepted_reason`, `accepted_at`, `verify_rc`, `tokens`, `lessons_injected`, `patterns_injected`,
`routing_advice`, `peer_review`, `rework_of`, `host`, and `lane`. After `ledger ingest` it also carries `pr`,
`review_findings`, `merged`, and `defect_within_7d`.

### Peer

| Command | Purpose | Example |
| --- | --- | --- |
| `openswarm consult` | Ask several executors the same question in read-only mode and compare the answers. | `openswarm consult --question "Which parser change is safest?" --ask codex --ask pi --json` |
| `openswarm review` | Review a finished run's branch with a read-only executor from another model family. | `openswarm review <run-id> --by codex --json` |
| `openswarm compare` | Run one card on two executor families and record the best-of-two comparison. | `openswarm compare --task-file /tmp/task.md --title "Parser fix" --a codex --b pi --verify 'pytest -q'` |

## Learning loop

The loop turns finished runs into better next runs. Each stage has its own command, and the curation commands write
reviewable drafts instead of editing the lesson or pattern catalog.

1. **Record.** Every run appends a ledger entry. Merge the host ledgers, link GitHub PR data, and record the
   accept or reject decision:

   ```bash
   openswarm fleet ledger pull --hosts worker,sandbox
   openswarm ledger ingest --repo acme/project --since 30
   openswarm ledger accept <run-id> --reason "Verified on host"
   ```

2. **Inject.** `run`, `lane assign`, `batch`, and `bus-worker` add matching L2 lessons and L3 reminders to the
   executor prompt, plus up to three active patterns. Preview the block before dispatching:

   ```bash
   openswarm lessons preview --card-type fix --backend codex --paths src/file.py
   ```

3. **Review.** Let a second executor look at the finished branch:

   ```bash
   openswarm review <run-id> --by codex --json
   openswarm compare --task-file /tmp/task.md --title "Parser fix" --a codex --b pi
   openswarm consult --question "Is this retry safe?" --ask codex --ask pi
   ```

4. **Lessons.** Turn review findings into lesson, guard, and pattern drafts:

   ```bash
   openswarm lessons propose --fleet --since 30
   openswarm lessons escalate --dry-run
   openswarm patterns propose --since 30
   openswarm patterns retire-check
   ```

5. **Weekly retro.** One command runs the loop over the last week and writes the report:

   ```bash
   openswarm retro --days 7 --repos acme/project
   openswarm routing weights --fleet
   openswarm routing compare --days 30 --fleet
   ```

   Measure a fixed card suite the same way when an executor or a prompt changes:

   ```bash
   openswarm regress run --backend codex --label nightly
   openswarm regress compare before.json after.json
   ```

Drafts land in `<knowledge.repo>/proposals/<date>/`, the retro report in `<knowledge.repo>/retro/<YYYY>-W<ww>.md`,
and the regression results in `<knowledge.repo>/regression/results/`. Copy a reviewed lesson into
`<knowledge.repo>/lessons/` and a reviewed pattern into `<knowledge.repo>/skills/` to make the next dispatch
inject it.

## Executor lessons

Dispatches read `lessons/*.yaml` from `policy.knowledge.repo` (default `~/work/agent-knowledge`). Matching L2
lessons and L3 reminders are added to the executor prompt, with at most 12 entries and 3,000 characters. A missing
repository adds no block. Use `--paths src/file.py` on `run`, `lane assign`, `batch`, or `bus-worker` to provide
path hints alongside paths named in the task card. Preview a selection with:

```bash
openswarm lessons preview --card-type fix --backend codex --paths src/file.py
```

Injected lesson ids are recorded in the run ledger's `lessons_injected` field.

Propose review-derived lessons from recent ledger runs linked to P0/P1 Codex inline findings with:

```bash
openswarm lessons propose [--fleet] [--since DAYS] [--out DIR] [--dry-run]
```

New lesson and recurrence proposals are written under `knowledge.repo/proposals/<date>/` by default. The command
tracks processed inline comment ids in `knowledge.repo/proposals/seen.json`; it never edits existing lessons.
`--dry-run` prints the proposed YAML and summary without writing proposal or seen-index files.

Escalate recurring lessons into reviewable L3 guard drafts with:

```bash
openswarm lessons escalate [--out DIR] [--dry-run]
```

The command writes `guard-<lesson-id>.md` proposals for non-L3 lessons with at least one recurrence, including
accepted recurrence proposals still under `proposals/`. Each draft maps its root-cause class to a fixed guard kind
and includes evidence links plus the required accompanying test. `proposals/escalate-seen.json` makes runs
idempotent; escalation never edits lessons or enforcement code.

## Curated patterns

Active patterns live in `<knowledge.repo>/skills/<id>.md`; see [`skills/README.md`](skills/README.md) for the front matter
and body format. `run` and lane dispatches add up to three matching active patterns after lessons, capped at 1,500
characters total. `lessons preview` shows both selected lessons and patterns. Injected pattern IDs are recorded in
`patterns_injected` in the run ledger.

Draft candidates from compare winner proposals and repeated peer-review findings with:

```bash
openswarm patterns propose [--since DAYS]
```

Check active patterns against similar runs from the last 30 days with:

```bash
openswarm patterns retire-check
```

Retirement proposals require at least eight injected and eight control runs for the same card type and backend, with
both `exec_pass` and `accepted` rates available. Both commands write reviewable drafts under
`<knowledge.repo>/proposals/<date>/`; they do not edit the pattern catalog.

## Routing weights (record-only)

`openswarm routing weights` summarizes ledger runs by backend, model and card type. It writes
`<knowledge.repo>/routing/weights.json` (default sample threshold: 8); use `--fleet` to read the merged fleet
ledger or `--out PATH` to choose another output path. The effective pass rate uses manual `accepted` decisions
when present and otherwise uses `exec_pass`; the report also includes each source rate and its record count,
first-try, rework and P1+ review rates, plus median tokens and seconds. Groups below `routing.min_samples` scored
runs are marked `enough: false`.

Run `openswarm review <run_id> [--by BACKEND[:MODEL]] [--json]` to review a finished run's branch against its base
with a read-only executor from another model family. Review output is saved to `runs/<id>/review.json`; the ledger
stores its reviewer, verdict, and P0/P1/P2 counts under `peer_review`, separate from GitHub's `review_findings`.
The default `review.auto_card_types` (`feature`, `fix`, `review-fix`, `migration`) starts reviews asynchronously
after a committed run. `review.prefer` selects the first available reviewer; `review.max_concurrent` bounds
automatic review jobs, while `review.timeout_seconds` and `review.max_diff_chars` cap review work.

Run the same card on two executor families with:

```bash
openswarm compare --task-file TASK.md --title TITLE \
  [--a BACKEND[:MODEL]] [--b BACKEND[:MODEL]] [--verify CMD] \
  [--card-type TYPE] [--budget-minutes N]
```

If either backend is omitted, compare selects an available backend from a different family. Both normal run
admissions apply the daily budget and provider concurrency caps. Each finished diff receives a cross-family
review; review errors are retained in the comparison and do not stop the other review. The winner is selected
by verification pass, fewer P0/P1 findings (when both reviews succeed), smaller changed-line count, then fewer
seconds; an exact tie has no winner. Results are saved under `runs/<compare-id>/compare.json` and
`compare.md`; a winner also creates a `pattern-<compare-id>.md` proposal under
`knowledge.repo/proposals/<date>/`.

Review completed runs with `openswarm ledger accept <run_id> [--reject] [--reason TEXT] [--fleet]`. The decision,
reason and timestamp are recorded atomically. `openswarm ledger ingest` sets acceptance from merged or closed
without merge PRs unless a manual decision is already present. Use `openswarm ledger backfill-pass [--fleet]` to
compute `exec_pass` for eligible older records.

`openswarm route --card-type fix` and `openswarm run --card-type fix` attach `routing_advice` when enough historical
samples exist for currently eligible backends. Advice ranks pass, first-try, rework and P1+ rates, then uses median
tokens and seconds as cost tie-breakers. It is recorded alongside the real route and does not change the selected
backend or model; `routing.weighted` remains false and reserved for future routing behavior. Compare recorded advice
with actual outcomes using `openswarm routing compare --days 30`. An advice outcome is shown as `not run` when the
actual route used a different backend or model, because that counterfactual was not executed.

Run the weekly fleet retrospective in one command:

```bash
openswarm retro [--days 7] [--out DIR] [--repos owner/name ...] [--no-pull]
```

By default it pulls fleet ledgers, ingests GitHub PR data for the selected repositories, backfills `exec_pass`,
updates routing weights, and proposes lessons (plus escalation proposals when that subcommand is available). It writes
`<knowledge.repo>/retro/<YYYY>-W<ww>.md` and prints a JSON summary. `--no-pull` reuses the current fleet ledger;
`--out` changes the report directory. A GitHub rate limit is recorded in the report and does not stop the remaining steps.

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
| `--type review/audit` | Prefers a non-GPT family first when one is enabled in `routing.default_order` |
| `--type mechanical/docs/test` at difficulty ≤ 2 | Prefers OpenCode Go first when it is enabled in `routing.default_order` |
| `--difficulty 1..5` | Sets tier and effort: fast/low, fast/medium, balanced/medium, balanced/high, deep/high |
| `--context-tokens` > 220k | Prefers enabled 1M-context backends |
| A window ≥ 95% used | Skips the backend |
| A window ≥ 85% used | Spills difficulty ≤ 3 to other backends; harder tasks stay with effort −1 |
| Burn pace > 1.25× even | Effort −1 |
| Resets within 24h with < 60% used | Effort +1, so quota isn't wasted |
| DeepSeek balance below floor | Skips DeepSeek when it is enabled for routing |
| `--backend ocgo` or `--backend deepseek` | Forces that opt-in backend; automatic routing uses either only when listed in `routing.default_order` |
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
| `bin/openswarm` | The CLI: `probe`, `status`, `route`, `run`, `lane`, `batch`, `bus`, `bus-worker`, `verify-remote`, `watch`, `runs`, `resume`, `report`, `budget`, `stop`, `fleet`, `consult`, `review`, `compare`, `regress`, `routing`, `lessons`, `patterns`, `retro`, `ingest-claude`, `ledger` |
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

### Pi text-only public input

**Currently disabled:** `pi-text` fails before dispatch because Pi cannot bind a proven exact registry selection to the dispatched process. Pi can partial-match or construct a custom fallback model, and provider case normalization can differ from the lease key. A separate model listing is credential-filtered and cannot prove the later process selection. The intended `openswarm pi-text --public-input-approved --text 'public question'` path is a separate opt-in path for input independently approved as non-sensitive and public. The input is limited to 16,000 UTF-8 bytes. It uses Pi's no-tools, no-extensions, no-skills, no-prompt-templates, no-themes, no-context-files and no-session flags, and refuses to run if Pi does not advertise them. Only Pi 0.73.0 and 0.73.1 are supported for stdin transport; other versions are rejected. Pi trims piped input, so trailing ECMAScript whitespace is rejected to preserve the supplied text. The prompt is sent to Pi on stdin, not in Pi's arguments. However, the `--text` argument is still visible in the openswarm process arguments and potentially shell history; do not include secrets. Openswarm does not persist prompts, subprocess output, or errors in its run logs. The answer is emitted as JSON on stdout and is not redacted; Pi or the provider could echo the input or other data.

Pi text requires explicit `pi.provider` and `pi.default_model` in the openswarm policy. Both must use letters, digits, dots, underscores, colons or hyphens, starting with a letter or digit; slash-qualified models are unsupported. Pi is invoked with both values, and assistant events reporting a different provider or model are rejected, including a conflicting optional `responseModel`. When enabled, Pi text execution waits for the configured Pi, Pi provider, and total concurrency caps using the same local leases as other executions. These leases coordinate processes sharing the same `OPENSWARM_HOME`; separate homes do not share a host-wide limit. A queued or running text request can be stopped using its `pi-text-slot-*` run ID.

This is not filesystem isolation or a security broker. It relies on the selected Pi executable honoring its advertised flags and version; a wrapper, compromised executable, provider configuration, or provider can violate those assumptions. Pi still has host process and credential access, inherits the environment without isolation, and sends the input to its configured provider; provider access, retention and logging are outside openswarm's control. Cleanup targets the subprocess group and cannot guarantee termination of descendants that escape that group. Separately approve each input and provider. Code-capable Pi execution remains unsuitable where filesystem isolation is required, and this mode does not enable or authorize it. Existing run, lane, review and consult modes retain their existing capabilities and are not covered by this text-only path.
