# Examples

Three small, runnable adoption recipes. Each one builds its own board and any
repository it needs in a temporary directory, needs no network and no daemon,
and finishes in a couple of seconds. They are meant to be read and adapted, not
just executed.

| Folder | Shows | Run |
|---|---|---|
| [`worktree-per-claim/`](worktree-per-claim/) | one repository, a git worktree per claim, and the refusal when a second agent wants the same task | `uv run python examples/worktree-per-claim/worktree_per_claim.py` |
| [`supervisor-loop/`](supervisor-loop/) | reacting to `history --since CURSOR` events with a persisted, bounded cursor | `uv run python examples/supervisor-loop/supervisor_loop.py` |
| [`planner-workers-reviewer/`](planner-workers-reviewer/) | `plan apply`, workers on `next --claim`, a reviewer that rejects and a worker that re-claims | `uv run python examples/planner-workers-reviewer/planner_workers_reviewer.py` |

Every recipe is also exercised by the test suite (`tests/test_examples.py`), so
they cannot drift from the CLI's actual behaviour.

Requirements: Python 3.12+, the `tack` CLI on `PATH` (or `TACK_CMD` set, e.g.
`TACK_CMD="uv run tack"`), and `git` for the first recipe.
