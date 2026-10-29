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
    # Board-level memo writes (schema 2). The payload points at the revision it
    # created (``rev``), never the memo body: the document lives in
    # ``memo_revisions``, the log holds the audit pointer.
    "memo.replaced",
    "memo.appended",
    "memo.restored",
    "memo.cleared",
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
    since: int | None = None,
) -> dict[str, Any]:
    """Return events, optionally limited to one task and a ``since`` cursor.

    The plain read (no ``since``) is newest first and keeps its 0.5.0 shape, so
    existing consumers are unaffected. ``since`` switches to the feed form:
    events with ``id`` greater than the cursor, **oldest first**, so a consumer
    resumes exactly where it stopped and appends in order. That document adds
    ``order: "asc"`` and echoes the cursor in ``since``. ``id`` is monotonic on
    the append-only ``events`` table, so the cursor is sound.
    """
    ascending = since is not None
    sql = "SELECT * FROM events"
    clauses: list[str] = []
    params: list[Any] = []
    if task_id is not None:
        clauses.append("task_id = ?")
        params.append(task_id)
    if since is not None:
        clauses.append("id > ?")
        params.append(int(since))
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY id ASC" if ascending else " ORDER BY id DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))
    with read_transaction(conn):
        rows = conn.execute(sql, params).fetchall()
    document: dict[str, Any] = {
        "schema_version": 1,
        "task_id": task_id,
        "items": [_event_from_row(row) for row in rows],
    }
    if ascending:
        document["order"] = "asc"
        document["since"] = int(since)
    return document


def latest_event_id(conn: sqlite3.Connection) -> int:
    """Return the highest event id on the board (``0`` when there are none).

    The starting cursor for ``history --follow`` without ``--since``: only
    events appended after the command starts are streamed.
    """
    with read_transaction(conn):
        row = conn.execute("SELECT COALESCE(MAX(id), 0) AS latest FROM events").fetchone()
    return int(row["latest"])


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
