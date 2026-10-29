# tackboard — design

Architecture notes for contributors. This document describes how the tool is
built and why; `README.md` describes how to use it.

## 1. The shape of the thing

`tackboard` is a Python package with one storage engine (SQLite, via the
standard library) and three consumers of a single domain layer:

```text
src/tackboard/
  __init__.py      package metadata
  __main__.py      `python -m tackboard`
  cli.py           argument parsing, rendering, exit codes
  config.py        project/database resolution, actor resolution
  db.py            connections, transactions, the busy/retry policy
  schema.py        DDL, database identity, migrations, backup, diagnostics
  models.py        enums, value limits, row dataclasses
  service.py       task CRUD, THE transition function, notes, list/count
  dependencies.py  edges, cycle detection, readiness, graph export
  claims.py        claim/release, the atomic `next --claim` selector
  history.py       append-only events: write and read
  memo.py          the board-level memo: revisions, guard, append rules
  views.py         read models (board, graph, task detail, memo) for CLI and web
  web.py           read-only HTTP server (board, DAG, task detail, /api/*)
  templates/       server-rendered HTML
  static/          committed CSS and JavaScript (no build step, no CDN)
tests/             pytest suite, mirrors the module layout
```

Two rules keep the layers apart:

1. **No domain logic outside the service layer.** The CLI and the web handlers
   parse options, call library functions, and render results. They contain no
   transition table, no dependency or cycle logic, no claim policy, no version
   arithmetic and no SQL. If an interface needs something the service layer does
   not expose, a function is added to the service layer and used from there.
2. **No interface-specific rules in the service layer.** Nothing in
   `service.py`, `claims.py`, `dependencies.py` or `history.py` knows that a
   command line exists. They raise typed exceptions and return plain
   dictionaries.

`memo.py` sits beside the service layer on the same footing: it owns the
board-level memo's revision table and its rules, holds no interface logic, and
raises typed exceptions returning plain dictionaries. Section 11 describes it.
The CLI and the web layer read it through one read model,
`views.memo_document`.

## 2. One database per project

A project *is* a SQLite file. Consequences, all deliberate:

- Task IDs are unique within a file, not globally. IDs are `T-` plus the
  sequence zero-padded to six digits, generated inside the creating write
  transaction from a monotonic `metadata.next_sequence` counter, so the counter
  only increases and IDs are never reused. Sorting and tie-breaking always use
  the integer `sequence`, never the string ID.
- Dependencies cannot cross databases, and no command attaches, merges or
  copies project databases. There is no cross-project view by design.
- Identity is explicit: `PRAGMA application_id` carries a fixed magic number
  and `PRAGMA user_version` carries the schema version. `init` refuses to
  initialize over a non-empty file that lacks the magic number; every other
  command refuses to open a file whose magic number or version it does not
  recognize. Nothing auto-migrates, so a newer build can never silently
  reinterpret an older file.
- Resolution order for the target file is `--db`, then `TACKBOARD_DB`, then the
  nearest `.tackboard.toml` walking up from the current directory. This lives in
  exactly one function (`config.resolve_database`), and relative paths are
  resolved against the current directory for the first two and against the
  config file's own directory for the third.
- Only `init` creates parent directories, and only `init` creates a database. A
  typo in `--db` fails loudly instead of leaving an empty file behind.

## 3. Stored status, derived readiness

The database stores one workflow status per task. It stores **no** notion of
"blocked", "ready to start", or "claimable" — those are computed on every read
from the graph, the claims table, and the explicit blocker text:

| Derived flag | Computed as |
|---|---|
| `dependency_ready` | every direct prerequisite is `done` |
| `eligible` | status `ready`, `dependency_ready`, no explicit blocker, no active claim |
| `reviewable` | status `review`, no active claim, no explicit blocker, `dependency_ready` |
| `blocked` | non-terminal status and at least one blocker entry |

Blockers are reported as structured entries: `prerequisite_unfinished`,
`prerequisite_cancelled`, or `explicit`. Readiness is evaluated over *direct*
prerequisites only; a `done` prerequisite is authoritative even if one of *its*
own prerequisites is later reopened. This is intentional — transitive
re-blocking would make a completed task's status depend on distant history.

Because readiness is derived, a dependency change takes effect immediately
without rewriting any task's status. A `reopen` that changes anything also
reports its active (`in_progress`/`review`) dependents in the document's
`active_dependents` key, so a human or agent can act on them.

The derived flags are computed in one function (`service.derived_flags`) and
used by the task projection, the listing filters, the count contract, the
`next` selector and `doctor`.

## 4. One transition function

Every status change in the tool — `status`, the `complete`/`reopen`/`cancel`/
`restore` shortcuts, `claim`, `release`, and `next --claim` — passes through
`service.apply_transition(conn, task_id, *, actor, mode, ...)`.

The function has one shape whatever the caller:

1. Open (or join) one `BEGIN IMMEDIATE` write transaction.
2. Load the task, check `--if-version` against the stored version.
3. Load the active claim and the direct prerequisite states.
4. Run the policy for the mode (`status`, `claim`, `release`). The policy checks
   the transition table, prerequisite gating, claim ownership, and the audited
   `--force` cases, and returns a *plan* — a set of intended row changes — or
   reports "no change". A claim never plans an assignment write: the assignment
   is a routing note, written only by `update_task`.
5. Apply the plan: update the task row with a compare-and-set on `version`,
   insert or delete the claim row, set or clear `completed_at`.
6. Append exactly one canonical history event, with the reason attached when
   `--force` was used.
7. Commit; on any exception the whole transaction rolls back, so a mutation and
   its event can never disagree.

The claim/status policy is therefore implemented
once. `claim`, the shortcuts, and the CLI are all thin wrappers that choose a
mode and pass options through; there is no second place where a transition can
be approved or a claim can be taken.

`mode="claim"` is also what `claims.next_task` calls after choosing a
candidate, which is what makes `next --claim` atomic: the selection query and
the transition run inside the *same* transaction, so two racing agents cannot
both see the same task as claimable. It is never implemented as "list, then
claim".

`--force` is deliberately narrow. Three enforcement points keep it honest: a
force that requires a reason but did not get one is a validation error; a force
used where none of the enumerated cases applies is a validation error after the
plan is built; and every accepted force is recorded in the event payload as
`{"forced": true, "reason": ...}`. `--force` never bypasses "a task claimed by
another agent cannot be modified" — the claim must be taken over or released
first.

`reject` is the one reason-bearing shortcut that is **not** a forced
transition: it is `review -> in_progress` through this same function, with a
mandatory `--reason` recorded in the `task.rejected` payload and no `forced`
key. A reason on a transition therefore has two distinct meanings — the audit
trail of an override, and the explanation of ordinary review feedback — and
only the first is force. `rejections` and `last_rejection_reason` are derived
from those events on read; nothing about a rejection is stored.

## 5. Transactions, history and idempotence

- **One transaction per mutation**, opened with `BEGIN IMMEDIATE` explicitly.
  Maintenance operations (`init`, `migrate`, `backup`) are not task mutations
  and write no task events.
- **Every mutation appends its history event(s) inside that transaction.**
  `history.record_event` never opens a transaction of its own; it writes into
  the caller's. The `events` table is append-only, enforced by SQLite triggers
  that abort `UPDATE` and `DELETE`. Events carry a UTC timestamp, an actor, an
  event type, the task id where applicable, and a compact JSON payload holding
  the changed fields. A board-level memo write appends its own event the same
  way, with `task_id` null and a payload that names the revision it created
  (section 11) — never the memo body, so `history` stays a log rather than a
  document store.
- **Versioning.** `version` starts at 1 and increments once per successful
  mutation of a task record or its own prerequisite set — including claims and
  releases, since a claim changes the task's state. Notes are event-only and
  change neither `version` nor `updated_at`. A caller may pass `--if-version N`
  to make a mutation conditional; the comparison happens inside the write
  transaction, so it cannot be raced. The board-level memo keeps a separate
  counter, `rev`, with its own guard, `--if-rev N` (section 11); a memo write
  changes no task's `version` and no task's `updated_at`.
- **Idempotence is a first-class result.** Setting a task to the status it
  already has, claiming a task already claimed by the same agent, re-adding an
  existing dependency, and removing an absent one all return `changed: false`
  with no version bump and no event.

Timestamps are UTC, fixed-width ISO-8601 with a `Z` suffix, so they sort
lexicographically; ties are broken by `sequence` (tasks) or the event ID.

## 6. The SQLite concurrency model

Several agent processes share one file, so the connection setup is part of the
contract, not an implementation detail:

- **`isolation_level=None`.** Python's `sqlite3` otherwise wraps statements in
  implicit transactions and never issues `BEGIN IMMEDIATE`, which is what makes
  "read, decide, write" safe. With autocommit off in Python and transactions
  managed explicitly, the tool issues the locking statement itself.
- **`BEGIN IMMEDIATE` for every mutation.** The write lock is taken up front,
  so two writers serialise instead of one discovering the conflict halfway
  through. `db.write_transaction` reuses an already-open transaction, which is
  how a read-decide-write sequence stays inside one lock.
- **`BEGIN` (deferred) for multi-query reads.** `board`, `graph`, `show`,
  `deps`, `dependents`, `claims` and `history` render from one consistent
  snapshot rather than from a sequence of statements that could straddle
  another process's commit.
- **Busy timeout, then a bounded retry.** Connections set a busy timeout
  (5 s by default) and `write_transaction` retries `SQLITE_BUSY` a small,
  bounded number of times with backoff before raising `LockedError` — exit 5
  with `retryable: true`. There is no unbounded retry loop.
- **WAL journal mode**, enabled by `init` and persisted as a database property,
  so readers never block the writer. The standard consequence applies: a
  database in WAL mode has `-wal` and `-shm` sidecars, which is why they are
  gitignored and why a consistent copy is taken with the backup API rather than
  `cp`.
- **Foreign keys on, on every connection**, with `ON DELETE CASCADE` for
  labels and claims and `ON DELETE RESTRICT` for dependencies; `CHECK`
  constraints cover the enum columns and the self-dependency case.
- **Transactions stay short** — no rendering, no I/O, no waiting on anything
  except SQLite while a write transaction is open.

Read-only *web* connections use `PRAGMA query_only=ON` rather than opening the
file `mode=ro`, because a read-only connection to a WAL database still needs
the `-shm` file and is unreliable without it.

## 7. The CLI as an API contract

The CLI is the primary interface for agents, so its output is a versioned API:

- Every JSON document on stdout carries `"schema_version": 1`, and nothing but
  that document goes to stdout. Contracts grow only by adding fields within a
  version.
- Human output is a rendering of the same data, never a second source of truth.
  `board --brief`, `board --count` and their JSON forms are produced from one
  filtered set and one count function.
- Exit codes are stable: 0 success (including idempotent no-ops and an empty
  `next`), 1 internal, 2 usage or missing actor, 3 not found or not configured,
  4 validation, invalid transition or cycle, 5 concurrency conflict, 6
  database, schema or migration problem. The mapping is mechanical: every
  exception in `errors.py` carries `code`, `exit_code` and `retryable`, and the
  CLI renders whichever one arrives rather than deciding per command.
- With `--json`, failures print the error envelope to **stdout** and leave
  stderr empty (unless `--verbose`), and even argument-parsing failures take
  that route — the parser is subclassed to raise a `UsageError` instead of
  writing its own text.
- Tracebacks appear only under `--verbose`; output is UTF-8 and never carries
  color codes; long-lived output is never written while a transaction is open.
- The board-level memo (section 11) adds its own documents without changing any
  other: `board`, `list`, `next`, `counts`, `deps`, `dependents`, `graph`,
  `doctor` and `history <TASK_ID>` are untouched by a memo write.
- The contract is published as data: one JSON Schema per document shape in
  `schemas/` (shipped as `tackboard/schemas`), with `schema_version` const 1 and
  `additionalProperties: false` so an additive field must be declared. The task
  projection is defined once, in `schemas/task.json`, and referenced by URN.
  `tack schema [DOCUMENT] --json` prints them; CI validates real output against
  them in process and through the installed console script.

Exit codes and JSON documents are the parts an agent is allowed to depend on.
Human output may be re-worded.

## 8. Where things are enforced

| Concern | Implementation | Enforced by |
|---|---|---|
| Status transition table | `models.ALLOWED_TRANSITIONS` | `service._plan_status` |
| Prerequisite gating | `models.PREREQUISITE_GATED_STATUSES` | `service._plan_status` |
| Claim policy and takeover | `service._plan_claim`, `_plan_release` | `apply_transition` |
| `--force` scope and auditing | `_check_force_reason`, plan flags | `apply_transition`, `dependencies.add_dependency` |
| Derived readiness | `service.derived_flags` | every projection and filter |
| Cycle detection | `dependencies.find_cycle`, `_prerequisite_path` | `add_dependency` |
| Batch cycle detection | `dependencies.find_cycle_in_edges` | `plans.load_plan` |
| Atomic plan application | `plans.apply_plan` | one `BEGIN IMMEDIATE` around rows, labels, edges and events |
| Atomic select-and-claim | `claims.next_task` | one `BEGIN IMMEDIATE` around both steps |
| Version arithmetic | compare-and-set on `version` | `_update_task_row`, `update_task` |
| Append-only history | triggers plus `record_event` | `schema._DDL` |
| Whole-document memo guard | `memo._guard` | `replace_memo`, `restore_memo`, `clear_memo` |
| Guard-free append safety | current text read inside the write transaction | `memo.append_memo` |
| Append-only memo revisions | triggers plus `memo._insert_revision` | `schema._MEMO_DDL` |
| Memo revision integrity | `schema._doctor_memo` | `doctor` (schema version 2 only) |
| Identifier/shape rules | `models` limits, `config.AGENT_ID_RE` | `service` validators |
| JSON envelope shape | `cli` renderers | `errors.TackError.to_json` |

## 9. Testing strategy

The suite is pytest, with `filterwarnings = ["error"]` so a deprecation cannot
be ignored into the next release. Areas map one-to-one onto the tool's own
surface: task lifecycle, project selection and database safety,
dependencies, claims and real multi-process concurrency (racing OS processes
against one task), the CLI contract, and integrity checks including seeded
corruption. The board-level memo has its own area: the 1 -> 2 migration on a
board the previous release seeded, a backup round-trip that preserves every
revision, doctor's revision-gap and oversize checks, the size cap, the guarded
whole-document writes against the guard-free append, two real processes racing
to append, the diff shapes, the web parity of `/api/memo`, and a no-leakage
test that pins the task surface byte for byte. Every test owns a temporary database and never touches a
developer's board.

Two habits are worth keeping:

- Concurrency is tested with real processes, not threads, because the locking
  behaviour under test is SQLite's.
- The CLI is exercised both in process (fast, for the bulk of the matrix) and
  once through the installed console script and the `python -m` form, so a
  packaging regression cannot hide behind an importable module.

## 11. The board-level memo

The memo is the project's **standing note**: the rules of engagement, the
read-me-first premise, the operating premise of the board. It is not the
per-session handoff narrative — that record keeps its own home — and the memo is
never mirror-written from it. One fact, one home still holds; the memo is a
document that stands on its own at board scope, read first and edited as a
whole.

It is deliberately not a task. Nothing in `tasks`, `claims` or `dependencies`
reads it, so it can never be claimed, completed, blocked or counted, and it
never appears in `board`, `list`, `next` or `counts`.

**Storage.** `memo_revisions(rev, text, author, created_at, reason)`, created by
the 1 -> 2 migration (`PRAGMA user_version` becomes 2; `application_id` is
unchanged because it identifies the file family, not the schema version). `rev`
is an `INTEGER PRIMARY KEY` assigned as `MAX(rev) + 1` inside the write
transaction, so it is gapless and monotonic; **rev 0 means "no memo yet"** and
the first write creates rev 1. A revision is immutable, which is why its stamp
is `created_at` and why the table is append-only (the same triggers `events`
carries). `--restore` and `--clear` add a revision; nothing is ever updated or
deleted. `backup` needs no code change — it is a whole-file SQLite snapshot, so
the revisions travel with it.

**Two counters, one idiom.** A task has `version` and `--if-version`; the memo
has `rev` and `--if-rev`. The flag, the field and the error code say the same
word: a mismatch is `stale_rev`, exit 5, **not retryable** — like
`stale_version` and `claim_conflict`, and unlike `locked`, because retrying
against a stale snapshot overwrites the other writer's revision. Both stale
guards report the same `details` shape, `{expected, current}`, so a caller can
branch on the numbers instead of parsing the message.

**Append is guard-free; whole-document writes are guarded.** `--append` reads
the current text *inside* its own write transaction and inserts one `\n` before
the new text when the memo is non-empty and does not already end with one. Two
concurrent appends therefore serialise on `BEGIN IMMEDIATE` and both survive: an
append can never erase anyone's content because it never trusts a stale
snapshot. `replace` (positional text, `--file`, `-`, `--edit`), `--restore` and
`--clear` rewrite the whole document, so on a non-empty memo they require
`--if-rev N` or `--force --reason`; the first creation is exempt because there is
nothing to clobber. `--force` is one enumerated case, requires `--reason`, is
refused on an append (there is nothing to force), and is recorded in its event
as `{"forced": true, "reason": ..., "rev": N, "previous_rev": M}` — the audit
names the revision the write displaced, which is the only thing a forced
overwrite actually costs: the displaced revision stays readable via `--rev` and
restorable via `--restore`.

**Audit.** Every accepted write appends exactly one board-level event
(`memo.replaced`, `memo.appended`, `memo.restored`, `memo.cleared`) inside the
same transaction, with `task_id` null and a payload that points at the revision
(`rev`, `bytes`, and the `reason` when one was given). The body lives only in
`memo_revisions`; the log holds the pointer.

**Limits and attribution.** The cap is 64 KiB (65536 UTF-8 bytes), the same
size as a `description`, measured in bytes on the stored text and reported in
the same `details` shape (`field`, `max`, `unit`, `actual`). Attribution is
per revision — author and `created_at` live on the revision, never inline in the
text, so the document stays clean and `--diff` stays meaningful. To see who
added which line, read `--diff` or `--history`.

**Reading and editing.** `tack memo --json` is the cheap head read a session
opens with; `--rev N` reads one revision, `--history` lists them newest first
(with `--limit`), and `--diff` renders a line-based unified diff with three
lines of context from the standard library alone (the project still has no
runtime dependency beyond it). `--edit` refuses outright without a terminal
(exit 2) rather than blocking on an editor prompt, which would cost a whole
dispatched leg; an editor that exits non-zero writes nothing and reports
`changed: false`.

**Availability.** Nothing auto-migrates. A board created by the previous release
is schema version 1 and the v2 build refuses it with exit 6 until `tack migrate`
runs, which writes its usual pre-migrate backup. The memo is therefore
unavailable on an unmigrated board — the intended uniform behaviour, not a gap
to smooth over.

`doctor` validates the memo only when `user_version` is 2: a memo revision table
that is missing or unusable (`memo_table_invalid`), a revision run that is not a
gapless `1..N`
(`memo_revision_gap`, details `{expected, found}`), a stored revision over the
cap (`memo_revision_oversize`, details `{rev, bytes, max}`), and a blank author
(`memo_revision_author`). The memo has no foreign keys, so the existing
foreign-key check is untouched.

`SCHEMA_VERSION` (2) and the release version are independent: the JSON document
`schema_version` stays 1, the database `user_version` is 2, and the release
number is neither.

## 10. Deliberate non-goals

No user accounts or authorization (agent identifiers are advisory labels);
no cross-project dependencies; no automatic lease expiry or heartbeats; no
bulk import; no web mutations; no automatic status change when a dependency
changes; no deletion of tasks (cancel, never delete). The design should stay
extensible, but none of these belong in the current version.

One clarification on that list, so "no bulk import" stays honest as the tool
grows.

**"No bulk import" means no import of foreign data or history.** A plan file is
not an import format for another tracker's rows: it creates new tasks from a
document this board's own author writes, with no ids, timestamps, versions,
events or completed work carried in. It cannot adopt an existing task, cannot
write history as if it had happened, and cannot touch a task it did not create.
Atomic, create-only application of a plan is **in scope**: `tack plan apply`
validates the whole document and commits every row, label and edge in one
`BEGIN IMMEDIATE` transaction (or commits nothing). See section 7 for the CLI
contract and `plans.py` for the rules.

Two notes on where the memo sits against this list. The board-level memo
(section 11) is **in scope** as of schema version 2: it is the project's
standing note, not a task. Pinning a **card** — a per-card `pin` — is
deliberately **deferred**, and the name `pin` is reserved for it so nobody
reaches for it to mean the board's memo.
