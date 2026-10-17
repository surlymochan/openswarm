# Routing benchmark results

All dollar amounts and window shares are computed from `bench/prices.json` times the trace's estimated tokens. They are not measured invoices.

A wasted first attempt is a decision whose first try targeted a quota the tool only learns is exhausted by a usage-limit (or insufficient-balance) error. openswarm skips those backends before the call, so its wasted-attempt count stays at zero by construction.

Subscription shares are reported two ways. The sum adds every scenario. The per-pass figure is the mean of the per-scenario shares, and each scenario is one pass over the 80-task trace. Read the per-pass number as the share of the nominal 5h budget one pass of the trace would use. OpenCode Go's 5h share can exceed 1 because it sums per-model fractions of small windows.

The chart is `bench/results/chart.svg`. It plots the objective rates and the Codex per-pass share only.

## Objective metrics

| Tool | Wasted first | Unrouted | Review independence | Failed attempts | Metered USD | Go account USD | Go 5h share per pass | Go 5h share (sum) | Codex 5h share per pass | Codex 5h share (sum) | Codex tokens (sum) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| openswarm | 0.0% | 0.0% | 54.8% | 0 | 0.0000 | 31.0513 | 1.4378 | 10.0651 | 0.4763 | 3.3338 | 6667600 |
| OmO | 23.4% | 19.8% | 71.4% | 254 | 1.5396 | 23.0340 | 1.0969 | 7.6780 | 0.8502 | 5.9512 | 11902500 |
| Codex default | 28.6% | 28.6% | 0.0% | 160 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 1.7812 | 12.4685 | 24937000 |
| strongest | 28.6% | 28.6% | 0.0% | 160 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 1.7812 | 12.4685 | 24937000 |
| cheapest | 0.0% | 0.0% | 0.0% | 0 | 0.0000 | 4.0443 | 0.0481 | 0.3370 | 0.0000 | 0.0000 | 0 |

## Unrouted decisions by OmO category

Unrouted counts below are a consequence of the provider set used for every tool in this bench: Codex (`chatgpt-subscription`), OpenCode Go, and DeepSeek. Providers that a chain names but this set does not connect — including Google and Anthropic — cannot be selected. That is a property of this comparison, not of OmO in general.

| Tool | OmO category | Decisions | Reason |
| --- | --- | ---: | --- |
| OmO | deep-high | 4 | every reachable rung in the category chain hit a usage-limit or insufficient-balance error |
| OmO | deep-low | 10 | every reachable rung in the category chain hit a usage-limit or insufficient-balance error |
| OmO | ultrabrain | 16 | every reachable rung in the category chain hit a usage-limit or insufficient-balance error |
| OmO | unspecified-high | 32 | every reachable rung in the category chain hit a usage-limit or insufficient-balance error |
| OmO | writing | 49 | no model on this category's own chain is served by the connected providers (Codex, OpenCode Go, DeepSeek). Chain providers: anthropic, anthropic-api, github-copilot, google, google-vertex, opencode. Chain models: claude-opus-4-6, claude-opus-5-5, gemini-4-argon |
| Codex default | deep-high | 4 | the baseline's single model is on an exhausted quota and the baseline has no fallback |
| Codex default | deep-low | 10 | the baseline's single model is on an exhausted quota and the baseline has no fallback |
| Codex default | quick | 54 | the baseline's single model is on an exhausted quota and the baseline has no fallback |
| Codex default | ultrabrain | 16 | the baseline's single model is on an exhausted quota and the baseline has no fallback |
| Codex default | unspecified-high | 32 | the baseline's single model is on an exhausted quota and the baseline has no fallback |
| Codex default | unspecified-low | 30 | the baseline's single model is on an exhausted quota and the baseline has no fallback |
| Codex default | writing | 14 | the baseline's single model is on an exhausted quota and the baseline has no fallback |
| strongest | deep-high | 4 | the baseline's single model is on an exhausted quota and the baseline has no fallback |
| strongest | deep-low | 10 | the baseline's single model is on an exhausted quota and the baseline has no fallback |
| strongest | quick | 54 | the baseline's single model is on an exhausted quota and the baseline has no fallback |
| strongest | ultrabrain | 16 | the baseline's single model is on an exhausted quota and the baseline has no fallback |
| strongest | unspecified-high | 32 | the baseline's single model is on an exhausted quota and the baseline has no fallback |
| strongest | unspecified-low | 30 | the baseline's single model is on an exhausted quota and the baseline has no fallback |
| strongest | writing | 14 | the baseline's single model is on an exhausted quota and the baseline has no fallback |

## Coverage, counted from the pinned sources

These counts are read from the source trees at the commits in the environment section. They are not a quality score. OmO's chains name many more providers and model ids than this bench connects.

| Router | Categories | Agents | Backends | Distinct providers in fallback chains | Distinct model ids |
| --- | ---: | ---: | ---: | ---: | ---: |
| openswarm | — | — | 3 (codex, ocgo, deepseek) | — | 27 in policy.json |
| OmO model-core | 9 | 11 | — | 26 | 36 |

openswarm's backends are `routing.default_order` in `policy.json`. Its model ids are the OpenCode Go monthly-cap keys plus `deepseek.tiers` values. Codex slugs are not listed in `policy.json`, so they are not in that count.

OmO's providers and model ids are the union of the category fallback chains (9 categories) and the agent fallback chains (11 agents) in `<omo-dir>/packages/model-core` at `07776864a043acdda9f9976ac8f71659a25f1d86`.

## Wasted first attempts by scenario

| Scenario | openswarm | OmO | Codex default | strongest | cheapest |
| --- | ---: | ---: | ---: | ---: | ---: |
| fresh | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| codex_soft_90 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| codex_hard_97 | 0.0% | 52.5% | 100.0% | 100.0% | 0.0% |
| ocgo_hot | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| deepseek_floor | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| mixed_late_week | 0.0% | 20.0% | 0.0% | 0.0% | 0.0% |
| tight_friday | 0.0% | 91.2% | 100.0% | 100.0% | 0.0% |

## Subscription share of one trace pass

Each cell is that scenario alone: one pass over the 80-task trace, as a share of the nominal 5h budget.

Codex nominal 5h share:

| Scenario | openswarm | OmO | Codex default | strongest | cheapest |
| --- | ---: | ---: | ---: | ---: | ---: |
| fresh | 0.7526 | 1.1903 | 2.4937 | 2.4937 | 0.0000 |
| codex_soft_90 | 0.5380 | 1.1903 | 2.4937 | 2.4937 | 0.0000 |
| codex_hard_97 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| ocgo_hot | 0.7526 | 1.1903 | 2.4937 | 2.4937 | 0.0000 |
| deepseek_floor | 0.7526 | 1.1903 | 2.4937 | 2.4937 | 0.0000 |
| mixed_late_week | 0.5380 | 1.1903 | 2.4937 | 2.4937 | 0.0000 |
| tight_friday | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |

OpenCode Go 5h share:

| Scenario | openswarm | OmO | Codex default | strongest | cheapest |
| --- | ---: | ---: | ---: | ---: | ---: |
| fresh | 1.4274 | 1.3370 | 0.0000 | 0.0000 | 0.0481 |
| codex_soft_90 | 1.6662 | 1.3370 | 0.0000 | 0.0000 | 0.0481 |
| codex_hard_97 | 2.4022 | 1.3370 | 0.0000 | 0.0000 | 0.0481 |
| ocgo_hot | 1.4274 | 1.3370 | 0.0000 | 0.0000 | 0.0481 |
| deepseek_floor | 1.4274 | 1.3370 | 0.0000 | 0.0000 | 0.0481 |
| mixed_late_week | 1.6662 | 0.9930 | 0.0000 | 0.0000 | 0.0481 |
| tight_friday | 0.0481 | 0.0000 | 0.0000 | 0.0000 | 0.0481 |

## Review independence by scenario

| Scenario | openswarm | OmO | Codex default | strongest | cheapest |
| --- | ---: | ---: | ---: | ---: | ---: |
| fresh | 100.0% | 100.0% | 0.0% | 0.0% | 0.0% |
| codex_soft_90 | 33.3% | 100.0% | 0.0% | 0.0% | 0.0% |
| codex_hard_97 | 33.3% | 66.7% | 0.0% | 0.0% | 0.0% |
| ocgo_hot | 100.0% | 100.0% | 0.0% | 0.0% | 0.0% |
| deepseek_floor | 100.0% | 100.0% | 0.0% | 0.0% | 0.0% |
| mixed_late_week | 16.7% | 33.3% | 0.0% | 0.0% | 0.0% |
| tight_friday | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |

## Agreement with the difficulty→tier rubric

This section is not a quality score and it is not the headline.

`min_tier` comes from openswarm's own difficulty map (difficulty 1–2 fast, 3–4 balanced, 5 deep, architecture deep). openswarm agrees with that map by construction whenever quota still allows the tier it would have picked on a fresh window. The rows below show how each router deviates from the rubric when a window is under pressure.

An unrouted decision is counted in the under column here. The unrouted rate itself is in the objective table.

| Scenario | openswarm | OmO | Codex default | strongest | cheapest |
| --- | --- | --- | --- | --- | --- |
| fresh | 100.0% / 0.0% / 0.0% | 62.5% / 22.5% / 15.0% | 41.2% / 40.0% / 18.8% | 18.8% / 81.2% / 0.0% | 40.0% / 0.0% / 60.0% |
| codex_soft_90 | 100.0% / 0.0% / 0.0% | 62.5% / 22.5% / 15.0% | 41.2% / 40.0% / 18.8% | 18.8% / 81.2% / 0.0% | 40.0% / 0.0% / 60.0% |
| codex_hard_97 | 100.0% / 0.0% / 0.0% | 52.5% / 20.0% / 27.5% | 0.0% / 0.0% / 100.0% | 0.0% / 0.0% / 100.0% | 40.0% / 0.0% / 60.0% |
| ocgo_hot | 100.0% / 0.0% / 0.0% | 62.5% / 22.5% / 15.0% | 41.2% / 40.0% / 18.8% | 18.8% / 81.2% / 0.0% | 40.0% / 0.0% / 60.0% |
| deepseek_floor | 100.0% / 0.0% / 0.0% | 62.5% / 22.5% / 15.0% | 41.2% / 40.0% / 18.8% | 18.8% / 81.2% / 0.0% | 40.0% / 0.0% / 60.0% |
| mixed_late_week | 96.2% / 0.0% / 3.8% | 62.5% / 2.5% / 35.0% | 41.2% / 40.0% / 18.8% | 18.8% / 81.2% / 0.0% | 40.0% / 0.0% / 60.0% |
| tight_friday | 40.0% / 0.0% / 60.0% | 33.8% / 18.8% / 47.5% | 0.0% / 0.0% / 100.0% | 0.0% / 0.0% / 100.0% | 40.0% / 0.0% / 60.0% |

Overall agreement (exact / over / under), same caveat:

- openswarm: 90.9% / 0.0% / 9.1%
- OmO: 57.0% / 18.8% / 24.3%
- Codex default: 29.5% / 28.6% / 42.0%
- strongest: 13.4% / 58.0% / 28.6%
- cheapest: 40.0% / 0.0% / 60.0%

## Latency

Latency is the only non-deterministic section. Decision files do not include it.

- openswarm in-process: n=1120 median=0.0075 ms p95=0.0215 ms
- openswarm CLI cold start: n=25 median=73.9972 ms p95=86.8789 ms
- OmO in-process: n=1120 median=0.0292 ms p95=0.1027 ms
- OmO: no standalone route CLI. model-core has no standalone route CLI. The figure is in-process time around resolveModelPipeline plus the exhaustion walk, after bun has loaded the module. It is not comparable to openswarm's process cold start.

## Environment

- date: 2026-10-17T11:32:00Z
- os: Linux-6.12.94+-x86_64-with-glibc2.41
- kernel: 6.12.94+
- cpu: Intel(R) Xeon(R) Processor
- cores: 8
- python: 3.13.5
- bun: 1.4.2
- openswarm_commit: 113d5514a0a78395699a39d597ab05bd7caf63c3
- omo_dir: `<omo-dir>`
- omo_commit: 07776864a043acdda9f9976ac8f71659a25f1d86
- commands:
  - `python3 bench/route_openswarm.py --out bench/results/openswarm.jsonl --latency-out bench/results/latency_openswarm.json --decisions 1000 --cli-runs 25`
  - `bun bench/route_omo.ts --omo-dir <omo-dir> --out bench/results/omo.jsonl --latency-out bench/results/latency_omo.json --decisions 1000`
  - `python3 bench/coverage.py --omo-dir <omo-dir> --out bench/results/coverage.json`
  - `python3 bench/score.py --openswarm bench/results/openswarm.jsonl --out-dir bench/results --env bench/results/environment.json --latency-openswarm bench/results/latency_openswarm.json --latency-omo bench/results/latency_omo.json --coverage bench/results/coverage.json --omo bench/results/omo.jsonl`
