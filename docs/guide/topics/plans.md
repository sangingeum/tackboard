`tack plan apply FILE` creates a whole dependency DAG in one command. The file
is JSON with one top-level key, `tasks`, whose elements carry a local `key`
(file-scoped handle, unique within the file), `title` (required),
`description`, `status` (default `ready`), `priority` (default `normal`),
`assignee`, `labels`, and `depends_on` (an array of keys from the same file).
Unknown fields are a validation error. `-` reads stdin. The command is
**create-only**: it never updates or adopts an existing task, and applying the
same file twice creates a second set. The whole document is validated first;
an accepted batch commits its rows, labels and edges in one `BEGIN IMMEDIATE`
transaction, so any failure leaves the board exactly as it was. `--dry-run`
validates and reports without writing anything (not even events). A cycle
among the batch's own edges is a validation error (exit 4). A plan appends one
`task.created` event per task and one board-level `plan.applied` event.

A complete plan file — two keys, one edge, one element without a key:

    {"tasks": [
      {"key": "parser", "title": "Ship the parser", "priority": "high",
       "assignee": "cpp-dev", "labels": ["core"]},
      {"key": "cli", "title": "Wire the CLI", "depends_on": ["parser"]},
      {"title": "Document the plan", "depends_on": ["cli"], "priority": "low"}
    ]}

Applying it returns one JSON document with `count: 3` and one
`{id, key, title}` element per created task (the keyless element's `key` is
`null`). Every mutating command needs an actor: pass `--agent ID` or set
`TACKBOARD_AGENT`.
