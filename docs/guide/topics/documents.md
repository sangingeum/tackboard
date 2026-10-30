Limits are enforced on write; a violation is exit 4 (`validation`).

| Field | Limit |
|---|---|
| `title` | 200 characters, after trimming (whitespace-only is rejected) |
| `description` | 64 KiB (65536 UTF-8 bytes) |
| memo text (`memo`) | 64 KiB (65536 UTF-8 bytes) |
| `label` | 50 characters per label, at most 20 labels per task |
| `blocked_reason` | 2 KiB (2048 UTF-8 bytes) |
| note text (`note`) | 2 KiB (2048 UTF-8 bytes) |
| note refs (`--ref`) | at most 20 refs per note; per-kind limits below |
| `--reason` (with `--force`, and always on `reject`) | 1024 characters |
| agent id (`--agent`, `--assignee`) | 1-64 characters from `[A-Za-z0-9._:-]` |

Ref limits by kind: `commit` is a git object id of 7-40 hexadecimal
characters; `url` is an absolute `http`/`https` URL with no whitespace, at most
2048 characters; `path` is a repository-relative path — no leading `/`, no
backslash, no `.`/`..`/empty segment — at most 1024 characters; `task` must
name an existing task on the same board. Identical `kind:value` pairs collapse
to one before the cap is counted.

Read the limit before writing: a rejected `create` costs a round trip, and the
fix is usually to shorten the title and move the full text into
`--description`. A length violation's `details` names the field:

```json
{"schema_version": 1, "error": {"code": "validation", "message": "Title is too long: 228 characters, maximum is 200.", "exit_code": 4, "retryable": false, "details": {"field": "title", "max": 200, "unit": "characters", "actual": 228}}}
```

- Every document carries `"schema_version": 1`; fields are only ever added.
- `--json` prints exactly one JSON document on **stdout**, never prose. With
  `--json`, errors are printed to **stdout** as
  `{"schema_version":1,"error":{"code","message","exit_code","retryable","details"}}`
  and stderr stays empty unless `--verbose`. Without `--json`, errors go to
  stderr and humans get prose.
- **The one NDJSON exception: `history --follow`.** It streams one flat JSON
  event object per line on stdout as events appear — the only command allowed
  to emit more than one JSON document. It implies `--json`; combining it with
  `--limit` is a usage error.
- `board`, `list`, `next` (both forms), `deps`/`dependents` and `claims` emit
  the **task projection without `description`** — the token-economy default.
  `board`, `list` and `next` take `--full` to include it; `deps`/`dependents`
  and `claims` have **no `--full` opt-in** and never carry a description. `show`
  and mutation results always include it on the `task` object; the
  `active_dependents` entries a `reopen` adds are likewise the projection
  *without* a description (see below).
- Error `details` carry machine-readable keys where they exist (a length
  violation gives `field` / `max` / `unit` / `actual`; a claim conflict gives
  `claimed_by`) — read them instead of parsing the message text.

The top-level keys of every `--json` document a consumer meets. `[...]` is an
array; `{...}` is an object whose inner keys are described below the table.

| Command | Top-level shape |
|---|---|
| `show <id>` | `{schema_version, task:{...}, notes:[...]}` |
| `next` | `{schema_version, tasks:[...]}` (the actor's ranked eligible candidates, in selection order; `[]` when none) |
| `next --claim` | `{schema_version, task:{...}}` (`task` is `null` when nothing is eligible) |
| `board` / `list` | `{schema_version, project, counts:{...}, tasks:[...]}` |
| `board` / `list` with `--brief` or `--count` | `{schema_version, project, counts:{...}}` (no `tasks`) |
| `deps <id>` / `dependents <id>` | `{schema_version, task_id, tasks:[...]}` |
| `graph --format json` | `{schema_version, nodes:[{id,title,status,priority,assignee,claimed_by,claimed_at}], edges:[{source,target}]}` |
| `graph --format dot` | Graphviz text, not JSON |
| `history [<id>]` | `{schema_version, task_id, items:[{id,timestamp,actor,event_type,task_id,payload}]}` (newest first) |
| `history [<id>] --since EVENT_ID` | `{schema_version, task_id, order:"asc", since, items:[...]}` (events with `id > EVENT_ID`, oldest first) |
| `history [<id>] --follow [--since EVENT_ID]` | NDJSON: one `{schema_version, id, timestamp, actor, event_type, task_id, payload}` per line |
| `plan apply <file>` | `{schema_version, dry_run, source, count, tasks:[{id, key, title}]}` (`id` is `null` under `--dry-run`) |
| `claims` | `{schema_version, items:[{task_id,agent_id,claimed_at,age_seconds,task:{...}}]}` |
| `agents` | `{schema_version, agents:[{agent_id,claimed_tasks,last_event_at,completed,released,forced}]}` |
| `memo` / `memo --rev N` | `{schema_version, rev, text, author, created_at, bytes}` (rev 0 with no memo: `rev:0`, `text:""`, `author:null`, `created_at:null`, `bytes:0`) |
| `memo --history` | `{schema_version, current_rev, items:[{rev, author, created_at, bytes, reason}]}` (newest first) |
| `memo --diff [A [B]]` | `{schema_version, from, to, added, removed, unified}` (`unified` is the text a human sees) |
| a memo write | `{schema_version, changed, memo:{rev, text, author, created_at, bytes}}`; `--dry-run` adds `dry_run:true`, `max`, `bytes`, `remaining` |
| `doctor` | `{schema_version, ok, errors:[...], warnings:[...]}` |
| a mutation except `note` and `reopen` (`create`, `update`, `status`, `complete`, `cancel`, `restore`, `reject`, `claim`, `release`, `depend add/remove`) | `{schema_version, changed, task:{...}}` |
| `reopen <id>` | `{schema_version, changed, task:{...}}`, plus `active_dependents:[...]` when it changes anything |
| `note <id> TEXT [--ref KIND:VALUE ...]` | `{schema_version, changed, event:{...}}`; the event's `payload` is `{text, refs:[{kind, value}]}` |
| `init` | `{schema_version, ok, database, operation, created, project_label, database_schema_version}` |
| `migrate` | `{schema_version, ok, database, operation, migrated, from_version, to_version, database_schema_version}`, plus `backup` when a migration ran |
| `backup` | `{schema_version, ok, database, operation, backup, source_application_id, database_schema_version}` |
| any error under `--json` | `{schema_version, error:{code,message,exit_code,retryable,details}}` |

`guide` prints text and ignores `--json`. `--brief` and `--count` print human
text without `--json` and the `counts`-only document with it.

The **task projection** (the `task` object, and each entry of `tasks`) carries
`id`, `title`, `description` (omitted on `board`/`list`/`next` unless `--full`),
`status`, `priority`, `assignee`, `labels`, `depends_on`, `dependency_ready`,
`eligible`, `reviewable`, `blocked`, `blocked_reason`, `blockers`,
`claimed_by`, `claimed_at`, `claim_age_seconds`, `claim_stale`, `rejections`,
`last_rejection_reason`, `created_at`, `updated_at`, `completed_at`, `version`.
`claim_age_seconds` is the active
claim's age in whole seconds (`null` when the task has no active claim) and
`claim_stale` says whether that age reached the configured
`stale_claim_after` threshold (always `false` without a claim). Both are
derived read-only fields — nothing is stored, `schema_version` stays `1`, and
the age is measured against one clock per read. A `graph --format json` node is
a reduced subset of it
(`id`, `title`, `status`, `priority`, `assignee`, `claimed_by`, `claimed_at`).

- **`show` is a container, not the task.** The task object is under `task`;
  `notes` holds the task's notes (newest first, at most five). Read
  `document["task"]`, not `document`.
- **`next` has two forms; `--claim` is the second.** Plain `next` is a
  container — `{"schema_version":1,"tasks":[...]}` holding the actor's whole
  ranked candidate list: every task that passes the stage, label, priority and
  assignee filters, not just one. When nothing is eligible it is
  `{"schema_version":1,"tasks":[]}` and the exit code is `0` — never `null`
  and never a scalar task. The candidates are in selection order, the order
  `next --claim` would take them in: priority high to low, then `created_at`,
  then the task's sequence (its creation order on the board). `next --claim`
  returns a **single-task document instead**: `{"schema_version":1,"task":{...}}`,
  the same task projection the candidates use (so without `--full` it omits
  `description`), with `task` equal to `null` when nothing is eligible. It has
  no `tasks` key; read `document["task"]`.
- **`init`, `migrate` and `backup` carry the operation's own `ok`, not
  `doctor`'s.** Each emits `{schema_version, ok, database, operation, ...}`.
  Here `ok: true` means *the operation succeeded* — the board was created,
  migrated or copied — which is a different thing from `doctor`'s `ok` (no
  errors found). Read the operation-specific key (`created`, `migrated`,
  `backup`) for what actually happened.
- **`deps` / `dependents`** name the queried task in `task_id` and hold its
  direct prerequisites / dependents in `tasks` (an empty `tasks` is normal).
- **`history`** sets `task_id` to the task you queried (`null` for the
  board-wide call) and holds the events in `items`. The plain form is newest
  first. `--since EVENT_ID` switches to the feed form: events with `id` greater
  than the cursor, **oldest first**, plus `order:"asc"` and the echoed `since`
  — so a consumer resumes exactly where it stopped and appends in order. The
  cursor is the last event `id` seen; delivery is at-least-once (an overlapping
  read may repeat an event, so de-duplicate on `id`). `--follow` streams new
  events as NDJSON (see the NDJSON exception above): without `--since` it starts
  at the board's current maximum id, with `--since` it resumes from the cursor,
  polls on a bounded interval, and exits **0** on SIGINT. A `--since` value that
  is not a non-negative integer is a validation error (exit 4).
  An assignment change is one `task.updated` event whose payload carries both
  sides: `previous_assignee` and `assignee` (`null` means unassigned), with
  `fields` still listing `assignee`. Older events may carry
  `previous_assignee` only. The change is additive within `schema_version` 1.
  **`claims`** holds one row per held claim in `items`, each row carrying the
  claim (`task_id`, `agent_id`, `claimed_at`, `age_seconds`) and the full `task`.
  **`agents`** is a derived summary, not stored state: one row per agent with at
  least one event or active claim (zero-activity agents never appear), carrying
  `agent_id`, `claimed_tasks` (ids), `last_event_at` (the timestamp of that
  agent's highest event id), and counts of `completed`, `released` and `forced`
  actions. A board with no activity returns `"agents": []`; the human form is a
  compact table and prints `(no agent activity)` when empty.
- **Mutations return `{"schema_version":1,"changed":<bool>,"task":{...}}`**, with
  two exceptions. `note` returns `{changed, event}` (a note is an event, not a
  task change). `reopen` adds a fourth top-level key, `active_dependents`, when
  it actually reopens something — see the next bullet. `changed:false` means the
  call was an idempotent no-op: nothing was written and no event was recorded.
- **`reopen` carries `active_dependents` only when `changed` is true.** The key
  is a fourth top-level field beside `schema_version`, `changed` and `task`, and
  it is absent from every other mutation and from a no-op `reopen`. The array
  lists the reopened task's direct dependents whose status is `in_progress` or
  `review` — the dependents that had already started — ordered by each
  dependent's own sequence (its creation order on the board). Each entry is the
  task projection as `board`/`list` produce it without `--full`: the same keys
  as the `task` object, except that `description` is omitted. A changed reopen
  may report an **empty array**: the key is present with `[]` when no dependent
  has started. Its meaning: reopening a prerequisite leaves those
  started dependents where they are, so they stay `in_progress`/`review` while
  now being displayed as blocked — the array is exactly the set a human or agent
  must decide about. See the matching pitfall, *Reopening a prerequisite does
  not rewrite anyone's status*.

`board --json` and `list --json` carry a `counts` object: `tasks`, one key per
status (`backlog`, `ready`, `in_progress`, `review`, `done`, `cancelled`), and
the derived `eligible`, `reviewable`, `blocked`, `claimed`.

**Every count describes the FILTERED result set, not the whole board.** The
counts are computed over exactly the `tasks` array in the same document, so
with any filter applied they are not board totals:

- `counts.tasks` is the size of the **filtered result**, not the board size.
  `board --json` (unfiltered) reports the board size; `list --eligible --json`
  reports the number of eligible tasks.
- `counts.blocked` counts blocked tasks **within the filtered set**.
  `list --eligible --json` therefore reports `blocked: 0` even when the board
  holds blocked tasks — an eligible task is never blocked by definition. To
  count the board's blocked tasks, query `list --blocked --json` (or read
  `board --count`).

`--brief` (one line of counts) and `--count` (`key=value` per line) are the
cheapest way to ask "what does this board look like?"; combining them is a
usage error. Both accept the same filters as `board`, and their counts are the
same filtered set.
