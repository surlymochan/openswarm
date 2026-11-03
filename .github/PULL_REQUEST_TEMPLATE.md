## What changed

<!-- One change per PR. Say what this does and why, in a few sentences. -->

Closes #

## How it was verified

<!-- Paste the actual commands and results, not just "tests pass". -->

```bash
python3 -m unittest discover -s tests
# Ran N tests in Xs — OK / FAILED (details below)
```

- [ ] Full test suite run locally (stdlib only, no backends needed)
- [ ] New or changed behaviour has a test (routing changes: a scenario in `tests/test_route.py`; workspace / verify / watch / risk-rule changes: `tests/test_workspace.py`)
- [ ] Tests do not depend on my machine: no real `~/.codex`, `~/.claude`, OpenCode DB, Keychain, or network (fixtures and temp dirs only)
- [ ] Offline demo still works, if CLI behaviour changed: `OPENSWARM_STATE_FILE=examples/demo-state.json bin/openswarm status` and `bin/openswarm route --type review --difficulty 3`

## Constraints

- [ ] Still single-file, standard-library-only — no third-party runtime dependencies
- [ ] No code that reads another tool's credentials by default; any new local data source is behind a `local_sources.*` switch in `policy.json` and listed in the README table
- [ ] `SKILL.md` updated if behaviour or flags changed
- [ ] No runtime artefacts committed (`runs/`, `state.json`, `ledger.jsonl`, worktrees)

## Notes for reviewers

<!-- Anything risky, deliberately out of scope, or worth a second look. -->
