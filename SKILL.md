---
name: tackboard
description: Use for agent task coordination on a per-project board via the `tack` CLI — create, claim, note and complete tasks, model dependency DAGs, read derived readiness, and select work atomically with `next --claim`. Use when several agents share one project's work and need a stable machine-readable contract; do NOT use for note-taking, file search (use rg) or git history.
version: 0.1.1
author: sangingeum, Hermes Agent
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
  are different things and are stored separately.
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
   concurrently get two different tasks, or one gets `null`.
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
this is deliberate: `ready` is the decision that work may start.

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
- `board`, `list` and `next` **omit `description`** unless `--full` is passed —
  that is the token-economy default. `show` and mutation results always include
  it.
- Error `details` carry machine-readable keys where they exist (a length
  violation gives `field` / `max` / `unit` / `actual`; a claim conflict gives
  `claimed_by`) — read them instead of parsing the message text.
- Mutations return `{"schema_version":1,"changed":<bool>,"task":{...}}`.
  `changed:false` means the call was an idempotent no-op: nothing was written
  and no event was recorded.
- `--brief` (one line of counts) and `--count` (`key=value` per line) are the
  cheapest way to ask "what does this board look like?"; combining them is a
  usage error. Both accept the same filters as `board`, and all counts are
  computed over the **filtered** set.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | success, including idempotent no-ops and an empty `next` |
| 1 | unexpected internal error |
| 2 | usage error: bad option combination, or no actor |
| 3 | task, project, or database not found / not configured |
| 4 | validation error, invalid transition, or dependency cycle |
| 5 | concurrency conflict: claim held, stale version, lock timeout (`retryable: true`) |
| 6 | database, schema, or migration error |

## Best practices

1. **Claim before you work; never work a task you do not hold.** The claim is
   what tells every other agent the task is taken. A task you are editing
   without holding is a task two agents are about to collide on.
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
    recovering an `in_progress` task with no claim, transferring an assignee).
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
  blocked; `doctor` reports that as a warning, not an error. Decide, then act —
  the tool will not silently make that call for you.
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
