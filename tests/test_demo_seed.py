"""The committed demo seed builds the board the README screenshots show."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "scripts" / "demo_seed.py"
STATUSES = {"backlog", "ready", "in_progress", "review", "done", "cancelled"}
CLOSED = {"done", "cancelled"}


def tack(db: Path, *args: str) -> dict[str, Any]:
    """Run one read-only ``tack`` command and return its JSON document."""
    result = subprocess.run(
        [sys.executable, "-m", "tackboard", *args, "--db", str(db), "--json"],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def run_seed(db: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(SEED), "--db", str(db)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def projection(db: Path) -> dict[str, Any]:
    """The seed's reviewable surface: no timestamps, no clock-dependent text."""
    board = tack(db, "board")
    graph = tack(db, "graph")
    tasks = [
        {
            "id": task["id"],
            "title": task["title"],
            "status": task["status"],
            "priority": task["priority"],
            "assignee": task["assignee"],
            "labels": sorted(task["labels"]),
            "depends_on": sorted(task["depends_on"]),
        }
        for task in sorted(board["tasks"], key=lambda item: item["id"])
    ]
    edges = sorted((edge["source"], edge["target"]) for edge in graph["edges"])
    return {"tasks": tasks, "edges": edges}


@pytest.fixture
def seeded(tmp_path: Path) -> Path:
    db = tmp_path / "demo.sqlite3"
    run_seed(db)
    return db


def test_seed_builds_the_documented_board(seeded: Path) -> None:
    board = tack(seeded, "board")
    assert board["counts"] == {
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
    ids = [task["id"] for task in board["tasks"]]
    assert sorted(ids) == [f"T-0000{number:02d}" for number in range(1, 13)]


def test_seed_exercises_every_status_glyph(seeded: Path) -> None:
    graph = tack(seeded, "graph")
    statuses = {node["status"] for node in graph["nodes"]}
    assert statuses == STATUSES
    closed = [node for node in graph["nodes"] if node["status"] in CLOSED]
    assert len(closed) == 4


def test_seed_has_one_claimed_in_progress_task_and_one_blocked_task(
    seeded: Path,
) -> None:
    board = tack(seeded, "board")
    by_id = {task["id"]: task for task in board["tasks"]}
    assert by_id["T-000004"]["status"] == "in_progress"
    assert by_id["T-000004"]["claimed_by"] == "backend-agent"
    blocked = by_id["T-000010"]
    assert blocked["blocked"] is True
    assert blocked["blocked_reason"]
    unfinished = [
        dependency for dependency in blocked["depends_on"]
        if by_id[dependency]["status"] != "done"
    ]
    assert len(unfinished) == 2


def test_seed_is_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "first.sqlite3"
    second = tmp_path / "second.sqlite3"
    run_seed(first)
    run_seed(second)
    assert projection(first) == projection(second)


def test_seed_resets_an_existing_board(tmp_path: Path) -> None:
    db = tmp_path / "demo.sqlite3"
    run_seed(db)
    run_seed(db)
    board = tack(db, "board")
    assert board["counts"]["tasks"] == 12
    assert len(board["tasks"]) == 12
