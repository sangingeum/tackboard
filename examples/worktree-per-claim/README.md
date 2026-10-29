# A worktree per claim

Three agents, one repository, and a git worktree per claimed task.

```bash
uv run python examples/worktree-per-claim/worktree_per_claim.py
```

The board is the coordination point; the repository is the shared resource. A
claim is what makes it safe to check out a branch: the task id names the branch
(`task/T-000001`) and the worktree (`.worktrees/T-000001`), so two agents
holding different tasks can never overwrite each other's files.

The script runs the whole cycle end to end:

1. three ready tasks, two agents;
2. Alice claims `T-000001` with `next --claim` and gets a worktree on
   `task/T-000001`;
3. Bob tries to claim the same task and is refused with exit 5
   (`claim_conflict`) — that refusal *is* the lock working;
4. Alice commits, records her evidence as typed refs
   (`note ... --ref commit:<sha> --ref path:NOTES.md`), completes the task and
   removes the worktree;
5. Bob's next claim now returns different work, because completion changed the
   board.

Notes for adapting it:

- Branch and worktree names come from the task id, so they are stable and
  greppable. Prefix them (`task/`) if your branches share a namespace.
- `git worktree remove` cleans the checkout but keeps the branch — that is
  deliberate: the commit is the evidence the note points at.
- Nothing here needs a daemon or the network; the repository and board are
  thrown away at the end.

Requires: `git`, the `tack` CLI (`TACK_CMD` overrides the command, e.g.
`TACK_CMD="uv run tack"`).
