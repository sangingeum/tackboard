#!/usr/bin/env python3
"""Build the demo board used by the README screenshots.

The target database is RESET first: the script deletes ``--db`` and its
``-wal``/``-shm`` companions, then recreates the board from scratch. Running it
twice against the same path therefore produces the same board, not a second
set of tasks.

Every task id, title, edge and status is fixed: the plan file next to this
script (``demo_plan.json``) is applied with ``tack plan apply``, and the few
states a plan cannot express (a claim, a completed task, a review hand-off, an
explicit blocked reason) are reached through ordinary ``tack`` commands. Task
ids are assigned in plan order, so a fresh board always yields T-000001 ..
T-000012.

Usage::

    uv run python scripts/demo_seed.py --db /tmp/tackboard-demo.sqlite3

The script prints the resulting board summary and exits non-zero if the
expected counts or edge total do not hold.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

PLAN = Path(__file__).with_name("demo_plan.json")

MEMO = """# Tackboard web view demo

This board is the deterministic seed used for the README screenshots. It is
built by `scripts/demo_seed.py` and is reset on every run, so edits here do not
survive a rebuild.

It carries one task per status (backlog, ready, in progress, review, done and
cancelled), one explicit blocked task with two unfinished prerequisites, and a
dependency DAG deep enough to show hidden-node elision.
"""

#: Exact counts the finished seed must produce.
EXPECTED_COUNTS = {
    "tasks": 12,
    "backlog": 2,
    "ready": 4,
    "in_progress": 1,
    "review": 1,
    "done": 3,
    "cancelled": 1,
    "eligible": 3,
    "reviewable": 1,
    "blocked": 2,
    "claimed": 1,
}
EXPECTED_EDGES = 10
EXPECTED_STATUSES = ("backlog", "ready", "in_progress", "review", "done",
                     "cancelled")


class SeedError(RuntimeError):
    """The seed did not produce the documented board."""


def run_tack(db: Path, *args: str) -> str:
    """Run one ``tack`` command and return its stdout, failing loudly."""
    command = [sys.executable, "-m", "tackboard", *args, "--db", str(db)]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise SeedError(
            f"{' '.join(command)} failed ({result.returncode}):\n"
            f"{result.stdout}{result.stderr}"
        )
    return result.stdout


def reset_database(db: Path) -> None:
    """Delete the board and its journal companions before rebuilding."""
    for suffix in ("", "-wal", "-shm"):
        candidate = db.with_name(db.name + suffix)
        candidate.unlink(missing_ok=True)


def build(db: Path) -> dict[str, object]:
    """Reset ``db`` and build the demo board; return the final board document."""
    reset_database(db)
    run_tack(db, "init", "--quiet")
    run_tack(db, "plan", "apply", str(PLAN), "--agent", "demo-seed", "--quiet")

    run_tack(db, "memo", MEMO, "--agent", "demo-seed", "--reason", "demo seed",
             "--quiet")

    # Completed work: claim first, then complete (creation cannot start done).
    for task in ("T-000001", "T-000002", "T-000005"):
        run_tack(db, "claim", task, "--agent", "demo-seed", "--quiet")
        run_tack(db, "complete", task, "--agent", "demo-seed", "--quiet")

    # Active work stays claimed so the claimed overlay is exercised.
    run_tack(db, "claim", "T-000004", "--agent", "backend-agent", "--quiet")

    # The review hand-off releases its claim when the status changes.
    run_tack(db, "claim", "T-000003", "--agent", "verifier", "--quiet")
    run_tack(db, "status", "T-000003", "review", "--agent", "verifier",
             "--quiet")

    # The explicit blocked reason sits on top of two unfinished prerequisites.
    run_tack(
        db,
        "update",
        "T-000010",
        "--blocked-reason",
        "waiting on the operator's review of the release notes",
        "--agent",
        "demo-seed",
        "--quiet",
    )

    # A short timeline so the task pages are not empty.
    run_tack(db, "note", "T-000004",
             "Release notes drafted; waiting for the gate to clear.",
             "--agent", "backend-agent", "--quiet")
    run_tack(db, "note", "T-000010",
             "Blocked until the release notes are published and the demo board "
             "is backfilled.",
             "--agent", "demo-seed", "--quiet")

    document: dict[str, object] = json.loads(
        run_tack(db, "board", "--json")
    )
    return document


def verify(db: Path, document: dict[str, object]) -> None:
    """Fail when the board deviates from the documented seed."""
    counts = document["counts"]
    assert isinstance(counts, dict)
    if counts != EXPECTED_COUNTS:
        raise SeedError(f"unexpected counts: {counts}")
    graph = json.loads(run_tack(db, "graph", "--json"))
    edges = graph["edges"]
    if len(edges) != EXPECTED_EDGES:
        raise SeedError(f"unexpected edge total: {len(edges)}")
    statuses = sorted({node["status"] for node in graph["nodes"]})
    if statuses != sorted(EXPECTED_STATUSES):
        raise SeedError(f"unexpected status coverage: {statuses}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path,
                        help="board file to reset and rebuild")
    args = parser.parse_args(argv)
    document = build(args.db)
    verify(args.db, document)
    print(json.dumps({"counts": document["counts"],
                      "database": str(args.db)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
