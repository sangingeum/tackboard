# Planner, workers, reviewer

A small DAG, two workers and a reviewer with a rejection round.

```bash
uv run python examples/planner-workers-reviewer/planner_workers_reviewer.py
```

The planner writes a plan file and applies it atomically:

```json
{"tasks": [
  {"key": "parser", "title": "Ship the parser", "status": "ready"},
  {"key": "cli", "title": "Wire the CLI", "depends_on": ["parser"], "status": "ready"},
  {"key": "docs", "title": "Document the CLI", "depends_on": ["cli"], "status": "ready"}
]}
```

```bash
tack plan apply plan.json --json     # one transaction: tasks, labels, edges
tack plan apply plan.json --dry-run  # validate and report; writes nothing
```

Then the roles take turns, and the board decides the order:

- **workers** call `next --claim`, which only ever returns a task whose
  prerequisites are `done` — so `cli` cannot start before `parser` is accepted;
- a worker hands work over with `status <id> review` instead of completing it;
- the **reviewer** calls `next --stage review --claim --different-from-author`,
  so it never reviews a task whose most recent `in_progress` entry was its own;
- rejecting is `reject <id> --reason "..."`. It moves the task back to
  `in_progress` and hands the claim back to the worker who last worked it, so
  the worker fixes it and resubmits without a release or a re-claim;
- accepting is `complete`, which is what makes the next task eligible.

The script asserts the board ends empty of open work — if the example ever
drifts from the CLI's rules, that assertion fires instead of printing a happy
story.

Requires: the `tack` CLI (`TACK_CMD` overrides the command), nothing else.
