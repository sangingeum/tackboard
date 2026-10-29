#!/usr/bin/env python3
"""Planner, workers and a reviewer — one board, four roles, one DAG.

Run it:

    uv run python examples/planner-workers-reviewer/planner_workers_reviewer.py

The shape:

1. **Planner** writes a plan file and applies it in one atomic command, so the
   DAG exists before anyone starts: ``parser`` <- ``cli`` <- ``docs``.
2. **Workers** take work with ``next --claim`` — never ``list`` plus ``claim`` —
   do it, and move it to ``review`` rather than completing it themselves.
3. **Reviewer** takes review work with ``next --stage review --claim
   --different-from-author``, so it never reviews its own in-progress work.
4. The reviewer rejects one task with a reason. Rejection moves it back to
   ``in_progress`` *claimed by the reviewer*, so the reviewer releases it and
   the worker re-claims it, fixes it, and sends it back to review.
5. The reviewer accepts the rest, which is what unblocks the next task — the
   workers' loop stops when nothing is eligible.

Everything lives in a temporary directory that is removed at the end.
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

PLAN = {
    "tasks": [
        {"key": "parser", "title": "Ship the parser", "status": "ready"},
        {"key": "cli", "title": "Wire the CLI", "depends_on": ["parser"], "status": "ready"},
        {"key": "docs", "title": "Document the CLI", "depends_on": ["cli"], "status": "ready"},
    ]
}


def tack(db: Path, actor: str, *argv: str) -> dict[str, Any]:
    completed = subprocess.run(
        [*TACK, *argv, "--json", "--db", str(db)],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "TACKBOARD_AGENT": actor},
    )
    if completed.returncode != 0:
        raise SystemExit(f"tack {' '.join(argv)}: {completed.stdout}{completed.stderr}")
    return json.loads(completed.stdout)


def plan(db: Path, path: Path) -> list[str]:
    path.write_text(json.dumps(PLAN, indent=2) + "\n", encoding="utf-8")
    applied = tack(db, "planner", "plan", "apply", str(path))
    print(f"planner applied {applied['count']} tasks from {path.name}")
    return [entry["id"] for entry in applied["tasks"]]


def work(db: Path, actor: str) -> str | None:
    """Claim one task, do it, hand it to review. Returns the task id, or None."""
    task = tack(db, actor, "next", "--claim")["task"]
    if task is None:
        return None
    tack(db, actor, "note", task["id"], f"{actor} implemented it")
    tack(db, actor, "status", task["id"], "review")
    print(f"  {actor} sent {task['id']} ({task['title']}) to review")
    return str(task["id"])


def review_once(db: Path, reviewer: str, *, reject_first: bool) -> str | None:
    """Take one reviewable task; reject the first, accept the rest."""
    reviewable = tack(
        db, reviewer, "next", "--stage", "review", "--claim", "--different-from-author"
    )["task"]
    if reviewable is None:
        return None
    if reject_first:
        tack(db, reviewer, "reject", reviewable["id"], "--reason", "missing tests")
        print(f"  {reviewer} rejected {reviewable['id']}: missing tests")
        # Rejection claims the task for the reviewer; hand it back.
        tack(db, reviewer, "release", reviewable["id"])
        print(f"  {reviewer} released {reviewable['id']} back to the worker")
    else:
        tack(db, reviewer, "complete", reviewable["id"])
        print(f"  {reviewer} accepted {reviewable['id']}")
    return str(reviewable["id"])


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="tack-review-"))
    try:
        db = root / "board.sqlite3"
        tack(db, "planner", "init")
        plan_ids = plan(db, root / "plan.json")
        print(f"board holds {plan_ids}")

        rejected = False
        for round_number in range(1, len(plan_ids) + 1):
            print(f"\nround {round_number}")
            while work(db, "worker") is not None:
                pass

            if not rejected:
                reviewed = review_once(db, "reviewer", reject_first=True)
                if reviewed is not None:
                    rejected = True
                    # The worker picks the rejected task back up and resubmits it.
                    tack(db, "worker", "claim", reviewed)
                    tack(db, "worker", "note", reviewed, "added the missing tests")
                    tack(db, "worker", "status", reviewed, "review")
                    print(f"  worker re-claimed {reviewed} and resubmitted it")
                    review_once(db, "reviewer", reject_first=False)

            while review_once(db, "reviewer", reject_first=False) is not None:
                pass
            if not tack(db, "worker", "board", "--brief")["counts"]["eligible"]:
                break

        counts = tack(db, "reviewer", "board", "--brief")["counts"]
        print(f"\nfinal counts: {counts}")
        if counts["review"] or counts["ready"] or counts["in_progress"]:
            raise SystemExit("the run left work on the board; the example is out of date")
        return 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
