**Rejections are derived, never stored.** `tack reject <id> --reason TEXT`
moves a task from `review` back to `in_progress` through the ordinary
transition function and appends one `task.rejected` event whose payload is
`{from:"review", to:"in_progress", reason}`. It is **not** a forced
transition — there is no `forced` key and `--force` is not accepted. The
reason is mandatory. `rejections` (integer, `0` when never rejected) and
`last_rejection_reason` (string or `null`) are counted from those events on
every read, so they cost no column and cannot drift from history.

Rejecting moves the task to `in_progress`, which always carries a claim. The
claim is handed back to the agent who most recently moved the task into
`in_progress` — the author — not to the rejecting reviewer; the event stays
attributed to the reviewer. Rejecting your own work leaves the claim with you.
A review task already claimed by a third party is still refused with a claim
conflict, so release it first. `task.rejected` events are not counted as
`in_progress` entries when deriving the author, so a second rejection still
goes back to the same author.

**Reviewer separation is opt-in.** `next --stage review --claim
--different-from-author` drops candidates whose most recent `in_progress`
entry was made by the calling agent. It is valid only with `--stage review`
and `--claim` (otherwise exit 2), runs inside the same `BEGIN IMMEDIATE`
select-and-claim transaction as the claim, and leaves the default selection
untouched.
