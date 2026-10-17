# Contributing to openswarm

Thanks for helping. openswarm is a single-file, standard-library-only Python CLI plus a Claude Code skill
(`SKILL.md`). Please keep it that way: no third-party runtime dependencies.

## Dev setup

```bash
git clone https://github.com/surlymochan/openswarm.git
cd openswarm
python3 -m unittest discover -s tests -v     # Python 3.9+; no Codex/OpenCode/network needed
```

Try the CLI without any backend installed:

```bash
export OPENSWARM_HOME="$(mktemp -d)" OPENSWARM_STATE_FILE=examples/demo-state.json
bin/openswarm status
bin/openswarm route --type review --difficulty 3
```

## What a good PR looks like

- **One change per PR**, with a test. Routing changes need a scenario in `tests/test_route.py`; workspace,
  verify, watch or risk-rule changes go in `tests/test_workspace.py`.
- Tests must not depend on your machine: no real `~/.codex`, `~/.claude`, OpenCode DB, Keychain or network.
  Use `tests/fixtures/state.json` and temp dirs (the test loader already isolates `OPENSWARM_HOME`).
- Keep `SKILL.md` in sync when behaviour or flags change. It is what the planning agent reads.
- Never add code that reads another tool's credentials by default. New local data sources go behind a
  `local_sources.*` switch in `policy.json` and must be listed in the README table.
- Run the suite before pushing; CI runs it on Linux and macOS, Python 3.9 / 3.11 / 3.13, plus gitleaks.

## Adding a backend

1. Add `backends/<id>.json` (invoke line, quota source, data boundary).
2. Add `probe_<id>`, `plan_<id>` and an executor in `bin/openswarm`, and register it in `PLANNERS` / `EXECUTORS`.
3. Add fixture data and routing scenarios.

## Reporting bugs

Open an issue with `openswarm status` output (it contains no secrets) and, for run problems, the run's
`result.json` with anything private removed. Security issues: please use GitHub's private vulnerability
reporting instead of a public issue.

By contributing you agree that your contributions are licensed under the Apache License 2.0.
