"""Activity history: append-only events and queries (spec section 9)."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any

import pytest

from tackboard import history, service
from tackboard.errors import InvalidTransitionError


def test_a_mutation_records_exactly_one_canonical_event(
    conn: sqlite3.Connection, make_task: Callable[..., dict[str, Any]]
) -> None:
    task = make_task(title="Recorded")
    events = history.list_events(conn)["items"]
    assert [event["event_type"] for event in events] == ["task.created"]
    assert events[0]["task_id"] == task["id"]
    assert events[0]["actor"] == "dev"


def test_events_are_append_only(
    conn: sqlite3.Connection, make_task: Callable[..., dict[str, Any]]
) -> None:
    make_task(title="Recorded")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE events SET actor = 'other'")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM events")


def test_a_failed_mutation_leaves_no_event(
    conn: sqlite3.Connection, make_task: Callable[..., dict[str, Any]]
) -> None:
    task = make_task(title="Untouched")
    before = len(history.list_events(conn)["items"])
    with pytest.raises(InvalidTransitionError):
        service.set_status(conn, task["id"], "done", actor="dev")
    assert len(history.list_events(conn)["items"]) == before


def test_a_no_op_mutation_records_no_event(
    conn: sqlite3.Connection, make_task: Callable[..., dict[str, Any]]
) -> None:
    task = make_task(title="No-op")
    before = len(history.list_events(conn)["items"])
    result = service.update_task(conn, task["id"], actor="dev", title="No-op")
    assert result["changed"] is False
    assert len(history.list_events(conn)["items"]) == before


def test_history_can_be_limited_to_one_task(
    conn: sqlite3.Connection, make_task: Callable[..., dict[str, Any]]
) -> None:
    first = make_task(title="First")
    make_task(title="Second")
    service.add_note(conn, first["id"], "handoff context", actor="dev")
    items = history.list_events(conn, first["id"])["items"]
    assert [event["event_type"] for event in items] == ["note", "task.created"]


def test_history_limit_returns_the_newest_events(
    conn: sqlite3.Connection, make_task: Callable[..., dict[str, Any]]
) -> None:
    task = make_task(title="Chatty")
    service.add_note(conn, task["id"], "one", actor="dev")
    service.add_note(conn, task["id"], "two", actor="dev")
    items = history.list_events(conn, task["id"], limit=2)["items"]
    assert len(items) == 2
    assert items[0]["payload"]["text"] == "two"


def test_note_event_does_not_change_version_or_updated_at(
    conn: sqlite3.Connection, make_task: Callable[..., dict[str, Any]]
) -> None:
    task = make_task(title="Noted")
    service.add_note(conn, task["id"], "a note", actor="dev")
    after = service.get_task(conn, task["id"])
    assert after["version"] == task["version"]
    assert after["updated_at"] == task["updated_at"]
