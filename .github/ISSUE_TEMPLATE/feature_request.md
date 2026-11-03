---
name: Feature request
about: Propose a new capability or behaviour change
title: ""
labels: enhancement
assignees: ""
---

## Problem

<!-- What is hard, slow, or impossible today? Who hits it — planner, executor, or operator? -->

## Proposal

<!-- What should swrm do instead? Include the exact command / flag / output shape if you have one in mind. -->

## Alternatives considered

## Constraints checklist

- [ ] Keeps swrm single-file, standard-library-only (no third-party runtime dependencies)
- [ ] Does not read another tool's credentials by default; any new local data source goes behind a `local_sources.*` switch in `policy.json` and is listed in the README table
- [ ] Can be tested without a real backend, network, Keychain, or a real `~/.codex` / `~/.claude` (fixtures / temp dirs only)
- [ ] Behaviour or flag changes are reflected in `SKILL.md`

## Additional context
