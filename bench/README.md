# Head-to-head routing benchmark

openswarm and [oh-my-openagent](https://github.com/code-yeongyu/oh-my-openagent) (OmO) v5.1.35 answer the same question: given a delegated coding task and a fixed set of subscriptions, which model runs it?

This benchmark compares that routing slice only. OmO is a multi-agent harness; most of what it does is outside this comparison. The benchmark was written by the openswarm maintainer, so the task mix and the tier labels can favor the tool that defined them. The trace, the catalogue, both decision files, and the score tables are published under `bench/` so the result can be recomputed.

The catalogue now includes the configured `gpt-6-luna` default for offline routing. Its benchmark parameters are illustrative, and the checked-in result files predate this catalogue and policy change; regenerate both sides before making a new comparison.

## Fairness rules

- Both tools see the same tasks, the same token estimates, and the same quota scenario.
- The model ids are the intersection of the two pinned trees. Codex slugs are the exception: openswarm does not pin them (it classifies live `codex debug models` text). The slugs here are the ones OmO's `packages/model-core` category chains and `supplemental-entries.ts` name, with descriptions that `policy.json` `tier_keywords` classify. See `catalogue.json`.
- Connected providers are the three backends openswarm can target: `chatgpt-subscription` (the Codex plan), `opencode-go`, and `deepseek`. The OpenAI API provider, Anthropic, Google, Copilot, OpenCode Zen, xAI, Z.ai, Xiaomi, and Moonshot are not connected. A chain rung that names only those providers is unreachable.
- openswarm's `exclude_patterns` still apply on the openswarm side. OmO does not read that list.
- No network calls, no paid API, no `codex` or `opencode` process. Routing is in-process.
- Task labels were fixed in `rubric.py` and this file before `bench/run.sh`. They are not adjusted after a score.
- Dollar amounts and window shares are `prices.json` × estimated tokens. They are not invoices. The file says so on every cost figure.

## What each router actually does

openswarm `route()` (`bin/openswarm`) picks a backend order from the task type, then a model inside that backend:

- `review`, `second-opinion`, and `audit` try OpenCode Go, then Codex, then DeepSeek.
- `mechanical`, `docs`, `test`, `format`, `rename`, and `migration` at difficulty ≤ 2 try OpenCode Go first.
- Everything else tries Codex, then OpenCode Go, then DeepSeek.
- Context above 220k tokens moves OpenCode Go and DeepSeek ahead of Codex.
- Difficulty sets the tier and effort: 1 fast/low, 2 fast/medium, 3 balanced/medium, 4 balanced/high, 5 deep/high.
- A Codex window ≥ 95% skips Codex. A window ≥ 85% spills difficulty ≤ 3 and keeps harder work at effort −1.
- An OpenCode Go model at ≥ 85% of any window budget is skipped. DeepSeek is skipped when the balance is under ¥10.

OmO resolves a delegated `task` category through `resolveModelPipeline` in `packages/model-core`. The category's builtin default model is tried first, then `CATEGORY_MODEL_REQUIREMENTS[category].fallbackChain`. A rung matches only a connected provider that serves that model (`fuzzyMatchModel`). `writing` sets `requiresAnyModel`, so it stays unresolved when none of its own models are connected; there is no system-default fallback in this bench.

OmO does not read a quota window. Exhaustion is simulated the way the runtime learns it:

1. The pipeline picks a model from availability alone.
2. If that provider would return a usage-limit or insufficient-balance error, the attempt counts as failed.
3. The error is passed to `classifyRuntimeFallbackError` and `shouldRetryError`. The simulated errors are `{message: "usage limit exceeded", statusCode: 429}` and `{message: "insufficient balance"}`. Both classify as `quota_exceeded` and are retryable.
4. `getNextFallback` walks the chain from the start. Unreachable rungs and a no-op repeat of the model that just failed are skipped with no attempt, matching the background-agent retry loop. The next reachable exhausted rung is another failed attempt. The first reachable model that is not exhausted is the one that runs.

A Codex window ≥ 95% exhausts every `chatgpt-subscription` model. An OpenCode Go model is exhausted only when its max window usage is ≥ 1.0 (the budget is gone). Usage of 0.90 is above openswarm's 85% skip and is not an OmO error. DeepSeek is exhausted below ¥10.

model-core has no standalone route command. OmO latency is in-process, after bun has loaded the module. It is not comparable to `openswarm route` cold start.

## Rubric

`min_tier` is the minimum adequate capability, taken from openswarm's difficulty map, with one exception. Agreement with that map is reported later as "Agreement with the difficulty→tier rubric". It is not a quality score. openswarm agrees with the map by construction whenever quota still allows the tier it would have picked on a fresh window, so the row shows how each router deviates under quota pressure.

| Difficulty or type | Minimum tier |
| --- | --- |
| 1–2 | fast |
| 3–4 | balanced |
| 5 | deep |
| `architecture` | deep, at any difficulty (every architecture task in the trace is difficulty 5) |

`independent_review` is true for `review` and `audit`.

OmO has no type field. The category mapping is a judgment:

| openswarm shape | OmO category | Why |
| --- | --- | --- |
| `docs` | `writing` | Prose lane. Its chain names Google and Anthropic only. |
| `review` | `unspecified-high` | No review category. This is the broad multi-surface lane. |
| `audit`, `architecture` | `ultrabrain` | The lane OmO reserves for a hard reasoning task. |
| difficulty ≤ 2, any other type | `quick` | Small or mechanical work, including quick fixes. |
| difficulty 3, any other type | `unspecified-low` | Moderate work with no specialist category. |
| difficulty 4, any other type | `unspecified-high` | Larger multi-file work. |
| difficulty 5 `debug` | `deep-high` | Treated as a cause that one read may not settle. |
| other difficulty 5 | `deep-low` | OmO's default deep lane. |

`research` is a large-context read. openswarm has no special case for that type, so it uses the default Codex-first order unless context exceeds 220k tokens. The category still follows the difficulty row above.

Token estimates are a formula, not a measurement. fast is 8k/0.8k input/output, balanced 22k/3k, deep 64k/9k. A context of at least 220k replaces the input with that context and sets output to 1.5k. Review and audit inputs are at least 36k and outputs at least 4k. Context for an ordinary task is 8k, 24k, or 48k by tier. The large-read tasks set 280k or 640k explicitly.

The reference implementer, for the independence metric, is the model chosen for the first `impl` at difficulty 3 whose context is under 220k (`t06`), in that same scenario. A review counts as independent when its model family differs from that implementer's family. An unrouted review does not.

An unrouted decision counts as under-provisioned. The unrouted rate is also reported alone.

## Catalogue

Tiers for Codex models come from `classify_codex` on the description. Tiers for usable OpenCode Go models come from the same price-tercile cut as `probe_ocgo` (sort by `cost_out`, cheapest third fast, next third balanced, rest deep). Equal prices keep catalogue order. Excluded Go models never enter that cut; their scoring tier is the judgment noted in the table.

| Model | Pool | Tier | Notes |
| --- | --- | --- | --- |
| `gpt-6-astra` | Codex subscription | deep | OmO ultrabrain and deep-high. Context 1,050,000. |
| `gpt-6.1-sol` | Codex subscription | balanced | OmO deep-low primary and the Codex difficulty-3 default. Context 400,000. |
| `gpt-5.6-sol` | Codex subscription | balanced, legacy | Later Codex rung. |
| `gpt-6-luna-fast` | Codex subscription | fast | OmO quick primary. |
| `gpt-6.1-sol-fast` | Codex subscription | fast | deep-low rung. |
| `gpt-5.6-sol-fast` | Codex subscription | fast, legacy | deep-low rung. |
| `glm-5.3-flash` | OpenCode Go, cap $60 | fast | Openswarm can select it. OmO's quick rung names `zai-coding-plan` only, which is not connected. |
| `mimo-v2.6-flash` | OpenCode Go, cap $60 | fast | OmO's quick rung names `xiaomi` only. |
| `grok-4.7` | OpenCode Go, cap $15 | balanced | Context 500,000 from model-core. |
| `mimo-v2.6-pro` | OpenCode Go, cap $15 | balanced | unspecified-low rung. Context 1,000,000 (Go default; no supplemental entry). |
| `glm-5.3` | OpenCode Go, cap $15 | deep | unspecified-high rung. |
| `kimi-k3` | OpenCode Go, cap $15 | deep | Context 262,144 from model-core. |
| `minimax-m3` | OpenCode Go, cap $15 | balanced (judgment) | Excluded by openswarm (`minimax`). OmO quick can still select it. |
| `minimax-m2.7` | OpenCode Go, cap $60 | fast (judgment) | Same exclusion. |
| `deepseek-flash` | DeepSeek, metered | fast | policy fast tier and OmO quick rung. |
| `deepseek-v4-pro` | DeepSeek, metered | deep | policy uses this id for both balanced and deep. A balanced task that lands here counts as over-provisioned. |

OpenCode Go per-token prices are the three bands in `examples/demo-state.json`: cap 60 uses cheap-flash (0.1 / 0.4 USD per 1M input/output), cap 15 uses big-pro (1.5 / 6.0). Those prices fill the subscription window. They are not a second bill. The 5-hour window is 20% of the monthly cap (`policy.json`).

DeepSeek prices are the off-peak cache-miss list from <https://api-docs.deepseek.com/quick_start/pricing/>, retrieved 2026-10-17: `deepseek-flash` 0.15 / 0.60, `deepseek-v4-pro` 0.66 / 1.98 USD per 1M tokens. Peak hours double those rates. The bench does not model cache hits or peak.

Codex has no per-token price. Its 5-hour share is `(input + output tokens) / 2,000,000`. The denominator is a bench convention, not an OpenAI quota. Compare tools only on that same denominator.

Codex context figures are the model-core supplemental limits. A 640k read needs `context > 640000 × 1.15`, so `grok-4.7` (500k) and `kimi-k3` (262k) do not fit that task. openswarm still prefers a 1M-class backend once context exceeds 220k, even when a Codex model has a larger window. That threshold is `policy.json`.

## Scenarios

Pace and the "quota resets soon, raise effort" rule are not exercised. Windows have no `pace` and no `hours_to_reset`, so those openswarm adjustments do not move the score.

| Id | Codex 5h / 7d | OpenCode Go | DeepSeek |
| --- | --- | --- | --- |
| `fresh` | 0.12 / 0.31 | unused | ¥55 |
| `codex_soft_90` | 0.90 / 0.40 | unused | ¥55 |
| `codex_hard_97` | 0.97 / 0.55 | unused | ¥55 |
| `ocgo_hot` | 0.20 / 0.30 | `glm-5.3-flash`, `grok-4.7`, `kimi-k3` at 0.90 | ¥55 |
| `deepseek_floor` | 0.12 / 0.31 | unused | ¥3 |
| `mixed_late_week` | 0.92 / 0.88 | `kimi-k3` and `glm-5.3` at 1.0 on the 30d window; `grok-4.7` at 0.90 on 5h | ¥12 |
| `tight_friday` | 0.97 / 0.80 | the six scarce Go models at 1.0; the two flash models at 0 | ¥40 |

## Trace

`trace.jsonl` has 80 tasks: implementation, refactor, mechanical edits, renames, formatting, docs, tests, review, audit, debugging, architecture, and large-context reads. Regenerate with `python3 bench/make_trace.py`. `tests/test_bench.py` checks the file against `rubric.py`.

## Commands

From the repository root:

```bash
bench/run.sh --omo-dir /path/to/oh-my-openagent
```

The OmO checkout used for the published run is tag `v5.1.35`, commit `07776864a043acdda9f9976ac8f71659a25f1d86`. Results record that checkout as `<omo-dir>` plus the commit. bun on this host was 1.4.2.

If `--omo-dir` is missing, or bun is missing, the script scores openswarm and the three baselines and writes `bench/results/omo_skipped.txt`.

Outputs in `bench/results/`:

| File | Contents |
| --- | --- |
| `openswarm.jsonl`, `omo.jsonl` | One decision per task per scenario |
| `baselines.jsonl` | Naive baselines: everything on `gpt-6.1-sol`, everything on `gpt-6-astra`, everything on `glm-5.3-flash` |
| `scores.json`, `scores.csv`, `summary.md` | Metrics, including the unrouted breakdown |
| `coverage.json` | Providers and model ids named in the pinned sources |
| `chart.svg`, `chart.png` | Grouped bars for the objective metrics |
| `latency_openswarm.json`, `latency_omo.json` | Median and p95 |
| `environment.json` | CPU, core count, OS, kernel, Python, bun, both commits, date, commands |

A decision carries `task_id`, `scenario`, `tool`, `backend`, `provider`, `model`, `effort`, `variant`, `tier`, `attempts`, `failed_attempts`, and `reasons`. `attempts` includes failed attempts on an exhausted quota.

Baselines have no fallback. If the single model is exhausted they record one failed attempt and no model. A hot-but-not-exhausted Go model (usage 0.90) does not fail a baseline, because a naive caller would not see a usage-limit error yet.

## Metrics

- Wasted first attempts: share of decisions whose first try hit an exhausted quota, per scenario and overall. Total failed attempts are reported beside that, because a chain can miss more than once.
- Unrouted: decisions that end with no model. Broken down by OmO category and reason. On this provider set a category can be unrouted because its chain names only providers that are not connected (the `writing` chain names Google and Anthropic), or because every reachable rung hit a usage-limit error. That is a consequence of connecting Codex, OpenCode Go, and DeepSeek, not of OmO in general.
- Review independence: share of review and audit tasks whose model family differs from the scenario's reference implementer.
- Computed cost: metered USD (DeepSeek), OpenCode Go accounting USD, and Codex token totals. Subscription usage is the sum across scenarios and the mean per scenario. Each scenario is one pass over the 80-task trace, so the per-pass figure is the share of the nominal 5h budget one pass would use. Failed attempts add no token cost. The call was rejected before generation.
- Coverage: distinct providers and model ids referenced by OmO model-core category and agent fallback chains, and the number of categories, counted from the pinned commit. Compared with openswarm's three backends and the model ids in `policy.json`.
- Agreement with the difficulty→tier rubric: exact, over, under against `min_tier`. This is not a quality score and it is not the headline. openswarm agrees by construction when quota allows. Unrouted is included in under.
- Latency: in-process per decision, at least 1000 samples, median and p95. openswarm also reports CLI cold start over 25 runs of `bin/openswarm route --type impl --difficulty 3`.

## Environment

Recorded by `bench/run.sh` into `bench/results/environment.json` on 2026-10-17T11:32:00Z. Paths in that file are relative to the repository root. The OmO checkout is recorded as `<omo-dir>` plus its commit.

| Item | Value |
| --- | --- |
| OS | Linux-6.12.94+-x86_64-with-glibc2.41 |
| Kernel | 6.12.94+ |
| CPU | Intel(R) Xeon(R) Processor, 8 cores |
| Python | 3.13.5 |
| bun | 1.4.2 |
| openswarm commit | `113d5514a0a78395699a39d597ab05bd7caf63c3` on `bench/head-to-head` (the bench files themselves are uncommitted working-tree changes) |
| OmO | `<omo-dir>` at `07776864a043acdda9f9976ac8f71659a25f1d86` (tag v5.1.35) |

Commands, from the repository root:

```bash
python3 bench/route_openswarm.py --out bench/results/openswarm.jsonl --latency-out bench/results/latency_openswarm.json --decisions 1000 --cli-runs 25
bun bench/route_omo.ts --omo-dir <omo-dir> --out bench/results/omo.jsonl --latency-out bench/results/latency_omo.json --decisions 1000
python3 bench/coverage.py --omo-dir <omo-dir> --out bench/results/coverage.json
python3 bench/score.py --openswarm bench/results/openswarm.jsonl --out-dir bench/results --env bench/results/environment.json --latency-openswarm bench/results/latency_openswarm.json --latency-omo bench/results/latency_omo.json --coverage bench/results/coverage.json --omo bench/results/omo.jsonl
```

## Results

560 decisions per tool (80 tasks × 7 scenarios). The headline is wasted first attempts, unrouted decisions, review independence, and the share of the nominal 5h budget one pass of the trace would use. The chart is `bench/results/chart.svg` (also `bench/results/chart.png`). Cost is computed, not billed.

Per-pass shares are the mean of the seven scenarios. Each scenario is one pass over the 80-task trace. The sum adds those seven passes. A fresh Codex-baseline pass uses 2.4937 of the nominal Codex window; the mean is lower because the two hard-Codex scenarios contribute 0.

| Tool | Wasted first | Unrouted | Review independence | Failed attempts | Metered USD | Go account USD | Go 5h share per pass | Go 5h share (sum) | Codex 5h share per pass | Codex 5h share (sum) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| openswarm | 0.0% | 0.0% | 54.8% | 0 | 0.0000 | 31.0513 | 1.4378 | 10.0651 | 0.4763 | 3.3338 |
| OmO | 23.4% | 19.8% | 71.4% | 254 | 1.5396 | 23.0340 | 1.0969 | 7.6780 | 0.8502 | 5.9512 |
| Codex default | 28.6% | 28.6% | 0.0% | 160 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 1.7812 | 12.4685 |
| strongest | 28.6% | 28.6% | 0.0% | 160 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 1.7812 | 12.4685 |
| cheapest | 0.0% | 0.0% | 0.0% | 0 | 0.0000 | 4.0443 | 0.0481 | 0.3370 | 0.0000 | 0.0000 |

### Unrouted decisions by OmO category

These counts follow from the provider set used for every tool: Codex (`chatgpt-subscription`), OpenCode Go, and DeepSeek. A chain that names only Google or Anthropic cannot resolve here. That is a property of this comparison, not of OmO in general. openswarm and the cheapest baseline left nothing unrouted.

| Tool | OmO category | Decisions | Reason |
| --- | --- | ---: | --- |
| OmO | deep-high | 4 | every reachable rung hit a usage-limit or insufficient-balance error |
| OmO | deep-low | 10 | every reachable rung hit a usage-limit or insufficient-balance error |
| OmO | ultrabrain | 16 | every reachable rung hit a usage-limit or insufficient-balance error |
| OmO | unspecified-high | 32 | every reachable rung hit a usage-limit or insufficient-balance error |
| OmO | writing | 49 | the chain names only `google`, `google-vertex`, `opencode`, `anthropic`, `anthropic-api`, and `github-copilot`, with models `gemini-4-argon`, `claude-opus-5-5`, and `claude-opus-4-6`. None of those providers are connected. `requiresAnyModel` blocks a fallthrough |
| Codex default | deep-high | 4 | the single baseline model is on an exhausted quota and the baseline has no fallback |
| Codex default | deep-low | 10 | same |
| Codex default | quick | 54 | same |
| Codex default | ultrabrain | 16 | same |
| Codex default | unspecified-high | 32 | same |
| Codex default | unspecified-low | 30 | same |
| Codex default | writing | 14 | same |
| strongest | deep-high | 4 | same, on the two hard-Codex scenarios |
| strongest | deep-low | 10 | same |
| strongest | quick | 54 | same |
| strongest | ultrabrain | 16 | same |
| strongest | unspecified-high | 32 | same |
| strongest | unspecified-low | 30 | same |
| strongest | writing | 14 | same |

The Codex-baseline rows are exactly the tasks in `codex_hard_97` and `tight_friday` (80 × 2 = 160).

### Coverage, counted from the pinned sources

| Router | Categories | Agents | Backends | Distinct providers in fallback chains | Distinct model ids |
| --- | ---: | ---: | ---: | ---: | ---: |
| openswarm | — | — | 3 (codex, ocgo, deepseek) | — | 27 in `policy.json` |
| OmO model-core | 9 | 11 | — | 26 | 36 |

OmO's 26 providers and 36 model ids are the union of the category fallback chains and the agent fallback chains in `<omo-dir>/packages/model-core` at `07776864a043acdda9f9976ac8f71659a25f1d86`. openswarm's model ids are the OpenCode Go monthly-cap keys plus `deepseek.tiers`. Codex slugs are not listed in `policy.json`.

### Wasted first attempts by scenario

| Scenario | openswarm | OmO | Codex default | strongest | cheapest |
| --- | ---: | ---: | ---: | ---: | ---: |
| fresh | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| codex_soft_90 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| codex_hard_97 | 0.0% | 52.5% | 100.0% | 100.0% | 0.0% |
| ocgo_hot | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| deepseek_floor | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| mixed_late_week | 0.0% | 20.0% | 0.0% | 0.0% | 0.0% |
| tight_friday | 0.0% | 91.2% | 100.0% | 100.0% | 0.0% |

### Review independence by scenario

Twelve review or audit tasks per scenario.

| Scenario | openswarm | OmO | Codex default | strongest | cheapest |
| --- | ---: | ---: | ---: | ---: | ---: |
| fresh | 100.0% | 100.0% | 0.0% | 0.0% | 0.0% |
| codex_soft_90 | 33.3% | 100.0% | 0.0% | 0.0% | 0.0% |
| codex_hard_97 | 33.3% | 66.7% | 0.0% | 0.0% | 0.0% |
| ocgo_hot | 100.0% | 100.0% | 0.0% | 0.0% | 0.0% |
| deepseek_floor | 100.0% | 100.0% | 0.0% | 0.0% | 0.0% |
| mixed_late_week | 16.7% | 33.3% | 0.0% | 0.0% | 0.0% |
| tight_friday | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |

### How the objective numbers move

- At 90% of the Codex window, openswarm moves difficulty ≤ 3 off Codex and lowers effort on the rest. OmO's decisions match the fresh scenario. 90% is not an error it can see, so it wastes nothing there.
- At 97%, openswarm skips Codex before the call (wasted first attempts 0). OmO's first Codex pick fails and it walks the chain: 52.5% of tasks in that scenario waste the first attempt. Across all seven scenarios that is 254 failed attempts. `ultrabrain`, `deep-high`, and `deep-low` then stay unresolved when every reachable Codex rung is exhausted.
- `writing` is unrouted for OmO in all 7 tasks × 7 scenarios. The chain names Google and Anthropic models only, and `requiresAnyModel` blocks a fallthrough. Those providers are not in this set.
- A DeepSeek balance of ¥3 changes no decision for either tool. OmO's metered total ($1.54) comes from scenarios where a Codex or Go rung has already failed and the chain reaches `deepseek-flash` or `deepseek-v4-pro`.
- Review independence is 71.4% for OmO and 54.8% for openswarm. openswarm's reviews stay independent while the reference implementer is still a GPT model. Once that implementer has spilled onto the same Go family the review uses, the rate falls (33.3% at the soft Codex limit, 0% on tight Friday).
- One pass of the trace uses 0.4763 of the nominal Codex 5h budget under openswarm and 0.8502 under OmO, averaged across the seven scenarios. The two Codex baselines average 1.7812 because five of the scenarios put the whole trace on Codex (2.4937 each) and the two hard-Codex scenarios use none. OpenCode Go's per-pass share is 1.4378 for openswarm and 1.0969 for OmO. That share can exceed 1 because it sums fractions of small per-model windows.
- On tight Friday, openswarm wastes nothing and leaves nothing unrouted, and its Go per-pass share falls to 0.0481, the same flash-only figure as the cheapest baseline. OmO wastes the first attempt on 91.2% of those tasks.

### Agreement with the difficulty→tier rubric

This section is not a quality score and it is not the headline. `min_tier` is openswarm's difficulty map. openswarm agrees with that map by construction whenever quota still allows the tier it would have picked on a fresh window. The cells are exact / over / under, and an unrouted decision counts as under.

| Scenario | openswarm | OmO | Codex default | strongest | cheapest |
| --- | --- | --- | --- | --- | --- |
| fresh | 100.0% / 0.0% / 0.0% | 62.5% / 22.5% / 15.0% | 41.2% / 40.0% / 18.8% | 18.8% / 81.2% / 0.0% | 40.0% / 0.0% / 60.0% |
| codex_soft_90 | 100.0% / 0.0% / 0.0% | 62.5% / 22.5% / 15.0% | 41.2% / 40.0% / 18.8% | 18.8% / 81.2% / 0.0% | 40.0% / 0.0% / 60.0% |
| codex_hard_97 | 100.0% / 0.0% / 0.0% | 52.5% / 20.0% / 27.5% | 0.0% / 0.0% / 100.0% | 0.0% / 0.0% / 100.0% | 40.0% / 0.0% / 60.0% |
| ocgo_hot | 100.0% / 0.0% / 0.0% | 62.5% / 22.5% / 15.0% | 41.2% / 40.0% / 18.8% | 18.8% / 81.2% / 0.0% | 40.0% / 0.0% / 60.0% |
| deepseek_floor | 100.0% / 0.0% / 0.0% | 62.5% / 22.5% / 15.0% | 41.2% / 40.0% / 18.8% | 18.8% / 81.2% / 0.0% | 40.0% / 0.0% / 60.0% |
| mixed_late_week | 96.2% / 0.0% / 3.8% | 62.5% / 2.5% / 35.0% | 41.2% / 40.0% / 18.8% | 18.8% / 81.2% / 0.0% | 40.0% / 0.0% / 60.0% |
| tight_friday | 40.0% / 0.0% / 60.0% | 33.8% / 18.8% / 47.5% | 0.0% / 0.0% / 100.0% | 0.0% / 0.0% / 100.0% | 40.0% / 0.0% / 60.0% |

Overall, same caveat: openswarm 90.9% / 0.0% / 9.1%, OmO 57.0% / 18.8% / 24.3%, Codex default 29.5% / 28.6% / 42.0%, strongest 13.4% / 58.0% / 28.6%, cheapest 40.0% / 0.0% / 60.0%.

### Latency

Latency is the only non-deterministic section.

| Measurement | n | median | p95 |
| --- | ---: | ---: | ---: |
| openswarm in-process `route()` | 1120 | 0.0075 ms | 0.0215 ms |
| openswarm `bin/openswarm route` cold start | 25 | 74.0 ms | 86.9 ms |
| OmO in-process, module already loaded | 1120 | 0.0292 ms | 0.1027 ms |

OmO has no route CLI, so there is no cold-start number for it. Both in-process medians are well under a tenth of a millisecond. The number a person would feel for openswarm is the CLI cold start.

## Limitations

- Agreement with the difficulty→tier rubric measures deviation from openswarm's difficulty map under quota pressure. It is not evidence that one router chose a better model.
- The trace is synthetic. Token counts are estimates from the rubric, not tokenizer output and not a bill.
- The tier of a model is this bench's label. `gpt-6.1-sol` is the balanced workhorse in openswarm's keyword scheme. OmO's `deep-low` lane uses that same model, so a difficulty-5 task mapped to `deep-low` can count as under-provisioned even though OmO considers the lane a deep default.
- `deepseek-v4-pro` is scored as deep, because that is the stronger of the two policy slots that share the id. A balanced task that falls through to it counts as over-provisioned.
- MiniMax tiers are judgments. Those models are excluded before openswarm's price tercile.
- Codex window share uses a nominal 2,000,000-token budget. It is not an OpenAI quota.
- OpenCode Go "prices" are subscription accounting rates from the demo state, not a second invoice.
- OmO's category chains prefer several providers this subscription set does not include. That is intentional: the bench holds the subscriptions fixed. It also means some categories, notably `writing`, do not resolve here.
- Soft quota, hard quota, and a usage-limit error are different events. The bench keeps them different on purpose.
- Burn pace and the expiring-quota effort bump are part of openswarm and are not in these scenarios.
- OmO latency does not include a process cold start. openswarm's CLI number does.
- Author bias: the task list, the category map, and the tier labels were chosen by the openswarm maintainer. Raw decisions are in the JSONL files.
