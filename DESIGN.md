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
  web.py           read-only HTTP server (board, DAG, task detail, /api/*)
  templates/       server-rendered HTML
  static/          vendored assets
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
without rewriting any task's status. A reopened prerequisite also reports its
active (`in_progress`/`review`) dependents so a human or agent can act on them.

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
   the transition table, prerequisite gating, claim ownership, assignment
   transfer, and the audited `--force` cases, and returns a *plan* — a set of
   intended row changes — or reports "no change".
5. Apply the plan: update the task row with a compare-and-set on `version`,
   insert or delete the claim row, set or clear `completed_at`.
6. Append exactly one canonical history event, with the reason attached when
   `--force` was used.
7. Commit; on any exception the whole transaction rolls back, so a mutation and
   its event can never disagree.

The claim/status policy table of the specification is therefore implemented
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

## 5. Transactions, history and idempotence

- **One transaction per mutation**, opened with `BEGIN IMMEDIATE` explicitly.
  Maintenance operations (`init`, `migrate`, `backup`) are not task mutations
  and write no task events.
- **Every mutation appends its history event(s) inside that transaction.**
  `history.record_event` never opens a transaction of its own; it writes into
  the caller's. The `events` table is append-only, enforced by SQLite triggers
  that abort `UPDATE` and `DELETE`. Events carry a UTC timestamp, an actor, an
  event type, the task id where applicable, and a compact JSON payload holding
  the changed fields.
- **Versioning.** `version` starts at 1 and increments once per successful
  mutation of a task record or its own prerequisite set — including claims and
  releases, since a claim changes the task's state. Notes are event-only and
  change neither `version` nor `updated_at`. A caller may pass `--if-version N`
  to make a mutation conditional; the comparison happens inside the write
  transaction, so it cannot be raced.
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

Exit codes and JSON documents are the parts an agent is allowed to depend on.
Human output may be re-worded.

## 8. Where things are enforced

| Concern | Implementation | Enforced by |
|---|---|---|
| Status transition table | `models.ALLOWED_TRANSITIONS` | `service._plan_status` |
| Prerequisite gating | `models.PREREQUISITE_GATED_STATUSES` | `service._plan_status` |
| Claim policy, takeover, assignment transfer | `service._plan_claim`, `_plan_release` | `apply_transition` |
| `--force` scope and auditing | `_check_force_reason`, plan flags | `apply_transition`, `dependencies.add_dependency` |
| Derived readiness | `service.derived_flags` | every projection and filter |
| Cycle detection | `dependencies.find_cycle`, `_prerequisite_path` | `add_dependency` |
| Atomic select-and-claim | `claims.next_task` | one `BEGIN IMMEDIATE` around both steps |
| Version arithmetic | compare-and-set on `version` | `_update_task_row`, `update_task` |
| Append-only history | triggers plus `record_event` | `schema._DDL` |
| Identifier/shape rules | `models` limits, `config.AGENT_ID_RE` | `service` validators |
| JSON envelope shape | `cli` renderers | `errors.TackError.to_json` |

## 9. Testing strategy

The suite is pytest, with `filterwarnings = ["error"]` so a deprecation cannot
be ignored into the next release. Areas map one-to-one onto the specification's
test section: task lifecycle, project selection and database safety,
dependencies, claims and real multi-process concurrency (racing OS processes
against one task), the CLI contract, and integrity checks including seeded
corruption. Every test owns a temporary database and never touches a
developer's board.

Two habits are worth keeping:

- Concurrency is tested with real processes, not threads, because the locking
  behaviour under test is SQLite's.
- The CLI is exercised both in process (fast, for the bulk of the matrix) and
  once through the installed console script and the `python -m` form, so a
  packaging regression cannot hide behind an importable module.

## 10. Deliberate non-goals

No user accounts or authorization (agent identifiers are advisory labels);
no cross-project dependencies; no automatic lease expiry or heartbeats; no
bulk import; no web mutations; no automatic status change when a dependency
changes; no deletion of tasks (cancel, never delete). The design should stay
extensible, but none of these belong in the current version.
