- **`--edit` refuses without a terminal** (exit 2, naming `--file` and `-`).
  Use `--append`, `--file` or `-`; an unattended agent must never wait on an
  editor prompt.
- **A replace on a non-empty memo needs `--if-rev N` or `--force --reason`.**
  A bare `tack memo "text"` exits 2. Every revision is kept, so the guard is
  about not clobbering a concurrent writer, not about losing data.
- **A memo revision conflict is not retryable.** `stale_rev` exits 5 with
  `retryable: false` and `details: {expected, current}`, like `stale_version`:
  re-read the memo and re-decide, or `--force --reason`.
- **`--reason` is required on a memo replace, restore or clear** and optional on
  an append. On tasks it stays `--force`-only.
- **A memo write never touches a task.** It appears in the board-wide `history`
  with a null task id, so `tack history <TASK_ID>` and `show` are unaffected.
- **An unmigrated board has no memo.** Opening it is exit 6 until
  `tack migrate` runs.

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
