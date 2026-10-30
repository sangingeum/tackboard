Global options (`--db`, `--json`, `--quiet`, `--verbose`, `--version`,
`--help`) work **before or after** the subcommand.

```text
tack init [--db PATH]
tack migrate | tack backup PATH | tack doctor [--json]
tack schema [DOCUMENT] [--json]
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
tack note TASK_ID TEXT [--ref KIND:VALUE ...]
tack reject TASK_ID --reason TEXT [--if-version N]
tack memo [TEXT | --file PATH | - | --edit | --append TEXT | --rev N
           | --history | --diff [N [N]] | --restore N | --clear]
          [--if-rev N] [--reason TEXT] [--force] [--dry-run] [--limit N]
tack claim TASK_ID [--force --reason TEXT] [--if-version N]
tack release TASK_ID [--force --reason TEXT] [--if-version N]
tack claims [--agent A] [--older-than 2h] [--stale]
tack agents [--json]
tack recover TASK_ID --reason TEXT [--if-version N]
tack next [--agent A] [--claim] [--stage work|review] [--label L]
          [--min-priority P] [--assignee A|--all-assignees] [--full]
          [--different-from-author] [--force --reason TEXT]
tack depend add TASK_ID --on PREREQUISITE_ID [--force --reason TEXT] [--if-version N]
tack depend remove TASK_ID --on PREREQUISITE_ID [--if-version N]
tack deps TASK_ID | tack dependents TASK_ID
tack graph [TASK_ID] [--format json|dot]
tack history [TASK_ID] [--limit N] [--since EVENT_ID] [--follow]
tack plan apply FILE|- [--dry-run] [--agent A]
tack serve [--host H] [--port P] [--allow-remote] [--allowed-host HOST ...]
tack guide
```

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
