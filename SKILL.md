---
name: tackboard
description: Use for agent task coordination on a per-project board via the `tack` CLI — create, claim, note and complete tasks, model dependency DAGs, read derived readiness, and select work atomically with `next --claim`. Use when several agents share one project's work and need a stable machine-readable contract; do NOT use for note-taking, file search (use rg) or git history.
version: 0.1.1
author: sangingeum
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Task Board, Agent Coordination, CLI, SQLite, Dependencies]
    related_skills: []
---

# tack — a project-scoped task board for agents

`tack` is a small coordination board: one SQLite file per project, a CLI as the
primary interface for agents, and a read-only web view for a human operator. It
tracks **what is being worked on, by whom, and what must finish first** — not
schedules, estimates, or a workflow engine.

Three properties shape every command:

- **Status is stored; readiness is derived.** `eligible`, `reviewable` and
  `blocked` are computed from the dependency graph, claims and explicit
  blockers. Never infer them from stored status alone.
- **Claims are a short-lived execution lock; assignees are long-lived.** They
  are different things and are stored separately: a claim never writes the
  assignment. The assignment is a routing note — "who is it for" — written only
  by an explicit routing command (`create --assignee` / `update --assignee`);
  the claim — "who is working this now" — is written only by the agent that
  takes it.
- **CLI output is a contract.** Every command has a stable JSON document and
  stable exit codes. Agents must never scrape the human-formatted output.

## When to use

- Two or more agents (or one agent over many sessions) share one project's work.
- Work has ordering constraints: task B is meaningless until task A lands.
- You need to hand work off with context: claim it, work it, leave a note,
  complete it, and let the next agent pick up what the board says is ready.
- A human wants to watch progress in a browser without touching the database.

**Don't use for:** personal note-taking; recording findings that belong in a
commit, issue, or document; capturing anything that does not need to be
*claimed and completed*.

## Setup

```bash
tack init --db .board/project.sqlite3     # creates parent directories
export TACKBOARD_DB=.board/project.sqlite3
export TACKBOARD_AGENT=my-agent           # every mutating command needs an actor
tack guide                                # the agent-oriented usage summary
```

Project/database resolution, highest precedence first:

1. `--db PATH` (relative paths resolve against the current directory)
2. `TACKBOARD_DB`
3. the nearest `.tackboard.toml` in the current directory or a parent:

   ```toml
   [tackboard]
   db = ".board/project.sqlite3"   # relative to the file's own directory
   ```

A command that cannot resolve a database fails (exit 3). Nothing except `init`
ever creates a database — a typo in a path is an error, not a new empty board.

If `tack` is not on `PATH`, use `uv run tack ...` from a checkout, or
`python -m tackboard ...`.

## The work loop

This is the whole discipline; the command reference below exists to support it.

1. **Select work atomically.** `tack next --claim --json` picks the top
   *eligible* task and claims it in a single transaction. Two agents running it
   concurrently get two different tasks, or one gets `{"schema_version":1,
   "task":null}` — `--claim` emits a **single-task document**, not the plain
   `next` container, so with nothing eligible its `task` is `null`. Plain
   `tack next --json` is the other form: `{"schema_version":1,"tasks":[...]}`,
   where the empty result is an **empty `tasks` array** — never a bare task and
   never `null`. See *The JSON contract*.
2. **Read before you act.** `tack show <id> --json` — confirm the status,
   prerequisites, description and any prior notes.
3. **Record intent while working.** `tack note <id> "..."` for handoff context,
   a repro command, or a link to the artifact. Notes are append-only and cheap.
4. **Complete it.** `tack complete <id> --json`. Completing a task is what
   unblocks its dependents — it is the moment the board changes shape.
5. **Review stage.** If your project routes work through `review`, a reviewer
   takes it with `tack next --stage review --claim --json`, then
   `tack complete <id>` to accept, or `tack status <id> in_progress --json` to
   send it back for rework.
6. **If you cannot finish.** `tack release <id> --json` (or leave the claim
   with a note, if you intend to continue in the same session). A claim held by
   a dead agent is the one thing that stalls a board — see best practice 14.

## Command reference

Global options (`--db`, `--json`, `--quiet`, `--verbose`, `--version`,
`--help`) work **before or after** the subcommand.

```text
tack init [--db PATH]
tack migrate | tack backup PATH | tack doctor [--json]
tack board|list [--status S] [--assignee A] [--label L] [--priority P]
                [--blocked] [--eligible|--ready] [--limit N]
                [--sort FIELD] [--reverse] [--full] [--brief] [--count]
tack show TASK_ID
tack create TITLE [--description TEXT | --description-file PATH]
                  [--priority low|normal|high|urgent] [--assignee A]
                  [--label L ...] [--depends-on TASK_ID ...] [--status STATUS]
tack update TASK_ID [--title T] [--description TEXT] [--priority P]
                    [--assignee A|--assignee ""] [--label L|--add-label L|--remove-label L]
                    [--blocked-reason TEXT|--clear-blocked-reason] [--if-version N]
tack status TASK_ID STATUS [--force --reason TEXT] [--if-version N]
tack complete|reopen|cancel|restore TASK_ID [--force --reason TEXT] [--if-version N]
tack note TASK_ID TEXT
tack claim TASK_ID [--force --reason TEXT] [--if-version N]
tack release TASK_ID [--force --reason TEXT] [--if-version N]
tack claims [--agent A] [--older-than 2h]
tack next [--agent A] [--claim] [--stage work|review] [--label L]
          [--min-priority P] [--all-assignees] [--full] [--force --reason TEXT]
tack depend add TASK_ID --on PREREQUISITE_ID [--force --reason TEXT] [--if-version N]
tack depend remove TASK_ID --on PREREQUISITE_ID [--if-version N]
tack deps TASK_ID | tack dependents TASK_ID
tack graph [TASK_ID] [--format json|dot]
tack history [TASK_ID] [--limit N]
tack serve [--host H] [--port P] [--allow-remote] [--allowed-host HOST ...]
tack guide
```

Every mutating command takes `--agent` or falls back to `TACKBOARD_AGENT`
(the explicit option wins). Read-only commands (`board`, `list`, `show`,
`deps`, `dependents`, `graph`, `history`, `claims`, `doctor`) need no actor.
`next` needs one even without `--claim`, because selection is agent-specific.
`next --claim` prints a single-task document where plain `next` prints the
`tasks` container — see *The JSON contract*.

### Statuses and transitions

`backlog → ready | cancelled`, `ready → backlog | in_progress | cancelled`,
`in_progress → ready | review | done | cancelled`,
`review → in_progress | done | ready | cancelled`,
`done → ready` (reopen), `cancelled → backlog` (restore).

There is no stored `blocked` status — blocking is always derived. Setting a task
to the status it already has is a **no-op success** (`"changed": false`, no
version bump); the shortcuts behave the same way (`complete` on `done`,
`reopen` on `ready`, `cancel` on `cancelled`, `restore` on `backlog`).

Be aware that `claim` only works on a **`ready`** task. A task created in
`backlog` (the default) must be moved to `ready` before anyone can claim it —
this is deliberate: `ready` is the decision that work may start. Claiming does
not require being the assignee: the assignment is a routing note, so any agent
may claim a `ready` task, and the claim names whoever takes it.

**Entering `in_progress` claims the task; leaving it releases the claim.** A
transition to `in_progress` sets `claimed_by`/`claimed_at` to the acting agent —
the same state `claim` produces — however you got there: `status <id>
in_progress`, `claim`, `next --claim`, or the `--force` path that overrides an
unfinished prerequisite. Leaving `in_progress` — to `review`, `done`, or back
to `ready`, and likewise `complete` from `in_progress` — clears them
(`claimed_by: null`). So a task moved to `in_progress` by `status` is held, not
free, and a claim is not always an explicit `claim` call. None of those paths
touches the assignment: `assignee` is written only by a routing command.

## Field limits

Limits are enforced on write; a violation is exit 4 (`validation`).

| Field | Limit |
|---|---|
| `title` | 200 characters, after trimming (whitespace-only is rejected) |
| `description` | 64 KiB (65536 UTF-8 bytes) |
| `label` | 50 characters per label, at most 20 labels per task |
| `blocked_reason` | 2 KiB (2048 UTF-8 bytes) |
| note text (`note`) | 2 KiB (2048 UTF-8 bytes) |
| `--reason` (with `--force`) | 1024 characters |
| agent id (`--agent`, `--assignee`) | 1-64 characters from `[A-Za-z0-9._:-]` |

Read the limit before writing: a rejected `create` costs a round trip, and the
fix is usually to shorten the title and move the full text into
`--description`. A length violation's `details` names the field:

```json
{"schema_version": 1, "error": {"code": "validation", "message": "Title is too long: 228 characters, maximum is 200.", "exit_code": 4, "retryable": false, "details": {"field": "title", "max": 200, "unit": "characters", "actual": 228}}}
```

## The JSON contract

- Every document carries `"schema_version": 1`; fields are only ever added.
- `--json` prints exactly one JSON document on **stdout**, never prose. With
  `--json`, errors are printed to **stdout** as
  `{"schema_version":1,"error":{"code","message","exit_code","retryable","details"}}`
  and stderr stays empty unless `--verbose`. Without `--json`, errors go to
  stderr and humans get prose.
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

### Document shapes

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
| `graph --format json` | `{schema_version, nodes:[{id,title,status,priority,assignee}], edges:[{source,target}]}` |
| `graph --format dot` | Graphviz text, not JSON |
| `history [<id>]` | `{schema_version, task_id, items:[{id,timestamp,actor,event_type,task_id,payload}]}` |
| `claims` | `{schema_version, items:[{task_id,agent_id,claimed_at,age_seconds,task:{...}}]}` |
| `doctor` | `{schema_version, ok, errors:[...], warnings:[...]}` |
| a mutation except `note` and `reopen` (`create`, `update`, `status`, `complete`, `cancel`, `restore`, `claim`, `release`, `depend add/remove`) | `{schema_version, changed, task:{...}}` |
| `reopen <id>` | `{schema_version, changed, task:{...}}`, plus `active_dependents:[...]` when it changes anything |
| `note <id> TEXT` | `{schema_version, changed, event:{...}}` |
| `init` | `{schema_version, ok, database, operation, created, project_label, database_schema_version}` |
| `migrate` | `{schema_version, ok, database, operation, migrated, from_version, to_version, database_schema_version}` |
| `backup` | `{schema_version, ok, database, operation, backup, source_application_id, database_schema_version}` |
| any error under `--json` | `{schema_version, error:{code,message,exit_code,retryable,details}}` |

`guide` prints text and ignores `--json`. `--brief` and `--count` print human
text without `--json` and the `counts`-only document with it.

The **task projection** (the `task` object, and each entry of `tasks`) carries
`id`, `title`, `description` (omitted on `board`/`list`/`next` unless `--full`),
`status`, `priority`, `assignee`, `labels`, `depends_on`, `dependency_ready`,
`eligible`, `reviewable`, `blocked`, `blocked_reason`, `blockers`,
`claimed_by`, `claimed_at`, `created_at`, `updated_at`, `completed_at`,
`version`. A `graph --format json` node is a reduced subset of it
(`id`, `title`, `status`, `priority`, `assignee`).

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
  the same `task` object a mutation returns, with `task` equal to `null` when
  nothing is eligible. It has no `tasks` key; read `document["task"]`.
- **`init`, `migrate` and `backup` carry the operation's own `ok`, not
  `doctor`'s.** Each emits `{schema_version, ok, database, operation, ...}`.
  Here `ok: true` means *the operation succeeded* — the board was created,
  migrated or copied — which is a different thing from `doctor`'s `ok` (no
  errors found). Read the operation-specific key (`created`, `migrated`,
  `backup`) for what actually happened.
- **`deps` / `dependents`** name the queried task in `task_id` and hold its
  direct prerequisites / dependents in `tasks` (an empty `tasks` is normal).
- **`history`** sets `task_id` to the task you queried (`null` for the
  board-wide call) and holds the events in `items`. **`claims`** holds one row
  per held claim in `items`, each row carrying the claim (`task_id`,
  `agent_id`, `claimed_at`, `age_seconds`) and the full `task`.
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

### The `counts` object

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

### The `doctor` document

`doctor --json` returns
`{"schema_version":1,"ok":<bool>,"errors":[...],"warnings":[...]}`:

- `errors` lists problems that make the board unusable — a missing database, a
  wrong application id, an unsupported schema version, broken foreign keys, a
  dangling dependency, a cycle, corrupt metadata. A non-empty `errors` exits
  **6**.
- `warnings` lists inconsistencies that are real but not fatal — an
  `in_progress` task whose prerequisite is unfinished, an `in_progress` task
  with no claim. Warnings still exit **0**.
- `ok` is `not errors`. **`ok:true` does NOT mean the board is clean**: it only
  means no errors were found, and it is emitted alongside a non-empty
  `warnings` array. The health signal is the `warnings` array (and `errors`) —
  not `ok`, and not the exit code. Read `warnings` on every run.
- Every entry of `errors` and `warnings` carries `code` (the machine-readable
  kind) and `message` (the human-readable text); beyond those two an entry
  carries only the keys its own kind needs. The claim and started-dependent
  checks name the task in `task_id` (`claim_on_inactive_task`,
  `in_progress_without_claim`), and `inconsistent_blocked_state` adds
  `prerequisite_id` for the unfinished prerequisite. A `schema_version` error
  adds `found` / `supported`; a `dependency_cycle` adds `path`; a foreign-key
  violation adds `table` / `rowid` — including `dangling_dependency`, whose
  entry concerns a task row but names it by `table` / `rowid`, not `task_id`.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | success, including idempotent no-ops and an empty `next` |
| 1 | unexpected internal error |
| 2 | usage error: bad option combination, or no actor |
| 3 | task, project, or database not found / not configured |
| 4 | validation error, invalid transition, or dependency cycle |
| 5 | concurrency conflict: claim held, stale version, lock timeout (`retryable: true`) |
| 6 | database, schema, or migration error, or a `doctor` run that found errors |

## Best practices

1. **Claim before you work; never work a task you do not hold.** The claim is
   what tells every other agent the task is taken. A task you are editing
   without holding is a task two agents are about to collide on. Holding need
   not come from a `claim` call: moving a task to `in_progress` (by `status`,
   `claim`, or `next --claim`) claims it for you, and leaving `in_progress`
   releases it — see *Statuses and transitions*.
2. **Use `next --claim`, not `list` + `claim`.** Selection and claiming in one
   transaction is the whole point; the two-call version has a race window that
   the tool is explicitly designed to remove.
3. **Always pass `--json` and parse it.** Read `changed`, the task fields and
   `blocked`/`blockers` rather than grepping prose. Human output is for humans.
4. **Set your actor once** (`export TACKBOARD_AGENT=<you>`) instead of passing
   `--agent` on every call; override it only to act on someone else's behalf.
5. **Model ordering as dependencies, not as prose.** If B needs A, create the
   edge. Then `eligible` tells every agent the truth, and you never have to
   maintain a hand-written "do this after that" list. Prefer
   `create --depends-on` so the task and its edges land atomically.
6. **Finish tasks when they finish.** Complete a task as soon as the work is
   done; dependents are unblocked by `done` and by nothing else. A board full of
   finished-but-open tasks is a board whose readiness signal is a lie.
7. **Keep one task to one work unit** that a single agent can complete in one
   sitting. If a task needs a plan, it is several tasks.
8. **Leave a note at the handoff boundary** — where you stopped, what you
   verified, where the artifact is. A note costs one event and saves the next
   agent an archaeology session.
9. **Read-modify-write? Use `--if-version`.** If you took a snapshot with
   `show --json` and then act on it, pass `--if-version` from that snapshot: a
   mismatch fails with exit 5 rather than clobbering a concurrent change.
10. **Treat exit 5 as "someone else is there", not as an error to argue with.**
    `claim_conflict` means another agent holds it — pick other work. `locked`
    (with `"retryable": true`) means retry with a short backoff. Neither is a
    reason to force anything.
11. **`--force` is an audited, exhaustive list, not a general bypass.** It
    requires `--reason`, it is recorded as an event, and it is allowed only for
    the enumerated cases (taking over or releasing another agent's claim,
    starting work whose only blocker is a cancelled/unfinished prerequisite,
    recovering an `in_progress` task with no claim).
    If you reach for `--force` and your case is not on that list, the answer is
    a different command, not a bigger hammer.
12. **Use `blocked_reason` for blockers that are not tasks** (waiting on a
    decision, an external system, an unavailable resource). It is visible,
    derived into `blockers` as `explicit`, and cleared with
    `--clear-blocked-reason`. Do not fake a dependency edge to represent it.
13. **Prefer `cancel` to deletion — there is no delete.** A cancelled task
    keeps its history and its ID stays meaningful; a cancelled prerequisite
    blocks its dependents with reason `prerequisite_cancelled` until you remove
    the edge or restore and complete it.
14. **Recover stale work explicitly.** `tack claims --older-than 2h` lists
    claims that look abandoned; clear one with
    `tack release <id> --force --reason "..."`. An `in_progress` task with no
    claim at all is an inconsistent state — `doctor` reports it, and recovering
    it needs `--force --reason`.
15. **Never copy or commit a live database file.** Back up with
    `tack backup PATH` (a consistent snapshot taken while agents are writing);
    `cp` can capture a torn WAL state. Gitignore the database and its `-wal` /
    `-shm` sidecars.
16. **Let the human watch, not edit.** `tack serve` is read-only by design
    (GET/HEAD only, loopback by default, no authentication). Mutations go
    through the CLI so that every change lands as an audited event.
17. **Ask the board cheap questions.** `tack board --brief` or `--count` for a
    status roll-up, `tack list --eligible` for "what can start now",
    `tack list --blocked` for "what is stuck and why". These are far cheaper
    than listing every task into your context.

## Pitfalls (things that look broken but are not)

- **`claim` on a `backlog` task fails with exit 4.** That is the intended
  guard; move it to `ready` first.
- **`--ready` is an alias for `--eligible`, not a filter on the stored status.**
  Documentation uses `--eligible` precisely to keep those two ideas apart.
- **`done` is authoritative and non-transitive.** If a task is `done`, a
  reopened prerequisite *further upstream* does not block its dependents —
  readiness is evaluated over direct prerequisites only, by design.
- **Reopening a prerequisite does not rewrite anyone's status.** Dependents
  that were already `in_progress`/`review` stay there and are *displayed* as
  blocked; the `reopen --json` document lists exactly those dependents in
  `active_dependents`, and `doctor` reports the situation as a warning, not an
  error. Decide, then act — the tool will not silently make that call for you.
- **A cancelled prerequisite is not "complete".** Dependents stay blocked until
  the edge is removed or the prerequisite is restored and completed.
- **`--force` is refused when `blocked_reason` is set**, and it never permits
  entering `review` or `done` while a prerequisite is unfinished. The
  unfinished-prerequisite override applies only to entering `in_progress`.
- **Notes do not touch `updated_at` or `version`.** They are events; a note will
  never make a task look freshly modified in a sort by `updated_at`.
- **`migrate` and `backup` do not create parent directories** (only `init`
  does), and `backup` refuses to overwrite an existing file.

## Verification

Prove the board works end to end — every step's exit code should be `0`:

```bash
tack init --db /tmp/board.sqlite3
export TACKBOARD_DB=/tmp/board.sqlite3 TACKBOARD_AGENT=check
tack create "Prerequisite" --status ready --json          # -> T-000001
tack create "Dependent" --status ready --depends-on T-000001 --json
tack claim T-000001 --json                                # held by check
tack complete T-000001 --json                             # -> changed:true
tack show T-000002 --json                                 # dependency_ready:true
tack next --claim --json                                  # -> claims T-000002
tack board --brief                                        # counts in one line
tack doctor --json                                        # ok:true, no warnings
```

If `claim` on the dependent succeeds only after the prerequisite is `done`, and
`doctor` reports a clean board, the readiness, dependency and claim machinery is
all working.
