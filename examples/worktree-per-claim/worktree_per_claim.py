#!/usr/bin/env python3
"""Three agents, one repository, a worktree per claim.

Run it:

    uv run python examples/worktree-per-claim/worktree_per_claim.py

What it shows, in order:

1. A board with three ready tasks and two agents working the same repository.
2. Agent A claims a task, and the claim buys it a git worktree on a branch
   named from the task id — so the task's work cannot collide with anyone
   else's files.
3. Agent B tries to claim the *same* task and is refused (exit 5,
   ``claim_conflict``) — the interesting half of atomic claiming.
4. Agent A does the trivial edit, commits, leaves a note carrying the commit
   and the path as typed refs, completes the task, and removes the worktree.
5. Agent B's next claim succeeds, because completion changed what is eligible.

The board and the repository live in a temporary directory that is removed at
the end; nothing touches your working tree.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

TACK = tuple(os.environ.get("TACK_CMD", "").split()) or ("tack",)
BRANCH_PREFIX = "task/"


class Board:
    """The board, as the agents see it: the CLI's JSON documents."""

    def __init__(self, db: Path, actor: str) -> None:
        self.db = db
        self.actor = actor

    def __call__(self, *argv: str, check: bool = True) -> tuple[int, dict[str, Any]]:
        completed = subprocess.run(
            [*TACK, *argv, "--json", "--db", str(self.db)],
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "TACKBOARD_AGENT": self.actor},
        )
        document = json.loads(completed.stdout) if completed.stdout.strip() else {}
        if check and completed.returncode != 0:
            raise SystemExit(f"{' '.join(argv)} failed: {completed.stdout}{completed.stderr}")
        return completed.returncode, document


def git(repo: Path, *argv: str, check: bool = True) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *argv], capture_output=True, text=True, check=False
    )
    if check and completed.returncode != 0:
        raise SystemExit(f"git {' '.join(argv)} failed: {completed.stderr}")
    return completed.stdout.strip()


def make_repository(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "agents@example.invalid")
    git(repo, "config", "user.name", "Example Agent")
    (repo / "NOTES.md").write_text("# Notes\n", encoding="utf-8")
    (repo / ".gitignore").write_text(".worktrees/\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "chore: start the repository")
    return repo


def work(ref: str, repo: Path, task_id: str) -> str:
    """Do the trivial edit inside the task's own worktree and commit it."""
    worktree = repo / ".worktrees" / task_id
    git(repo, "worktree", "add", "-q", str(worktree), "-b", f"{BRANCH_PREFIX}{task_id}")
    notes = worktree / "NOTES.md"
    notes.write_text(
        notes.read_text(encoding="utf-8") + f"- {task_id}: implemented by {ref}\n",
        encoding="utf-8",
    )
    git(worktree, "add", "NOTES.md")
    git(worktree, "commit", "-q", "-m", f"feat: record {task_id}")
    return git(worktree, "rev-parse", "--short", "HEAD")


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="tack-worktree-"))
    try:
        repo = make_repository(root)
        board = Board(root / "board.sqlite3", actor="")
        board("init")

        alice = Board(board.db, actor="alice")
        bob = Board(board.db, actor="bob")

        for title in ("Write the parser", "Write the CLI", "Write the docs"):
            _, document = alice("create", title, "--status", "ready")
            print(f"created {document['task']['id']}  {title}")

        # 1. Alice takes work atomically.
        _, claimed = alice("next", "--claim")
        task = claimed["task"]
        assert task is not None
        task_id = task["id"]
        print(f"\nalice claimed {task_id} ({task['title']}) as {task['claimed_by']}")

        # 2. The claim buys a worktree on a branch named from the task id.
        sha = work("alice", repo, task_id)
        print(f"alice worked in .worktrees/{task_id} on {BRANCH_PREFIX}{task_id} -> {sha}")

        # 3. Bob tries the same task: the board refuses him, by name.
        code, refusal = bob("claim", task_id, check=False)
        error = refusal.get("error", {})
        print(
            f"bob tried to claim {task_id}: exit {code}, "
            f"{error.get('code')} — {error.get('message')}"
        )

        # 4. Evidence as refs, then completion.
        alice(
            "note",
            task_id,
            "implemented; see the commit",
            "--ref",
            f"commit:{sha}",
            "--ref",
            "path:NOTES.md",
        )
        _, finished = alice("complete", task_id)
        print(f"alice completed {task_id}: changed={finished['changed']}")
        git(repo, "worktree", "remove", "--force", str(repo / ".worktrees" / task_id))
        print(f"worktree .worktrees/{task_id} removed; branch {BRANCH_PREFIX}{task_id} kept")

        # 5. Bob now gets different work.
        _, next_task = bob("next", "--claim")
        print(f"bob's next claim: {next_task['task']['id']} ({next_task['task']['title']})")

        _, brief = alice("board", "--brief")
        print(f"\nboard now: {brief['counts']}")
        return 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
