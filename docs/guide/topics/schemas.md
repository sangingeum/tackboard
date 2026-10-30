The schemas are the source of truth for every document below: one JSON Schema
(draft 2020-12) per shape, in the `schemas` directory of the repository and
inside the installed package (`tackboard/schemas`).

```bash
tack schema --json            # every schema: name, $id, repo-relative path
tack schema show --json       # one schema document, unchanged
tack schema board --json
```

`$id`s are URNs (`urn:tackboard:schema:<name>`) — there is no hosted copy at a
public URL, so a consumer builds a resolver from the listed documents. The task
projection is defined once, in `schemas/task.json` (`$defs.task`,
`$defs.node`, `$defs.event`, `$defs.note`, `$defs.blocker`), and every other
schema references it rather than restating it. Each schema sets
`additionalProperties: false`: new fields must be declared, which is the
additive-only rule above in machine-readable form. An unknown document name is
a usage error (exit 2) that lists the allowed set.

Naming: the schema names are the document shapes — `show`, `next`,
`next_claim`, `board` (with and without `tasks`; the same schema describes
both, `tasks` is optional), `deps`, `graph`, `history`, `history_since`,
`history_follow` (one NDJSON line), `plan_apply`, `claims`, `agents`, `memo`,
`memo_history`, `memo_diff`, `memo_write`, `doctor`, `task_mutation`, `note`,
`init`, `migrate`, `backup`, `error`.
