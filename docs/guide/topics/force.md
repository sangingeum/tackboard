`--force` is an audited, exhaustive list, not a general bypass. It requires
`--reason TEXT` (at most 1024 characters) and is recorded as an event carrying
`forced: true` and the reason. It is accepted only for these cases:

- taking over or releasing another agent's claim (`claim --force`,
  `release --force`);
- starting work whose only unfinished blockers are cancelled prerequisites
  (entering `in_progress`);
- adding a prerequisite that is unfinished to a task already `in_progress` or
  `review` (`depend add --force`);
- recovering an `in_progress` task that lost its claim (`recover`, which is a
  thin wrapper over `release --force`);
- replacing, restoring or clearing a non-empty memo without `--if-rev`
  (`memo --force --reason`).

It is refused when `blocked_reason` is set, and it never permits entering
`review` or `done` with an unfinished prerequisite — the unfinished-prerequisite
override applies only to entering `in_progress`. If your case is not on the
list, the answer is a different command, not a bigger hammer. `tack recover`
adds no force case of its own: a claim the caller already holds is refused
(use plain `release`), and a healthy unclaimed task is an idempotent no-op.
