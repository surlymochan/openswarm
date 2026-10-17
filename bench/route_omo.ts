/**
 * oh-my-openagent side of the routing benchmark.
 *
 * Imports model-core from the pinned checkout (--omo-dir) and resolves each
 * delegated task category with resolveModelPipeline. Quota exhaustion is not
 * known up front: a pick on an exhausted provider is one failed attempt, the
 * usage-limit error is passed through the model-core classifier, and the next
 * rung comes from getNextFallback, skipping unreachable and no-op entries the
 * way the background-agent retry loop does.
 *
 * There is no standalone `route` CLI in model-core. Latency is in-process.
 */
import { spawnSync } from "node:child_process"
import { readFileSync, writeFileSync, mkdirSync } from "node:fs"
import { dirname, join } from "node:path"

const HARD = 0.95
const FLOOR = 10
const USAGE_LIMIT = 1

type ModelSpec = {
  id: string
  tier?: string
  omo_providers?: string[]
}

type Catalogue = {
  connected_providers: string[]
  models: ModelSpec[]
}

type Scenario = {
  id: string
  codex: { five_hour: number; seven_day: number }
  ocgo_usage: Record<string, { "5h"?: number; "7d"?: number; "30d"?: number }>
  deepseek_balance_cny: number
}

type Task = {
  id: string
  omo_category: string
}

type Attempt = {
  provider: string
  model: string
  variant?: string
  failed: boolean
  reason: string
}

type Core = {
  CATEGORY_MODEL_REQUIREMENTS: Record<string, {
    requiresAnyModel?: boolean
    fallbackChain: { providers: string[]; model: string; variant?: string }[]
  }>
  resolveModelPipeline: (request: unknown, cache: unknown) => {
    model: string
    provenance: string
    variant?: string
    attempted?: string[]
  } | undefined
  fuzzyMatchModel: (target: string, available: Set<string>, providers?: string[]) => string | null
  transformModelForProvider: (provider: string, model: string) => string
  shouldRetryError: (error: { name?: string; message?: string; statusCode?: number }) => boolean
  classifyRuntimeFallbackError: (error: unknown) => string | undefined
  getNextFallback: (chain: FallbackEntry[], attemptCount: number) => FallbackEntry | undefined
  hasMoreFallbacks: (chain: FallbackEntry[], attemptCount: number) => boolean
}

type FallbackEntry = { providers: string[]; model: string; variant?: string }

function arg(name: string, fallback: string): string {
  const i = process.argv.indexOf(name)
  if (i >= 0 && process.argv[i + 1]) return process.argv[i + 1]
  return fallback
}

function readJson(path: string): unknown {
  return JSON.parse(readFileSync(path, "utf8"))
}

function readJsonl(path: string): unknown[] {
  return readFileSync(path, "utf8")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => JSON.parse(line))
}

function percentile(samples: number[], p: number): number {
  const xs = [...samples].sort((a, b) => a - b)
  const k = (xs.length - 1) * p
  const lo = Math.floor(k)
  const hi = Math.min(lo + 1, xs.length - 1)
  return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)
}

function splitModel(full: string): { provider: string; model: string } {
  const i = full.indexOf("/")
  if (i <= 0) return { provider: "", model: full }
  return { provider: full.slice(0, i), model: full.slice(i + 1) }
}

function canon(model: string): string {
  return model.toLowerCase().replace(/\./g, "-")
}

function samePick(
  a: { provider: string; model: string },
  b: { provider: string; model: string },
): boolean {
  return a.provider.toLowerCase() === b.provider.toLowerCase() && canon(a.model) === canon(b.model)
}

function availableModels(catalogue: Catalogue): Set<string> {
  const allow = new Set(catalogue.connected_providers)
  const out = new Set<string>()
  for (const model of catalogue.models) {
    for (const provider of model.omo_providers ?? []) {
      if (allow.has(provider)) out.add(`${provider}/${model.id}`)
    }
  }
  return out
}

function worstCodex(scenario: Scenario): number {
  return Math.max(scenario.codex.five_hour, scenario.codex.seven_day)
}

function ocgoMax(scenario: Scenario, model: string): number {
  const usage = scenario.ocgo_usage?.[model]
  if (!usage) return 0
  return Math.max(usage["5h"] ?? 0, usage["7d"] ?? 0, usage["30d"] ?? 0)
}

function exhaustionError(
  provider: string,
  model: string,
  scenario: Scenario,
): { name: string; message: string; statusCode: number } | null {
  if ((provider === "chatgpt-subscription" || provider === "openai") && worstCodex(scenario) >= HARD) {
    return { name: "RateLimitError", message: "usage limit exceeded", statusCode: 429 }
  }
  if (provider === "opencode-go" && ocgoMax(scenario, model) >= USAGE_LIMIT) {
    return { name: "RateLimitError", message: "usage limit exceeded", statusCode: 429 }
  }
  if (provider === "deepseek" && scenario.deepseek_balance_cny < FLOOR) {
    return { name: "BillingError", message: "insufficient balance", statusCode: 402 }
  }
  return null
}

function resolveEntry(
  core: Core,
  entry: FallbackEntry,
  available: Set<string>,
): { provider: string; model: string; full: string; variant?: string } | null {
  for (const provider of entry.providers) {
    const entryModelID = entry.model.startsWith(`${provider}/`)
      ? entry.model.slice(provider.length + 1)
      : entry.model
    const transformed = core.transformModelForProvider(provider, entryModelID)
    const candidates = transformed === entryModelID ? [entryModelID] : [transformed, entryModelID]
    for (const modelID of candidates) {
      const match = core.fuzzyMatchModel(`${provider}/${modelID}`, available, [provider])
      if (match) {
        const parts = splitModel(match)
        return { provider: parts.provider, model: parts.model, full: match, variant: entry.variant }
      }
    }
  }
  return null
}

function chainHasMatch(core: Core, chain: FallbackEntry[], available: Set<string>): boolean {
  return chain.some((entry) => resolveEntry(core, entry, available) !== null)
}

function retryableQuota(core: Core, error: { name: string; message: string; statusCode: number }): boolean {
  return core.classifyRuntimeFallbackError(error) === "quota_exceeded" && core.shouldRetryError(error)
}

function routeOne(
  core: Core,
  task: Task,
  scenario: Scenario,
  available: Set<string>,
  connected: string[],
  tiers: Map<string, string>,
): Record<string, unknown> {
  const requirement = core.CATEGORY_MODEL_REQUIREMENTS[task.omo_category]
  const reasons: string[] = []
  const base = {
    task_id: task.id,
    scenario: scenario.id,
    tool: "omo",
    backend: null as string | null,
    provider: null as string | null,
    model: null as string | null,
    effort: null as string | null,
    variant: null as string | null,
    tier: null as string | null,
    attempts: 0,
    failed_attempts: 0,
    reasons,
  }
  if (!requirement) {
    reasons.push(`unknown category ${task.omo_category}`)
    return base
  }
  const chain = requirement.fallbackChain
  if (requirement.requiresAnyModel && !chainHasMatch(core, chain, available)) {
    reasons.push(
      `${task.omo_category} requiresAnyModel and none of its fallback models are in the connected set`,
    )
    return base
  }

  const categoryDefault = `${chain[0].providers[0]}/${chain[0].model}`
  const cache = {
    readConnectedProvidersCache: () => connected,
    findProviderModelMetadata: () => undefined,
  }
  const resolved = core.resolveModelPipeline({
    intent: { categoryDefaultModel: categoryDefault },
    constraints: { availableModels: available, connectedProviders: connected },
    policy: { fallbackChain: chain },
  }, cache)

  if (!resolved || !resolved.model.includes("/")) {
    reasons.push(`${task.omo_category} resolved no model (no system default is configured)`)
    return base
  }

  const first = splitModel(resolved.model)
  let current = { provider: first.provider, model: first.model, variant: resolved.variant }
  reasons.push(`initial ${resolved.provenance} -> ${resolved.model}${resolved.variant ? " variant " + resolved.variant : ""}`)

  const attempts: Attempt[] = []
  const initialError = exhaustionError(current.provider, current.model, scenario)
  if (!initialError) {
    attempts.push({ ...current, failed: false, reason: "available" })
  } else if (!retryableQuota(core, initialError)) {
    attempts.push({ ...current, failed: true, reason: `classifier rejected: ${initialError.message}` })
    reasons.push(`first pick ${current.provider}/${current.model} errored but the classifier did not allow fallback`)
    return finish(base, attempts, tiers, reasons)
  } else {
    attempts.push({ ...current, failed: true, reason: initialError.message })
    reasons.push(
      `first pick ${current.provider}/${current.model} failed (${initialError.message}); classifier=quota_exceeded`,
    )
    let attemptCount = 0
    while (core.hasMoreFallbacks(chain, attemptCount)) {
      const entry = core.getNextFallback(chain, attemptCount)
      attemptCount += 1
      if (!entry) break
      const next = resolveEntry(core, entry, available)
      if (!next) continue
      if (samePick(next, current)) continue
      const error = exhaustionError(next.provider, next.model, scenario)
      if (error) {
        if (!retryableQuota(core, error)) {
          attempts.push({ provider: next.provider, model: next.model, variant: next.variant, failed: true, reason: `classifier rejected: ${error.message}` })
          reasons.push(`stopped at ${next.provider}/${next.model}: classifier rejected fallback`)
          break
        }
        attempts.push({ provider: next.provider, model: next.model, variant: next.variant, failed: true, reason: error.message })
        reasons.push(`fallback ${next.provider}/${next.model} failed (${error.message}); classifier=quota_exceeded`)
        current = { provider: next.provider, model: next.model, variant: next.variant }
        continue
      }
      attempts.push({ provider: next.provider, model: next.model, variant: next.variant, failed: false, reason: "next fallback rung" })
      reasons.push(`fallback selected ${next.provider}/${next.model}${next.variant ? " variant " + next.variant : ""}`)
      break
    }
  }
  return finish(base, attempts, tiers, reasons)
}

function finish(
  base: Record<string, unknown>,
  attempts: Attempt[],
  tiers: Map<string, string>,
  reasons: string[],
): Record<string, unknown> {
  const success = [...attempts].reverse().find((item) => !item.failed)
  const failed = attempts.filter((item) => item.failed).length
  if (success) {
    base.backend = success.provider
    base.provider = success.provider
    base.model = success.model
    base.variant = success.variant ?? null
    base.effort = success.variant ?? null
    base.tier = tiers.get(success.model) ?? null
  } else if (attempts.length > 0) {
    reasons.push(`no reachable model after ${failed} failed attempt(s)`)
  }
  base.attempts = attempts.length
  base.failed_attempts = failed
  base.reasons = reasons
  return base
}

async function main(): Promise<void> {
  const bench = dirname(new URL(import.meta.url).pathname)
  const omoDir = arg("--omo-dir", "")
  if (!omoDir) {
    console.error("OmO side skipped: pass --omo-dir pointing at the pinned oh-my-openagent checkout")
    process.exit(2)
  }
  const out = arg("--out", join(bench, "results", "omo.jsonl"))
  const latencyOut = arg("--latency-out", join(bench, "results", "latency_omo.json"))
  const want = Number(arg("--decisions", "1000"))
  const catalogue = readJson(join(bench, "catalogue.json")) as Catalogue
  const scenarios = readJson(join(bench, "scenarios.json")) as { scenarios: Scenario[] }
  const trace = readJsonl(join(bench, "trace.jsonl")) as Task[]
  const core = await import(join(omoDir, "packages/model-core/src/index.ts")) as Core
  const commit = spawnSync("git", ["-C", omoDir, "rev-parse", "HEAD"], { encoding: "utf8" })
  const available = availableModels(catalogue)
  const tiers = new Map(catalogue.models.map((model) => [model.id, model.tier ?? ""]))
  const connected = catalogue.connected_providers

  const samples: number[] = []
  let rows: Record<string, unknown>[] = []
  let guard = 0
  while (samples.length < want && guard < 20) {
    const pass: Record<string, unknown>[] = []
    for (const scenario of scenarios.scenarios) {
      for (const task of trace) {
        const t0 = performance.now()
        const row = routeOne(core, task, scenario, available, connected, tiers)
        samples.push(performance.now() - t0)
        pass.push(row)
      }
    }
    if (rows.length === 0) rows = pass
    guard += 1
  }

  mkdirSync(dirname(out), { recursive: true })
  const stable = (row: Record<string, unknown>) => JSON.stringify(row, Object.keys(row).sort())
  writeFileSync(out, rows.map((row) => stable(row) + "\n").join(""))
  const latency = {
    in_process: {
      kind: "in_process",
      unit: "ms",
      n: samples.length,
      median: Number(percentile(samples, 0.5).toFixed(4)),
      p95: Number(percentile(samples, 0.95).toFixed(4)),
      min: Number(Math.min(...samples).toFixed(4)),
      max: Number(Math.max(...samples).toFixed(4)),
    },
    cli_cold_start: null,
    note: "model-core has no standalone route CLI. The figure is in-process time around resolveModelPipeline plus the exhaustion walk, after bun has loaded the module. It is not comparable to openswarm's process cold start.",
    omo_commit: (commit.stdout || "").trim(),
    omo_dir: "<omo-dir>",
  }
  writeFileSync(latencyOut, JSON.stringify(latency, null, 2) + "\n")
  console.log(`omo decisions ${rows.length} -> ${out}`)
  console.log(`omo commit ${latency.omo_commit}`)
  console.log(`in-process n=${latency.in_process.n} median=${latency.in_process.median}ms p95=${latency.in_process.p95}ms`)
}

main().catch((error: unknown) => {
  console.error(error)
  process.exit(1)
})
