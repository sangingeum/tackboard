"""Append-only activity history (spec section 9).

``record_event`` never opens its own transaction: it runs inside the caller's
mutation transaction so a mutation and its event commit or roll back together.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from .db import read_transaction
from .errors import ValidationError
from .models import utcnow_iso

CANONICAL_EVENT_TYPES: tuple[str, ...] = (
    "task.created",
    "task.updated",
    "task.status_changed",
    "task.completed",
    "task.reopened",
    "task.cancelled",
    "task.restored",
    "task.claimed",
    "task.released",
    "dependency.added",
    "dependency.removed",
    "note",
)


def record_event(
    conn: sqlite3.Connection,
    *,
    event_type: str,
    actor: str,
    task_id: str | None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append one canonical event for a mutation in the current transaction."""
    if event_type not in CANONICAL_EVENT_TYPES:
        raise ValidationError(
            f"Unknown event type {event_type!r}; canonical event types are: "
            f"{', '.join(CANONICAL_EVENT_TYPES)}."
        )
    timestamp = utcnow_iso()
    body = json.dumps(payload or {}, sort_keys=True, separators=(",", ":"))
    cursor = conn.execute(
        "INSERT INTO events(timestamp, actor, event_type, task_id, payload_json) "
        "VALUES(?, ?, ?, ?, ?)",
        (timestamp, actor, event_type, task_id, body),
    )
    return {
        "id": cursor.lastrowid,
        "timestamp": timestamp,
        "actor": actor,
        "event_type": event_type,
        "task_id": task_id,
        "payload": payload or {},
    }


def _event_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "timestamp": row["timestamp"],
        "actor": row["actor"],
        "event_type": row["event_type"],
        "task_id": row["task_id"],
        "payload": json.loads(row["payload_json"]),
    }


def list_events(
    conn: sqlite3.Connection,
    task_id: str | None = None,
    *,
    limit: int | None = None,
) -> dict[str, Any]:
    """Return recent events, newest first, optionally limited to one task."""
    sql = "SELECT * FROM events"
    params: list[Any] = []
    if task_id is not None:
        sql += " WHERE task_id = ?"
        params.append(task_id)
    sql += " ORDER BY id DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))
    with read_transaction(conn):
        rows = conn.execute(sql, params).fetchall()
    return {
        "schema_version": 1,
        "task_id": task_id,
        "items": [_event_from_row(row) for row in rows],
    }


#: The event type appended by ``service.add_note`` (spec 9.1).
NOTE_EVENT_TYPE = "note"


def list_notes(
    conn: sqlite3.Connection,
    task_id: str,
    *,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Return a task's most recent notes, newest first (spec 9.1).

    ``show`` displays the latest notes; the query lives here so neither CLI nor
    web layer carries SQL.
    """
    with read_transaction(conn):
        rows = conn.execute(
            "SELECT * FROM events WHERE task_id = ? AND event_type = ? "
            "ORDER BY id DESC LIMIT ?",
            (task_id, NOTE_EVENT_TYPE, int(limit)),
        ).fetchall()
    return [_event_from_row(row) for row in rows]


__all__ = [
    "CANONICAL_EVENT_TYPES",
    "NOTE_EVENT_TYPE",
    "list_events",
    "list_notes",
    "record_event",
]
