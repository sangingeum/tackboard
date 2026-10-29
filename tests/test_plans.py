"""Atomic plan application: validation, one transaction, one batch event.

Every test owns its own temporary board. Validation is pure — a rejected plan
must leave the board with the task and event counts it had before the call — and
an accepted plan commits its tasks, labels and edges together with one
``task.created`` event per task and one ``plan.applied`` event.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tackboard import cli, history, plans, schema, service
from tackboard.errors import CycleError, ValidationError

RunCli = Callable[..., tuple[int, str, str]]

PLAN: dict[str, Any] = {
    "tasks": [
        {
            "key": "parser",
            "title": "Ship the parser",
            "status": "ready",
            "priority": "high",
            "assignee": "dev",
            "labels": ["core"],
        },
        {"key": "cli", "title": "Wire the CLI", "depends_on": ["parser"]},
        {"title": "Document the plan", "depends_on": ["cli"], "priority": "low"},
    ]
}


def _load(plan: Any, source: str = "plan.json") -> list[plans.PlanTask]:
    return plans.load_plan(json.dumps(plan), source=source)


def _counts(conn: sqlite3.Connection) -> tuple[int, int]:
    """Return ``(tasks, events)`` — the two counts an all-or-nothing claim uses."""
    tasks = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    return int(tasks), int(events)


# --------------------------------------------------------------------------- #
# the accepted batch
# --------------------------------------------------------------------------- #


def test_a_plan_creates_tasks_labels_and_edges_together(conn: sqlite3.Connection) -> None:
    document = plans.apply_plan(
        conn, actor="dev", tasks=_load(PLAN), source="plan.json"
    )

    assert document["schema_version"] == 1
    assert document["dry_run"] is False
    assert document["count"] == 3
    assert [item["key"] for item in document["tasks"]] == ["parser", "cli", None]
    ids = [item["id"] for item in document["tasks"]]
    assert ids == ["T-000001", "T-000002", "T-000003"]

    first = service.get_task(conn, ids[0])
    assert first["status"] == "ready"
    assert first["priority"] == "high"
    assert first["assignee"] == "dev"
    assert first["labels"] == ["core"]
    assert first["depends_on"] == []

    second = service.get_task(conn, ids[1])
    assert second["depends_on"] == [ids[0]]

    third = service.get_task(conn, ids[2])
    assert third["depends_on"] == [ids[1]]
    assert third["priority"] == "low"


def test_a_plan_records_one_created_event_per_task_and_one_batch_event(
    conn: sqlite3.Connection,
) -> None:
    document = plans.apply_plan(
        conn, actor="dev", tasks=_load(PLAN), source="plan.json"
    )
    ids = [item["id"] for item in document["tasks"]]
    items = history.list_events(conn)["items"]

    # Newest first: the batch event closes the batch, then one event per task.
    assert [item["event_type"] for item in items] == [
        "plan.applied",
        "task.created",
        "task.created",
        "task.created",
    ]
    batch = items[0]
    assert batch["task_id"] is None
    assert batch["actor"] == "dev"
    assert batch["payload"] == {"file": "plan.json", "tasks": 3, "ids": ids}

    dependent = next(item for item in items if item["task_id"] == ids[1])
    assert dependent["payload"]["depends_on"] == [ids[0]]
    assert dependent["payload"]["status"] == "ready"
    assert dependent["payload"]["priority"] == "normal"


def test_applying_the_same_plan_twice_creates_a_second_set_of_tasks(
    conn: sqlite3.Connection,
) -> None:
    """Create-only: a plan never updates or re-creates an existing task."""
    first = plans.apply_plan(conn, actor="dev", tasks=_load(PLAN), source="plan.json")
    second = plans.apply_plan(conn, actor="dev", tasks=_load(PLAN), source="plan.json")

    first_ids = [item["id"] for item in first["tasks"]]
    second_ids = [item["id"] for item in second["tasks"]]
    assert set(first_ids).isdisjoint(second_ids)
    assert _counts(conn)[0] == 6
    batch_events = [
        event
        for event in history.list_events(conn)["items"]
        if event["event_type"] == "plan.applied"
    ]
    assert len(batch_events) == 2


# --------------------------------------------------------------------------- #
# dry run
# --------------------------------------------------------------------------- #


def test_a_dry_run_reports_the_batch_and_writes_nothing(conn: sqlite3.Connection) -> None:
    before = _counts(conn)
    document = plans.apply_plan(
        conn, actor="dev", tasks=_load(PLAN), source="plan.json", dry_run=True
    )

    assert document["dry_run"] is True
    assert document["count"] == 3
    assert [item["key"] for item in document["tasks"]] == ["parser", "cli", None]
    assert all(item["id"] is None for item in document["tasks"])
    assert _counts(conn) == before


def test_a_dry_run_still_rejects_a_cycle(conn: sqlite3.Connection) -> None:
    cyclic = {
        "tasks": [
            {"key": "a", "title": "A", "depends_on": ["b"]},
            {"key": "b", "title": "B", "depends_on": ["a"]},
        ]
    }
    with pytest.raises(CycleError):
        _load(cyclic)
    assert _counts(conn) == (0, 0)


# --------------------------------------------------------------------------- #
# validation: nothing is created
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("plan", "fragment"),
    [
        ({"extra": 1, "tasks": [{"title": "A"}]}, "unknown keys"),
        ({"tasks": [{"title": "A", "bogus": 1}]}, "unknown keys"),
        ({"tasks": [{"key": "a"}]}, "'title' is required"),
        (
            {"tasks": [{"key": "a", "title": "A"}, {"key": "a", "title": "B"}]},
            "duplicate key",
        ),
        (
            {"tasks": [{"key": "a", "title": "A", "depends_on": ["zzz"]}]},
            "not a key in this file",
        ),
        (
            {"tasks": [{"key": "a", "title": "A", "depends_on": ["a"]}]},
            "cannot depend on itself",
        ),
        ({"tasks": [{"key": "a", "title": "A", "status": "sideways"}]}, "Unknown status"),
        ({"tasks": [{"key": "a", "title": "A", "priority": "whenever"}]}, "Unknown priority"),
        (
            {"tasks": [{"key": "a", "title": "A", "status": "in_progress"}]},
            "Cannot create a task as",
        ),
        ({"tasks": []}, "must not be empty"),
        ({"tasks": "nope"}, "must be an array"),
        ({"tasks": [{"title": "   "}]}, "Title must not be empty"),
        ({"tasks": [{"title": "A", "labels": "core"}]}, "array of strings"),
        ({"tasks": [{"title": "A", "depends_on": [1]}]}, "only strings"),
        ({"tasks": [{"title": "A", "assignee": 7}]}, "must be a string"),
        ({"tasks": [{"title": "A", "description": 7}]}, "must be a string"),
    ],
)
def test_an_invalid_plan_is_rejected_and_creates_nothing(
    conn: sqlite3.Connection, plan: dict[str, Any], fragment: str
) -> None:
    before = _counts(conn)
    with pytest.raises(ValidationError) as caught:
        _load(plan)
    assert fragment in str(caught.value)
    assert _counts(conn) == before


def test_a_plan_that_is_not_an_object_is_rejected(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValidationError):
        _load([{"title": "A"}])
    with pytest.raises(ValidationError):
        plans.load_plan("not json", source="plan.json")
    assert _counts(conn) == (0, 0)


def test_a_cycle_among_the_batch_is_rejected(conn: sqlite3.Connection) -> None:
    cyclic = {
        "tasks": [
            {"key": "a", "title": "A", "depends_on": ["c"]},
            {"key": "b", "title": "B", "depends_on": ["a"]},
            {"key": "c", "title": "C", "depends_on": ["b"]},
        ]
    }
    with pytest.raises(CycleError) as caught:
        _load(cyclic)
    assert "a -> c -> b -> a" in str(caught.value) or "cycle" in str(caught.value)
    assert _counts(conn) == (0, 0)


def test_a_mid_batch_failure_rolls_the_whole_batch_back(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All-or-nothing: a failure after the first row still writes no event."""
    before = _counts(conn)
    original = service._insert_task_row
    calls = {"count": 0}

    def failing_insert(*args: Any, **kwargs: Any) -> str:
        calls["count"] += 1
        if calls["count"] == 2:
            raise sqlite3.OperationalError("simulated failure on the second task")
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "_insert_task_row", failing_insert)
    with pytest.raises(sqlite3.OperationalError):
        plans.apply_plan(conn, actor="dev", tasks=_load(PLAN), source="plan.json")
    monkeypatch.setattr(service, "_insert_task_row", original)

    assert calls["count"] == 2
    assert _counts(conn) == before
    assert conn.execute("SELECT COUNT(*) FROM task_labels").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM dependencies").fetchone()[0] == 0


# --------------------------------------------------------------------------- #
# the CLI surface
# --------------------------------------------------------------------------- #


@pytest.fixture
def run(capsys: pytest.CaptureFixture[str]) -> RunCli:
    """Invoke the CLI in process, returning (exit code, stdout, stderr)."""

    def _run(*argv: str) -> tuple[int, str, str]:
        code = cli.main(list(argv))
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return _run


@pytest.fixture
def plan_file(tmp_path: Path) -> Path:
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(PLAN), encoding="utf-8")
    return path


def _document(stdout: str) -> dict[str, Any]:
    text = stdout.strip()
    assert text.startswith("{"), f"stdout is not a JSON document: {stdout!r}"
    assert "\n" not in text, f"stdout carries more than one line: {stdout!r}"
    document = json.loads(text)
    assert document["schema_version"] == 1
    return document


def _db(tmp_path: Path) -> Path:
    path = tmp_path / "project.sqlite3"
    schema.init_database(path, label="project")
    return path


def test_cli_plan_apply_prints_one_json_document(
    run: RunCli, plan_file: Path, tmp_path: Path
) -> None:
    db = _db(tmp_path)
    code, out, err = run(
        "plan", "apply", str(plan_file), "--agent", "dev", "--db", str(db), "--json"
    )
    assert code == 0, err
    assert err == ""
    document = _document(out)
    assert document["dry_run"] is False
    assert document["count"] == 3
    assert [item["id"] for item in document["tasks"]] == ["T-000001", "T-000002", "T-000003"]
    assert document["tasks"][0]["title"] == "Ship the parser"


def test_cli_plan_apply_dry_run_reports_and_writes_nothing(
    run: RunCli, plan_file: Path, tmp_path: Path
) -> None:
    db = _db(tmp_path)
    code, out, err = run(
        "plan", "apply", str(plan_file), "--dry-run", "--agent", "dev",
        "--db", str(db), "--json",
    )
    assert code == 0, err
    document = _document(out)
    assert document["dry_run"] is True
    assert document["count"] == 3
    assert all(item["id"] is None for item in document["tasks"])

    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0


def test_cli_plan_apply_validation_error_is_exit_4_on_stdout(
    run: RunCli, tmp_path: Path
) -> None:
    db = _db(tmp_path)
    broken = tmp_path / "broken.json"
    broken.write_text(
        json.dumps({"tasks": [{"key": "a", "title": "A", "depends_on": ["missing"]}]}),
        encoding="utf-8",
    )
    code, out, err = run(
        "plan", "apply", str(broken), "--agent", "dev", "--db", str(db), "--json"
    )
    assert code == 4
    assert err == ""
    error = _document(out)["error"]
    assert error["code"] == "validation"
    assert error["exit_code"] == 4
    assert "not a key in this file" in error["message"]


def test_cli_plan_apply_human_output_lists_the_created_tasks(
    run: RunCli, plan_file: Path, tmp_path: Path
) -> None:
    db = _db(tmp_path)
    code, out, err = run(
        "plan", "apply", str(plan_file), "--agent", "dev", "--db", str(db)
    )
    assert code == 0, err
    lines = out.splitlines()
    assert lines[0].startswith("Applied ")
    assert "3 tasks created." in lines[0]
    assert "T-000001 [parser]  Ship the parser" in out


def test_cli_plan_without_a_subcommand_is_a_usage_error(
    run: RunCli, tmp_path: Path
) -> None:
    db = _db(tmp_path)
    code, out, err = run("plan", "--db", str(db), "--json")
    assert code == 2
    assert err == ""
    assert "tack plan apply" in _document(out)["error"]["message"]


def test_cli_plan_apply_reads_stdin(
    run: RunCli, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = _db(tmp_path)
    fake = type("Fake", (), {"read": lambda self: json.dumps(PLAN)})
    monkeypatch.setattr("sys.stdin", fake())
    code, out, err = run(
        "plan", "apply", "-", "--agent", "dev", "--db", str(db), "--json"
    )
    assert code == 0, err
    document = _document(out)
    assert document["source"] == "stdin"
    assert document["count"] == 3
