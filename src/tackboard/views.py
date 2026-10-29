"""Interface-neutral read models shared by the CLI and the web layer.

Spec: section 10.2 and 10.5 (the board document and the count contract),
section 7.3 and 10.5 (graph export), section 11.2 (task detail), and
section 11.3 (the JSON API must be produced by the service layer with the same
shapes the CLI emits).

These functions hold no HTTP and no rendering logic. Each opens one read
transaction, calls the same domain functions the CLI calls, and returns plain
dictionaries — so the CLI, the JSON API and the server-rendered pages describe
one consistent snapshot in one vocabulary. Nothing here knows that a command
line or a browser exists.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from . import claims, dependencies, history, memo, service
from .db import read_transaction
from .schema import database_label

JSON_SCHEMA_VERSION = 1

#: Task detail shows the latest notes (spec 9.1) and recent history (spec 11.2).
TASK_DETAIL_NOTE_LIMIT = 5
TASK_DETAIL_HISTORY_LIMIT = 20


def board_document(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    assignee: str | None = None,
    label: str | None = None,
    priority: str | None = None,
    blocked: bool | None = None,
    eligible: bool | None = None,
    limit: int | None = None,
    sort: str | None = None,
    reverse: bool = False,
    full: bool = False,
) -> dict[str, Any]:
    """Return the spec 10.5 board document for one filtered set."""
    with read_transaction(conn):
        tasks = service.list_tasks(
            conn,
            status=status,
            assignee=assignee,
            label=label,
            priority=priority,
            blocked=blocked,
            eligible=eligible,
            limit=limit,
            sort=sort,
            reverse=reverse,
            full=full,
        )
        counts = service.counts(conn, tasks)
        project = database_label(conn)
    return {
        "schema_version": JSON_SCHEMA_VERSION,
        "project": project,
        "counts": counts,
        "tasks": tasks,
    }


def graph_document(
    conn: sqlite3.Connection,
    task_id: str | None = None,
    *,
    include_claim_staleness: bool = False,
) -> dict[str, Any]:
    """Return the graph document plus the derived flags each node displays.

    The nodes and edges are exactly what ``tack graph --format json`` emits;
    the derived readiness flags are added so the browser can distinguish done,
    eligible, in-progress, review, blocked and cancelled nodes without
    recomputing the graph itself. ``include_claim_staleness`` is the
    server-rendered page's opt-in: it adds the read-only
    ``claim_age_seconds``/``claim_stale`` pair so the page and its outline can
    mark a stale claim without re-deriving the threshold in the web layer. The
    JSON API leaves the flag off so ``/api/graph`` stays byte-identical to the
    CLI document.
    """
    with read_transaction(conn):
        graph = dependencies.export_graph(conn, task_id, fmt="json")
        node_ids = [str(node["id"]) for node in graph["nodes"]]
        projections = {
            str(task["id"]): task
            for task in service.project_tasks_by_id(conn, node_ids)
        }
    nodes: list[dict[str, Any]] = []
    for node in graph["nodes"]:
        projection = projections.get(str(node["id"]), {})
        rendered: dict[str, Any] = {
            **node,
            "dependency_ready": bool(projection.get("dependency_ready", True)),
            "eligible": bool(projection.get("eligible", False)),
            "reviewable": bool(projection.get("reviewable", False)),
            "blocked": bool(projection.get("blocked", False)),
            "claimed_at": node.get("claimed_at"),
        }
        if include_claim_staleness:
            rendered["claim_age_seconds"] = projection.get("claim_age_seconds")
            rendered["claim_stale"] = bool(projection.get("claim_stale", False))
        nodes.append(rendered)
    return {
        "schema_version": JSON_SCHEMA_VERSION,
        "nodes": nodes,
        "edges": graph["edges"],
    }


def task_document(
    conn: sqlite3.Connection,
    task_id: str,
    *,
    note_limit: int = TASK_DETAIL_NOTE_LIMIT,
    history_limit: int = TASK_DETAIL_HISTORY_LIMIT,
) -> dict[str, Any]:
    """Return everything the task detail view and ``/api/tasks/<id>`` show."""
    with read_transaction(conn):
        task = service.get_task(conn, task_id)
        prerequisites = dependencies.list_dependencies(conn, task_id)["tasks"]
        dependents = dependencies.list_dependents(conn, task_id)["tasks"]
        notes = history.list_notes(conn, task_id, limit=note_limit)
        events = history.list_events(conn, task_id, limit=history_limit)["items"]
        claim = claims.active_claim(conn, task_id)
    return {
        "schema_version": JSON_SCHEMA_VERSION,
        "task": task,
        "claim": claim,
        "prerequisites": prerequisites,
        "dependents": dependents,
        "notes": notes,
        "history": events,
    }


def memo_document(
    conn: sqlite3.Connection, rev: int | None = None
) -> dict[str, Any]:
    """The board-level memo document, exactly as ``tack memo --json`` prints it.

    The CLI and the web layer both consume this, so ``/api/memo`` is the CLI's
    own document — one snapshot, one vocabulary (spec 11.3).
    """
    return {"schema_version": JSON_SCHEMA_VERSION, **memo.read_memo(conn, rev)}


def agents_document(conn: sqlite3.Connection) -> dict[str, Any]:
    """Per-agent activity derived from active claims and the event log.

    Every agent with at least one event or one active claim appears; an agent
    with neither is not an agent on this board and never appears as a zero row.
    ``claimed_tasks`` lists the task ids the agent currently holds,
    ``last_event_at`` is the timestamp of that agent's highest event id, and
    ``completed`` / ``released`` / ``forced`` count the matching events. Nothing
    is stored: one read transaction computes the whole summary.
    """
    with read_transaction(conn):
        claimed_rows = conn.execute(
            """
            SELECT c.agent_id AS agent_id, c.task_id AS task_id
            FROM claims c JOIN tasks t ON t.id = c.task_id
            ORDER BY c.agent_id, c.task_id
            """
        ).fetchall()
        event_rows = conn.execute(
            """
            SELECT e.actor AS agent_id,
                   SUM(CASE WHEN e.event_type = 'task.completed'
                            THEN 1 ELSE 0 END) AS completed,
                   SUM(CASE WHEN e.event_type = 'task.released'
                            THEN 1 ELSE 0 END) AS released,
                   SUM(CASE WHEN json_extract(e.payload_json, '$.forced') = 1
                            THEN 1 ELSE 0 END) AS forced,
                   MAX(e.id) AS last_event_id
            FROM events e
            GROUP BY e.actor
            ORDER BY e.actor
            """
        ).fetchall()
        last_ids = [int(row["last_event_id"]) for row in event_rows]
        timestamps: dict[int, str] = {}
        if last_ids:
            placeholders = ", ".join("?" for _ in last_ids)
            timestamps = {
                int(row["id"]): str(row["timestamp"])
                for row in conn.execute(
                    f"SELECT id, timestamp FROM events WHERE id IN ({placeholders})",
                    last_ids,
                )
            }

    agents: dict[str, dict[str, Any]] = {}
    for row in event_rows:
        agents[str(row["agent_id"])] = {
            "agent_id": str(row["agent_id"]),
            "claimed_tasks": [],
            "last_event_at": timestamps[int(row["last_event_id"])],
            "completed": int(row["completed"]),
            "released": int(row["released"]),
            "forced": int(row["forced"]),
        }
    for row in claimed_rows:
        agent = agents.setdefault(
            str(row["agent_id"]),
            {
                "agent_id": str(row["agent_id"]),
                "claimed_tasks": [],
                "last_event_at": None,
                "completed": 0,
                "released": 0,
                "forced": 0,
            },
        )
        agent["claimed_tasks"].append(str(row["task_id"]))

    return {
        "schema_version": JSON_SCHEMA_VERSION,
        "agents": [agents[key] for key in sorted(agents)],
    }


__all__ = [
    "JSON_SCHEMA_VERSION",
    "TASK_DETAIL_HISTORY_LIMIT",
    "TASK_DETAIL_NOTE_LIMIT",
    "agents_document",
    "board_document",
    "graph_document",
    "memo_document",
    "task_document",
]
