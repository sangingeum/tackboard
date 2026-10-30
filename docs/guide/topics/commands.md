Global options (`--db`, `--json`, `--quiet`, `--verbose`, `--version`,
`--help`) work **before or after** the subcommand.

<!-- COMMAND INVENTORY -->

Every task or memo mutation takes `--agent` or falls back to `TACKBOARD_AGENT`
(the explicit option wins). Read-only commands (`board`, `list`, `show`,
`deps`, `dependents`, `graph`, `history`, `claims`, `doctor`, `schema`) need no
actor, and so do the memo's reads (`memo`, `memo --rev`, `memo --history`,
`memo --diff`) and `memo --dry-run`; every memo write needs one.
`next` needs one even without `--claim`, because selection is agent-specific.
`next --claim` prints a single-task document where plain `next` prints the
`tasks` container — see *The JSON contract*.

`tack claims --stale` is a convenience filter over the same listing: it keeps
claims at least as old as the configured threshold, which is the optional
`[tackboard] stale_claim_after` key in `.tackboard.toml` and defaults to `2h`.
`--older-than` still works and overrides the threshold for that call; an
unparseable configured value is a validation error (exit 4), never a silent
fallback. `tack recover TASK_ID --reason TEXT` is the one-command way to
resolve a dead claim: it is a thin wrapper over
`release --force --reason`, mapping only to the enumerated force cases (a claim
held by another agent, or an `in_progress` task that lost its claim). It adds
no transition and no force case, so a claim the caller already holds is refused
by the existing policy (use plain `release`), and a healthy unclaimed task is
an idempotent no-op.

`tack plan apply FILE` creates a whole dependency DAG in one command — the file
format and its guarantees are in `tack guide --topic plans`.

### Statuses and transitions

`backlog → ready | cancelled`, `ready → backlog | in_progress | cancelled`,
`in_progress → ready | review | done | cancelled`,
`review → in_progress | done | ready | cancelled`,
`done → ready` (reopen), `cancelled → backlog` (restore).

There is no stored `blocked` status — blocking is always derived. Setting a task
to the status it already has is a **no-op success** (`"changed": false`, no
version bump); the shortcuts behave the same way (`complete` on `done`,
`reopen` on `ready`, `cancel` on `cancelled`, `restore` on `backlog`).

Be aware that `claim` works on a **`ready`**, **`review`** or
**`in_progress`** task. On `ready` it also moves the task to `in_progress`;
on `review` it takes the review without changing its status; on
`in_progress` it recovers a task whose claim was lost. A task created in
`backlog` (the default) must be moved to `ready` before anyone can claim it —
this is deliberate: `ready` is the decision that work may start. Claiming
does not require being the assignee: the assignment is a routing note, so any
agent may claim a claimable task, and the claim names whoever takes it.

**Entering `in_progress` claims the task; leaving it releases the claim.** A
transition to `in_progress` sets `claimed_by`/`claimed_at` to the acting agent —
the same state `claim` produces — however you got there: `status <id>
in_progress`, `claim`, `next --claim`, or the `--force` path that overrides an
unfinished prerequisite. Leaving `in_progress` — to `review`, `done`, or back
to `ready`, and likewise `complete` from `in_progress` — clears them
(`claimed_by: null`). So a task moved to `in_progress` by `status` is held, not
free, and a claim is not always an explicit `claim` call. None of those paths
touches the assignment: `assignee` is written only by a routing command.
