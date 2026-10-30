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
    starting work whose only blocker is an unfinished or cancelled prerequisite,
    adding an unfinished prerequisite to a task already in progress or review,
    recovering an `in_progress` task with no claim, replacing, restoring or
    clearing a non-empty memo without `--if-rev`).
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
14. **Recover stale work explicitly.** `tack claims --stale` lists claims past
    the configured threshold (default `2h`), and `doctor` reports each one as a
    `stale_claim` warning; clear one with
    `tack recover <id> --reason "..."` — a thin wrapper over
    `release --force --reason`. An `in_progress` task with no claim at all is an
    inconsistent state — `doctor` reports it, and `recover` resolves it the same
    way. Nothing expires on its own: staleness is visible, and clearing it is
    always one explicit, audited command.
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

18. **Read the memo, and keep it a standing note.** `tack memo --json` is the
    board's operating premise, not a log of what a session did; a project's
    history belongs in its commits, its issues and its handoff record. Append a
    line when the premise genuinely changes, and say why in `--reason`.
    Attribution is per revision: `--history` shows who wrote each revision
    and why; `--diff` shows what changed between two revisions.
