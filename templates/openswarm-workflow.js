export const meta = {
  name: 'openswarm-dispatch',
  description: 'Fan out planned tasks to Codex / OpenCode Go / DeepSeek via openswarm, watch with budgets, review diffs',
  whenToUse: 'After Claude has split work into independent tasks with acceptance criteria. args = {repo, openswarm, maxParallel, tasks:[{id,title,prompt,type,difficulty,budgetMinutes?,backend?}], review: "light"|"full"|"delegate"}',
  phases: [
    { title: 'Dispatch', detail: 'thin haiku dispatchers run openswarm (Codex etc. do the coding)', model: 'haiku' },
    { title: 'Review', detail: 'Claude checks each diff against its acceptance criteria' },
  ],
}

// ---- args ----------------------------------------------------------------
const A = args || {}
const XS = A.openswarm || '~/.claude/skills/openswarm/bin/openswarm'
const REPO = A.repo
const TASKS = A.tasks || []
const MAX = Math.max(1, A.maxParallel || 3)   // take from `openswarm status` concurrency recommendation
const REVIEW = A.review || 'light'            // from route().claude_review

if (!REPO || !TASKS.length) throw new Error('args.repo and args.tasks are required')

// Simple semaphore so we never exceed the quota-derived concurrency.
let active = 0
const waiters = []
async function slot(fn) {
  while (active >= MAX) await new Promise(r => waiters.push(r))
  active++
  try { return await fn() } finally { active--; const w = waiters.shift(); if (w) w() }
}

const RUN_SCHEMA = {
  type: 'object',
  required: ['run_id', 'status', 'backend', 'model', 'branch', 'commit', 'summary', 'tests', 'events'],
  properties: {
    run_id: { type: 'string' },
    status: { type: 'string' },
    backend: { type: 'string' },
    model: { type: 'string' },
    branch: { type: ['string', 'null'] },
    commit: { type: ['string', 'null'] },
    summary: { type: 'string' },
    tests: { type: 'string' },
    events: { type: 'array', items: { type: 'string' }, description: 'RISK/STALL/BUDGET/ERROR lines seen while watching' },
  },
}

const REVIEW_SCHEMA = {
  type: 'object',
  required: ['verdict', 'issues'],
  properties: {
    verdict: { type: 'string', enum: ['accept', 'fix', 'reject'] },
    issues: { type: 'array', items: { type: 'string' } },
  },
}

function dispatchPrompt(t) {
  const flags = [
    `--cwd ${JSON.stringify(REPO)}`,
    `--type ${t.type || 'impl'}`,
    `--difficulty ${t.difficulty || 3}`,
    `--budget-minutes ${t.budgetMinutes || 30}`,
    `--title ${JSON.stringify(t.title)}`,
    t.backend ? `--backend ${t.backend}` : '',
    t.model ? `--model ${t.model}` : '',
  ].filter(Boolean).join(' ')
  return `You are a thin dispatcher. Do NOT edit code yourself. Use Bash only.

1. Write the task text below verbatim to a temp file, then run:
   ${XS} run --detach ${flags} --task-file <that file>
   Note the "id" in its JSON output.
2. Loop: run \`${XS} watch <id> --from-end --max-minutes 8\` (Bash timeout 560000 ms).
   Collect any lines tagged RISK / STALL / BUDGET / ERROR / EXITED.
   If the last line is WATCH-EXPIRED, run the watch again. Stop when you see DONE or EXITED.
   If a RISK line shows something destructive outside the worktree (push, rm -rf of home paths, secrets), run \`${XS} stop <id>\` immediately.
3. Read ~/.claude/openswarm/runs/<id>/result.json and return run_id, status, backend, model, branch, commit,
   summary (result.summary), tests (result.tests), and the collected event lines.

TASK:
${t.prompt}`
}

function reviewPrompt(t, r) {
  const depth = REVIEW === 'full'
    ? 'Read the full patch and the touched files; run the relevant tests in a scratch checkout of the branch if cheap.'
    : 'Read only the diffstat and the patch hunks (git show), not whole files. Do not run tests unless the executor reported none.'
  return `Review executor output for task "${t.title}" in repo ${REPO}.
Branch ${r.branch}, commit ${r.commit}. Executor: ${r.backend}/${r.model}. Executor says: ${r.summary}. Tests: ${r.tests}.
Acceptance criteria / task:
${t.prompt}

${depth}
Use \`git -C ${JSON.stringify(REPO)} show --stat ${r.commit}\` and \`git -C ${JSON.stringify(REPO)} show ${r.commit}\`.
Verdict: accept (meets criteria), fix (small issues — list them precisely), reject (wrong approach). Do not modify the repo.`
}

// ---- pipeline: dispatch -> review, no barrier --------------------------------
const results = await pipeline(
  TASKS,
  t => slot(() => agent(dispatchPrompt(t), {
    label: `dispatch:${t.id || t.title}`, phase: 'Dispatch', model: 'haiku', effort: 'low', schema: RUN_SCHEMA,
  })),
  (r, t) => {
    if (!r || !r.commit || r.status !== 'done') return { task: t, run: r, review: null }
    if (REVIEW === 'delegate') return { task: t, run: r, review: { verdict: 'accept', issues: ['claude review delegated (quota tight) — spot-check later'] } }
    return agent(reviewPrompt(t, r), { label: `review:${t.id || t.title}`, phase: 'Review', schema: REVIEW_SCHEMA })
      .then(v => ({ task: t, run: r, review: v }))
  },
)

const done = results.filter(Boolean)
log(`${done.filter(x => x.review && x.review.verdict === 'accept').length}/${TASKS.length} accepted`)
return done.map(x => ({
  id: x.task.id || x.task.title,
  status: x.run ? x.run.status : 'dispatch-failed',
  backend: x.run && `${x.run.backend}/${x.run.model}`,
  branch: x.run && x.run.branch,
  commit: x.run && x.run.commit,
  verdict: x.review ? x.review.verdict : null,
  issues: x.review ? x.review.issues : [],
  alerts: x.run ? x.run.events : [],
}))
