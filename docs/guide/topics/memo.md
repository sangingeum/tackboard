`tack memo` is the board's own **standing note** — the project's operating
premise and rules of engagement, one document at board scope. Read it first;
it is what the board is for, not what one session did.

- It is **not a task.** Nothing claims it, completes it or blocks it, and it
  never appears in `board`, `list`, `next`, `counts`, `deps` or `graph`.
- It is **not the session handoff record.** It is a standing note, read first
  and edited as a whole; the per-session narrative keeps its own home.
- Reads need no actor; a write needs `--agent`/`TACKBOARD_AGENT`; `--dry-run`
  needs none.

```bash
tack memo --json                          # the head revision (cheap: open a session with it)
tack memo --rev 3 --json                  # one revision
tack memo --history --limit 5 --json      # the revisions, newest first
tack memo --diff 6 7 --json               # line diff, three lines of context
tack memo "new text" --reason "why"       # replace (first creation needs no guard)
tack memo --append "one more line"        # append a line (never needs a guard)
tack memo --file notes.md --reason "why"   # replace from a file ('-' reads stdin)
tack memo --restore 2 --reason "rollback" --if-rev 5
tack memo --clear --reason "starting over"
```

**Revisions.** Each write creates the next revision, and every revision is kept:
`--restore` and `--clear` add a revision rather than editing or deleting one.
Revision `0` means "no memo yet". `--history` reports `current_rev`, each
entry's `author`, `created_at`, `bytes` and `reason`.

**The guard.** A replace, restore or clear rewrites the whole document, so on a
non-empty memo it requires **either** `--if-rev N` **or** `--force --reason`; a
bare `tack memo "text"` is exit 2. `--force` is one enumerated case: it records
`{"forced": true, "reason": ..., "rev": N, "previous_rev": M}` in the event, and
is refused on an append (an append is never guarded, so there is nothing to
force). A stale `--if-rev` is exit 5 with code `stale_rev`,
`retryable: false` and `details: {"expected", "current"}`, exactly like a stale
`--if-version` — re-read the memo and re-decide, or force.

**The append rule.** If the current memo is non-empty and does not end with a
newline, one `\n` is inserted before the appended text; nothing else about
either part is transformed (no trimming, no stripping). Two concurrent appends
both survive: the current text is read inside the write transaction.

**`--reason`** is the same concept as a task's: the recorded explanation for the
write. It is required on replace, restore and clear and optional on append.

**Size.** The cap is 64 KiB (65536 UTF-8 bytes), measured in bytes on the
stored text. `--dry-run` runs the real validation and exits with the real code
without writing anything, which is how to probe the cap:

```bash
tack memo --file big.md --reason "draft" --dry-run --json
# -> {"schema_version":1,"dry_run":true,"changed":true,"memo":{...},"max":65536,"bytes":...,"remaining":...}
```

**Attribution is per revision** — the author and timestamp live on the revision,
never inline in the text. `--history` shows who wrote each revision and why;
`--diff` shows what changed between two revisions. Neither attributes
individual lines to an author.

**`--edit`** needs a terminal: without one it exits 2 immediately, naming
`--file` and `-`, rather than blocking on an editor prompt. An editor that
exits non-zero writes nothing (`changed: false`, exit 0).

**Availability.** A board created by an earlier release has no memo table and is
refused with exit 6 until `tack migrate` runs, which writes its usual
pre-migrate backup. Nothing auto-migrates. A board that claims schema version 2
but whose `memo_revisions` table is missing or unusable is refused the same way,
with error code `memo_table_invalid` and a message naming the version-2 columns
it expects.
