/**
 * Count providers and model ids named on OmO model-core fallback chains.
 * Reads the pinned checkout via --omo-dir. Writes JSON to stdout.
 */
import { join } from "node:path"

function arg(name: string): string {
  const i = process.argv.indexOf(name)
  return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : ""
}

type Entry = { providers: string[]; model: string }
type Req = { fallbackChain: Entry[] }

const omoDir = arg("--omo-dir")
if (!omoDir) {
  console.error("coverage.ts: --omo-dir is required")
  process.exit(2)
}

const core = await import(join(omoDir, "packages/model-core/src/index.ts")) as {
  CATEGORY_MODEL_REQUIREMENTS: Record<string, Req>
  AGENT_MODEL_REQUIREMENTS: Record<string, Req>
}

function collect(table: Record<string, Req>) {
  const providers = new Set<string>()
  const models = new Set<string>()
  const per: Record<string, { providers: string[]; models: string[] }> = {}
  for (const name of Object.keys(table).sort()) {
    const pset = new Set<string>()
    const mset = new Set<string>()
    for (const entry of table[name].fallbackChain) {
      for (const provider of entry.providers) {
        providers.add(provider)
        pset.add(provider)
      }
      models.add(entry.model)
      mset.add(entry.model)
    }
    per[name] = { providers: [...pset].sort(), models: [...mset].sort() }
  }
  return { providers, models, per }
}

const categories = collect(core.CATEGORY_MODEL_REQUIREMENTS)
const agents = collect(core.AGENT_MODEL_REQUIREMENTS)
const providers = new Set([...categories.providers, ...agents.providers])
const models = new Set([...categories.models, ...agents.models])

const out = {
  categories: Object.keys(core.CATEGORY_MODEL_REQUIREMENTS).length,
  category_names: Object.keys(core.CATEGORY_MODEL_REQUIREMENTS).sort(),
  agents: Object.keys(core.AGENT_MODEL_REQUIREMENTS).length,
  agent_names: Object.keys(core.AGENT_MODEL_REQUIREMENTS).sort(),
  distinct_providers: providers.size,
  providers: [...providers].sort(),
  distinct_model_ids: models.size,
  model_ids: [...models].sort(),
  category_chains: categories.per,
}
console.log(JSON.stringify(out))
