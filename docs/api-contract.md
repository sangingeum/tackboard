# The JSON API contract

`tack serve` exposes a read-only HTTP view of one board. Four routes return JSON:

| Route | Query | Document |
|---|---|---|
| `/api/board` | `status`, `assignee`, `priority`, `label`, `blocked`, `eligible` | the board document |
| `/api/graph` | `task` | the dependency graph document |
| `/api/tasks/<id>` | — | the task detail document |
| `/api/memo` | — | the board memo document |

`GET` and `HEAD` only; every other method answers `405`. Every JSON response
carries `ETag` and `Cache-Control: no-cache`. A request that fails inside a
route answers with the same error envelope the CLI prints under `--json`
(`{"schema_version": 1, "error": {code, message, exit_code, retryable, details}}`),
mapped to `404` (not found) or `400` (usage or validation). An unknown path,
including `/api/history`, is outside the contract and answers `404` in plain
text.

## The rule

> For the same board and the same query, the HTTP document is a **superset** of
> the CLI document: every key the CLI document carries is present in the HTTP
> document with the same name, the same meaning and the same shape. The HTTP
> document may carry additional keys, and those keys are declared in its own
> published schema.

The relationship is one-directional on purpose. Equality is not the contract,
and it never was in the code: both documents are produced by the same functions
in `tackboard.views`, which call the same domain reads. The HTTP layer adds what
a browser needs to render without re-deriving board state; the CLI adds nothing
the HTTP layer must not have.

Consequences that follow from the rule:

- **A CLI document may shrink, an HTTP document may not.** Removing a key from a
  CLI document is a contract change under the deprecation policy. Removing a key
  from an HTTP document is a breaking change for the browser and for any
  consumer reading it, and needs its own schema version.
- **A new CLI key appears in the HTTP document too** unless the route has no
  counterpart for that document, and then the API schema must say so.
- **Byte-identity is not a property to test for.** Both documents are JSON
  objects; key order and the CLI's trailing newline are not part of the shape.

## What each route carries

Measured against a served board and the same board read through the CLI. "Extra"
means present in the HTTP document, absent from the CLI document named in the
column.

### `/api/board` — the board document

CLI counterpart: `tack board --json` (also `tack list --json`).

Relationship: **equal**. The HTTP body is the CLI document with no trailing
newline. Top-level keys: `schema_version`, `project`, `counts`, `tasks`; each
task is the full task projection.

The query surface is narrower than the CLI's: `status`, `assignee`, `priority`,
`label`, `blocked` and `eligible` are accepted, while `--limit`, `--sort`,
`--reverse` and `--full` are not. A filter that names a value the board cannot
satisfy is a `400` with the CLI's validation message. `blocked` and `eligible`
are presence flags: `?blocked=0` still filters to blocked tasks, and the value
is ignored.

### `/api/graph` — the dependency graph document

CLI counterpart: `tack graph --format json` (also `tack graph --json`).

Relationship: **superset**. Top-level keys are `schema_version`, `nodes`,
`edges`, unchanged. Each node is the CLI node plus four derived readiness flags:

| Extra node key | Meaning |
|---|---|
| `dependency_ready` | every prerequisite is finished |
| `eligible` | the task can start now under the stage and assignee rules |
| `reviewable` | the task can move to review |
| `blocked` | the task cannot progress for a prerequisite or explicit reason |

The CLI node itself carries `id`, `title`, `status`, `priority`, `assignee`,
`claimed_by` and `claimed_at`; all seven appear in the HTTP node unchanged.

Claim staleness is **not** part of `/api/graph`. `claim_age_seconds` and
`claim_stale` are added only for the server-rendered graph page, which calls the
same view with its staleness opt-in; the page passes the stale task ids into the
canvas and the script marks those nodes itself. A consumer of `/api/graph` reads
`claimed_at` and applies its own threshold.

### `/api/tasks/<id>` — the task detail document

CLI counterpart: `tack show <id> --json`.

Relationship: **superset**. The CLI document is `schema_version`, `task`,
`notes`. The HTTP document is `schema_version`, `task`, `notes`, plus:

| Extra key | Shape | Meaning |
|---|---|---|
| `claim` | `{task_id, agent_id, claimed_at, age_seconds}` or `null` | the active claim; `null` when unclaimed |
| `prerequisites` | array of task projections | the same entries `tack deps <id> --json` returns |
| `dependents` | array of task projections | the same entries `tack dependents <id> --json` returns |
| `history` | array of history events | the newest 20 events for this task |

The shared parts are produced by the same reads as their CLI counterparts, so
`task`, `notes`, each `prerequisites`/`dependents` entry and each history event
carry the same keys and values. Two shape notes belong to the contract:

- `prerequisites` and `dependents` entries are the projection **without**
  `description`, matching `tack deps` and `tack dependents`, not `tack show`.
- `history` is the same event shape as `tack history <id> --json`'s `items`,
  wrapped under `history` rather than `items`, and **capped at the newest 20
  events**. The CLI command is unbounded unless `--limit` is given. For a task
  with more than 20 events, the HTTP list is a prefix of the CLI list, not an
  alternative copy of it.

There is no `/api/history` route. History over HTTP is this embedded list; a
consumer that needs the whole feed reads `tack history --json`.

### `/api/memo` — the board memo document

CLI counterpart: `tack memo --json`.

Relationship: **equal**. The HTTP body is the CLI document with no trailing
newline. `tack memo --rev N`, `--history` and `--diff` have no HTTP counterpart;
the route always returns the current revision.

## Why not the alternatives

**Shrinking the HTTP documents to the CLI documents was rejected.** The graph
canvas fetches `/api/graph` and renders `blocked`, `eligible` and `reviewable`
directly: node overlays, the status words, the `data-` attributes the keyboard
model reads, and the "Hide done & cancelled" recount. Those flags are domain
derivations. Removing them would either push readiness logic into the browser —
a second implementation of the readiness rules, which the one-domain-layer
principle forbids — or force the graph to be rendered server-side only, losing
the live polling the page exists for. It would also be a breaking change for any
consumer already reading the route, with no version to deprecate it under.

**Growing the CLI documents to make the API a pure mirror was rejected too.**
Making `tack graph --json` carry four derived booleans on every node costs every
CLI consumer, including agents whose context is the scarce resource: the whole
point of the reduced node is that a graph of a thousand tasks stays small.
Making `tack show` carry the active claim, both dependency lists and twenty
history events turns one cheap command into an aggregate that duplicates
documents the CLI already exposes (`claims`, `deps`, `dependents`, `history`)
and breaks the rule that a command prints one document about one thing. The
token-economy defaults — no `description` on board and list, a five-note cap on
`show`, an unbounded `history` that is asked for explicitly — would all be
undone to satisfy a claim of equality that no consumer needs.

**Amending the bar is therefore the honest option**, and it is the only one
that leaves both interfaces doing their own job through one domain layer.

## Schemas

The published schemas describe the documents that exist today, and today they
cover the CLI documents only. `/api/board` and `/api/memo` validate against
`board.json` and `memo.json`. `/api/graph` does **not** validate: its nodes
carry four keys that `task.json`'s node definition does not allow, and every
schema is closed with `additionalProperties: false`. `/api/tasks/<id>` has no
schema at all.

The contract is only enforceable once the API documents have their own
published shapes:

- `api_graph.json` — the graph document, with nodes referencing the shared node
  definition plus the four derived flags, all closed.
- `api_task.json` — the task detail document, referencing the shared task, note
  and event definitions, declaring `claim`, `prerequisites`, `dependents` and
  `history`.

`scripts/validate_schemas.py` must then produce and validate both documents
against a scratch board, so an undeclared key on either route fails CI exactly
as an undeclared CLI key does. Publishing the shapes grows the schema set from
24 files (23 document shapes plus the shared task projection) to 26 files (25
document shapes plus the projection), and that count is stated in
`docs/1.0-criteria.md` and must move with it.

Until those two schemas land, the contract in this document is written and
reviewed but not machine-enforced, and `/api/graph` is a document the project
publishes without describing. That gap is the implementation work, not a
disagreement about the rule.

## Changing the contract

Any additive key on an HTTP document is declared in that document's schema in
the same change, `schema_version` stays `1`, and `CHANGELOG.md` records the
addition. Adding a route is a new document shape and a new schema. Removing,
renaming or re-meaning a key on an HTTP document is a breaking change and
follows the deprecation policy: a new `schema_version`, the old document
available for at least one minor release, and the change named in
`CHANGELOG.md`.
