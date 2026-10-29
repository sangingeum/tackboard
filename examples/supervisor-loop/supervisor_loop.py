#!/usr/bin/env python3
"""A supervisor that watches the board's event feed and reacts to it.

Run it:

    uv run python examples/supervisor-loop/supervisor_loop.py
    uv run python examples/supervisor-loop/supervisor_loop.py --max-passes 5

The pattern is the one an orchestrator needs: never poll the whole board, read
the events since a cursor, act on the interesting ones, persist the cursor, and
stop. ``history --since EVENT_ID`` is that feed — oldest first, resumable, and
at-least-once (so the loop de-duplicates on the event id).

The supervisor reacts to ``task.completed`` (a dependent may now be startable)
and ``task.status_changed`` (work moved into review). Each pass it also does one
unit of work itself, so the feed has something to show; a real supervisor would
be reacting to workers elsewhere. ``--max-passes`` bounds the run and is what
makes it testable.

Everything lives in a temporary directory that is removed at the end.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

TACK = tuple(os.environ.get("TACK_CMD", "").split()) or ("tack",)
WATCHED = {"task.completed", "task.status_changed"}


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


class Cursor:
    """The high-water mark, persisted between passes (and between runs)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.value = 0
        if path.exists():
            self.value = int(json.loads(path.read_text(encoding="utf-8"))["cursor"])

    def advance(self, event_id: int) -> None:
        if event_id > self.value:
            self.value = event_id
            self.path.write_text(
                json.dumps({"cursor": self.value}) + "\n", encoding="utf-8"
            )


def react(event: dict[str, Any], db: Path) -> str:
    """Turn one watched event into a reaction. This is the supervisor's policy."""
    kind = event["event_type"]
    task_id = event["task_id"]
    if kind == "task.completed":
        dependents = tack(db, "supervisor", "dependents", task_id)["tasks"]
        eligible = [task["id"] for task in dependents if task["dependency_ready"]]
        return f"{task_id} completed -> {len(dependents)} dependent(s), ready now: {eligible}"
    payload = event["payload"]
    return f"{task_id} {payload.get('from')} -> {payload.get('to')}: note the handoff"


def one_worker_step(db: Path, actor: str) -> str | None:
    """One unit of work, so the feed keeps moving. A real worker lives elsewhere."""
    task = tack(db, actor, "next", "--claim")["task"]
    if task is None:
        return None
    tack(db, actor, "note", task["id"], "supervised step")
    tack(db, actor, "status", task["id"], "review")
    tack(db, actor, "complete", task["id"])
    return str(task["id"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--max-passes",
        type=int,
        default=3,
        help="stop after this many passes (default 3; keeps the loop testable)",
    )
    args = parser.parse_args(argv)

    root = Path(tempfile.mkdtemp(prefix="tack-supervisor-"))
    try:
        db = root / "board.sqlite3"
        tack(db, "planner", "init")
        created = []
        for title in ("Prerequisite", "Dependent one", "Dependent two"):
            created.append(tack(db, "planner", "create", title, "--status", "ready"))
        prerequisite, *dependents = [entry["task"]["id"] for entry in created]
        for dependent in dependents:
            tack(db, "planner", "depend", "add", dependent, "--on", prerequisite)
        print(f"board: {prerequisite} with dependents {dependents}")

        cursor = Cursor(root / "cursor.json")
        print(f"starting cursor: {cursor.value}")

        for number in range(1, args.max_passes + 1):
            completed = tack(db, "supervisor", "history", "--since", str(cursor.value))
            events = completed["items"]
            seen = {event["id"] for event in events}
            print(f"\npass {number}: {len(events)} new event(s) since {cursor.value}")

            for event in events:
                if event["event_type"] in WATCHED:
                    print(f"  [{event['id']}] {react(event, db)}")
                cursor.advance(event["id"])

            # At-least-once delivery over overlapping reads: de-duplicate by id.
            assert len(seen) == len(events), "the feed repeated an event id"

            worked = one_worker_step(db, "worker")
            print(f"  worker completed: {worked or 'nothing eligible'}")
            if worked is None and not events:
                print("  idle and quiet: stopping early")
                break

        print(f"\ncursor persisted at {cursor.path}: {cursor.value}")
        print(f"final counts: {tack(db, 'supervisor', 'board', '--brief')['counts']}")
        return 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
