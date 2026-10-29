# A supervisor loop over the event feed

Watch a board without polling it: read `history --since CURSOR`, react, persist
the cursor, stop.

```bash
uv run python examples/supervisor-loop/supervisor_loop.py
uv run python examples/supervisor-loop/supervisor_loop.py --max-passes 5
```

`tack history --since EVENT_ID --json` is the resumable feed: events with an
`id` greater than the cursor, **oldest first**, with the cursor echoed back. The
script uses it to react to:

- `task.completed` — a dependent may now be startable (it reads `dependents`
  and reports which are `dependency_ready`);
- `task.status_changed` — work moved between `in_progress` and `review`, which
  is a handoff worth naming.

Three properties worth copying:

- **The cursor is a file.** `cursor.json` holds `{"cursor": N}` and is rewritten
  after each pass, so a restart resumes where the last one stopped.
- **Delivery is at-least-once.** An overlapping read can repeat events, so the
  loop de-duplicates on the event id (it asserts the batch has no repeats).
- **The loop is bounded.** `--max-passes` caps the run; with nothing eligible
  and no new events it also stops early. That is what makes it testable rather
  than an always-on daemon.

Each pass also performs one unit of work so the feed has something to show. A
real supervisor would instead react to workers running elsewhere — the reaction
function is the only part that would change.

Requires: the `tack` CLI (`TACK_CMD` overrides the command), nothing else.
