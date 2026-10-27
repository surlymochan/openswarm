# Curated patterns

The runtime pattern catalog lives at `<knowledge.repo>/skills/`. Store each pattern in its own `<id>.md` file,
using an ID such as `P-0001`. `openswarm lessons preview` shows patterns that match the selected card type, backend,
and path hints. Dispatch injects at most three active patterns, with a combined limit of 1,500 characters.

## File format

Each file starts with YAML front matter and has a short Markdown body:

```markdown
---
id: P-0001
title: Validate archive paths before extraction
applies_to:
  card_types: [fix, feature]
  paths: [src/archive/**]
  backends: [codex]
source: "PR #42"
status: active
---
When to use: When changing archive extraction or path validation.
Pattern:
- Resolve the destination before writing files.
- Reject paths that escape the extraction root.
- Cover nested traversal and separator variants in tests.
Example: [path validation change](https://github.com/example/project/pull/42)
```

Required front matter fields are `id`, `title`, `applies_to`, `source`, and `status`. `applies_to` contains inline
lists for `card_types`, `paths`, and `backends`; an empty list matches any value for that dimension. `source` names
the compare ID, PR, or review evidence. `status` is `active` or `retired`. The body explains when to use the pattern,
states the pattern in three to six lines, and links one good example.

Keep patterns concrete and reusable. The catalog is read-only to `openswarm patterns propose` and
`openswarm patterns retire-check`; those commands write drafts under `<knowledge.repo>/proposals/`. Review a draft
before copying a curated version into `skills/`.
