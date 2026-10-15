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

from . import claims, dependencies, history, service
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
    conn: sqlite3.Connection, task_id: str | None = None
) -> dict[str, Any]:
    """Return the graph document plus the derived flags each node displays.

    The nodes and edges are exactly what ``tack graph --format json`` emits;
    the derived readiness flags are added so the browser can distinguish done,
    eligible, in-progress, review, blocked and cancelled nodes without
    recomputing the graph itself.
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
        nodes.append(
            {
                **node,
                "dependency_ready": bool(projection.get("dependency_ready", True)),
                "eligible": bool(projection.get("eligible", False)),
                "reviewable": bool(projection.get("reviewable", False)),
                "blocked": bool(projection.get("blocked", False)),
                "claimed_by": projection.get("claimed_by"),
            }
        )
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


__all__ = [
    "JSON_SCHEMA_VERSION",
    "TASK_DETAIL_HISTORY_LIMIT",
    "TASK_DETAIL_NOTE_LIMIT",
    "board_document",
    "graph_document",
    "task_document",
]
