"""Dependency DAG: edges, cycle detection, readiness, graph export.

Spec: section 7 (dependency DAG) and section 10.5 (graph envelopes). The
validation policy for claimed tasks is shared with :mod:`tackboard.service`
through its internal helpers, so there is exactly one implementation.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from typing import Any

from . import history
from .config import validate_agent_id
from .db import read_transaction, write_transaction
from .errors import CycleError, StaleVersionError, ValidationError
from .models import Claim, utcnow_iso
from .service import (
    _FORCE_NOT_APPLICABLE,
    _assert_not_claimed_by_other,
    _check_force_reason,
    _load_task,
    _project_rows,
    _project_task,
    _task_row,
)
from .service import _blockers as _blocker_entries
from .service import derived_flags as computed_flags

_CANCELLED = "cancelled"
_DONE = "done"
_IN_PROGRESS = "in_progress"
_REVIEW = "review"


def _edge_exists(
    conn: sqlite3.Connection, task_id: str, prerequisite_id: str
) -> bool:
    row = conn.execute(
        "SELECT 1 FROM dependencies WHERE task_id = ? AND prerequisite_id = ?",
        (task_id, prerequisite_id),
    ).fetchone()
    return row is not None


def _prerequisite_path(
    conn: sqlite3.Connection, start: str, target: str
) -> list[str] | None:
    """Return a prerequisite chain ``start -> ... -> target`` if one exists."""
    edges: dict[str, list[str]] = {}
    for row in conn.execute(
        "SELECT task_id, prerequisite_id FROM dependencies ORDER BY prerequisite_id"
    ):
        edges.setdefault(str(row["task_id"]), []).append(str(row["prerequisite_id"]))

    stack: list[tuple[str, list[str]]] = [(start, [start])]
    visited: set[str] = set()
    while stack:
        node, path = stack.pop()
        if node == target and len(path) > 1:
            return path
        if node in visited:
            continue
        visited.add(node)
        for neighbour in edges.get(node, []):
            stack.append((neighbour, [*path, neighbour]))
    return None


def find_cycle(conn: sqlite3.Connection) -> list[str] | None:
    """Return one dependency cycle as ``[a, b, ..., a]``, or ``None``."""
    edges: dict[str, list[str]] = {}
    for row in conn.execute(
        "SELECT task_id, prerequisite_id FROM dependencies ORDER BY prerequisite_id"
    ):
        edges.setdefault(str(row["task_id"]), []).append(str(row["prerequisite_id"]))

    white, grey, black = 0, 1, 2
    colour: dict[str, int] = {}

    def visit(node: str, path: list[str]) -> list[str] | None:
        colour[node] = grey
        for neighbour in edges.get(node, []):
            state = colour.get(neighbour, white)
            if state == grey:
                index = path.index(neighbour)
                return [*path[index:], neighbour]
            if state == white:
                found = visit(neighbour, [*path, neighbour])
                if found is not None:
                    return found
        colour[node] = black
        return None

    for node in list(edges):
        if colour.get(node, white) == white:
            cycle = visit(node, [node])
            if cycle is not None:
                return cycle
    return None


def add_dependency(
    conn: sqlite3.Connection,
    task_id: str,
    prerequisite_id: str,
    *,
    actor: str,
    if_version: int | None = None,
    force: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """Add a prerequisite edge; idempotent, cycle-checked, atomic (spec 7.3)."""
    actor = validate_agent_id(actor)
    forced_reason = _check_force_reason(force, reason)

    with write_transaction(conn):
        task = _load_task(conn, task_id)
        if if_version is not None and task.version != if_version:
            raise StaleVersionError(
                f"Stale version for {task_id}: expected {if_version}, "
                f"current {task.version}."
            )
        if task_id == prerequisite_id:
            raise ValidationError(f"{task_id} cannot depend on itself.")
        prerequisite = _load_task(conn, prerequisite_id)

        if _edge_exists(conn, task_id, prerequisite_id):
            return {"changed": False, "task": _project_task(conn, task_id)}

        _assert_not_claimed_by_other(conn, task_id, actor)

        force_used = False
        if task.status == _CANCELLED:
            raise ValidationError(
                f"Cannot add a prerequisite to {task_id}: it is cancelled. "
                "Restore it first."
            )
        if task.status == _DONE and prerequisite.status != _DONE:
            raise ValidationError(
                f"Cannot add prerequisite {prerequisite_id} to {task_id}: "
                "a done task may only gain done prerequisites."
            )
        if task.status in {_IN_PROGRESS, _REVIEW} and prerequisite.status != _DONE:
            if not force:
                raise ValidationError(
                    f"Cannot add prerequisite {prerequisite_id} to {task_id}: "
                    f"it is {task.status} (use --force --reason)."
                )
            force_used = True
        elif force:
            raise ValidationError(_FORCE_NOT_APPLICABLE)

        path = _prerequisite_path(conn, prerequisite_id, task_id)
        if path is not None:
            cycle = " -> ".join([*path, prerequisite_id])
            raise CycleError(
                f"Cannot add dependency: this would create a cycle ({cycle})."
            )

        conn.execute(
            "INSERT INTO dependencies(prerequisite_id, task_id) VALUES(?, ?)",
            (prerequisite_id, task_id),
        )
        conn.execute(
            "UPDATE tasks SET updated_at = ?, version = ? WHERE id = ? AND version = ?",
            (_now(), task.version + 1, task_id, task.version),
        )
        payload: dict[str, Any] = {"prerequisite_id": prerequisite_id}
        if force_used:
            payload["forced"] = True
            payload["reason"] = forced_reason
        history.record_event(
            conn,
            event_type="dependency.added",
            actor=actor,
            task_id=task_id,
            payload=payload,
        )
        return {"changed": True, "task": _project_task(conn, task_id)}


def remove_dependency(
    conn: sqlite3.Connection,
    task_id: str,
    prerequisite_id: str,
    *,
    actor: str,
    if_version: int | None = None,
) -> dict[str, Any]:
    """Remove a prerequisite edge; idempotent (spec 7.3)."""
    actor = validate_agent_id(actor)

    with write_transaction(conn):
        task = _load_task(conn, task_id)
        if if_version is not None and task.version != if_version:
            raise StaleVersionError(
                f"Stale version for {task_id}: expected {if_version}, "
                f"current {task.version}."
            )
        _load_task(conn, prerequisite_id)

        if not _edge_exists(conn, task_id, prerequisite_id):
            return {"changed": False, "task": _project_task(conn, task_id)}

        _assert_not_claimed_by_other(conn, task_id, actor)
        conn.execute(
            "DELETE FROM dependencies WHERE task_id = ? AND prerequisite_id = ?",
            (task_id, prerequisite_id),
        )
        conn.execute(
            "UPDATE tasks SET updated_at = ?, version = ? WHERE id = ? AND version = ?",
            (_now(), task.version + 1, task_id, task.version),
        )
        history.record_event(
            conn,
            event_type="dependency.removed",
            actor=actor,
            task_id=task_id,
            payload={"prerequisite_id": prerequisite_id},
        )
        return {"changed": True, "task": _project_task(conn, task_id)}


def _related(
    conn: sqlite3.Connection, task_id: str, *, dependents: bool
) -> dict[str, Any]:
    _task_row(conn, task_id)
    if dependents:
        sql = """
            SELECT t.* FROM tasks t
            JOIN dependencies d ON d.task_id = t.id
            WHERE d.prerequisite_id = ?
            ORDER BY t.sequence
        """
    else:
        sql = """
            SELECT t.* FROM tasks t
            JOIN dependencies d ON d.prerequisite_id = t.id
            WHERE d.task_id = ?
            ORDER BY t.sequence
        """
    with read_transaction(conn):
        rows = conn.execute(sql, (task_id,)).fetchall()
        tasks = _project_rows(conn, rows, full=False)
    return {"schema_version": 1, "task_id": task_id, "tasks": tasks}


def list_dependencies(conn: sqlite3.Connection, task_id: str) -> dict[str, Any]:
    """Return the direct prerequisites of ``task_id``."""
    return _related(conn, task_id, dependents=False)


def list_dependents(conn: sqlite3.Connection, task_id: str) -> dict[str, Any]:
    """Return the tasks that directly depend on ``task_id``."""
    return _related(conn, task_id, dependents=True)


def dependency_ready(conn: sqlite3.Connection, task_id: str) -> bool:
    """True when every direct prerequisite is ``done`` (spec 7.1)."""
    _task_row(conn, task_id)
    row = conn.execute(
        """
        SELECT COUNT(*) AS unfinished
        FROM dependencies d
        JOIN tasks p ON p.id = d.prerequisite_id
        WHERE d.task_id = ? AND p.status <> 'done'
        """,
        (task_id,),
    ).fetchone()
    return int(row["unfinished"]) == 0


def blockers(conn: sqlite3.Connection, task_id: str) -> list[dict[str, Any]]:
    """Reason entries: ``prerequisite_unfinished``, ``prerequisite_cancelled``, ``explicit``."""
    row = _task_row(conn, task_id)
    prerequisites = _prerequisite_states(conn, task_id)
    return _blockers_for(prerequisites, row["blocked_reason"])


def derived_flags(conn: sqlite3.Connection, task_id: str) -> dict[str, Any]:
    """The spec 7.4 derived flags plus claim information."""
    row = _task_row(conn, task_id)
    prerequisites = _prerequisite_states(conn, task_id)
    claim_row = conn.execute(
        "SELECT agent_id, claimed_at FROM claims WHERE task_id = ?", (task_id,)
    ).fetchone()
    claim = (
        None
        if claim_row is None
        else Claim(
            task_id=task_id,
            agent_id=claim_row["agent_id"],
            claimed_at=claim_row["claimed_at"],
        )
    )
    flags = computed_flags(prerequisites, row["blocked_reason"], row["status"], claim)
    flags["claimed_by"] = claim.agent_id if claim is not None else None
    flags["claimed_at"] = claim.claimed_at if claim is not None else None
    return flags


def _prerequisite_states(
    conn: sqlite3.Connection, task_id: str
) -> list[tuple[str, str]]:
    rows = conn.execute(
        """
        SELECT d.prerequisite_id AS prerequisite_id, p.status AS status
        FROM dependencies d
        JOIN tasks p ON p.id = d.prerequisite_id
        WHERE d.task_id = ?
        ORDER BY p.sequence
        """,
        (task_id,),
    ).fetchall()
    return [(str(row["prerequisite_id"]), str(row["status"])) for row in rows]


def _blockers_for(
    prerequisites: Iterable[tuple[str, str]], blocked_reason: str | None
) -> list[dict[str, Any]]:
    return _blocker_entries(list(prerequisites), blocked_reason)


def _dot_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def _graph_data(
    conn: sqlite3.Connection, task_id: str | None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if task_id is None:
        rows = conn.execute("SELECT * FROM tasks ORDER BY sequence").fetchall()
    else:
        _task_row(conn, task_id)
        rows = conn.execute(
            """
            SELECT DISTINCT t.* FROM tasks t
            WHERE t.id = ?
               OR t.id IN (SELECT prerequisite_id FROM dependencies WHERE task_id = ?)
               OR t.id IN (SELECT task_id FROM dependencies WHERE prerequisite_id = ?)
            ORDER BY t.sequence
            """,
            (task_id, task_id, task_id),
        ).fetchall()

    node_ids = [str(row["id"]) for row in rows]
    nodes = [
        {
            "id": row["id"],
            "title": row["title"],
            "status": row["status"],
            "priority": row["priority"],
            "assignee": row["assignee"],
        }
        for row in rows
    ]
    edges: list[dict[str, Any]] = []
    if node_ids:
        placeholders = ",".join("?" for _ in node_ids)
        edge_rows = conn.execute(
            f"""
            SELECT prerequisite_id, task_id FROM dependencies
            WHERE prerequisite_id IN ({placeholders}) AND task_id IN ({placeholders})
            ORDER BY prerequisite_id, task_id
            """,
            [*node_ids, *node_ids],
        ).fetchall()
        edges = [
            {"source": row["prerequisite_id"], "target": row["task_id"]}
            for row in edge_rows
        ]
    return nodes, edges


def export_graph(
    conn: sqlite3.Connection, task_id: str | None = None, *, fmt: str = "json"
) -> Any:
    """Export the whole graph or one task's neighbourhood (spec 7.3, 10.5)."""
    if fmt not in {"json", "dot"}:
        raise ValidationError(f"Unknown graph format {fmt!r}; expected json or dot.")
    with read_transaction(conn):
        nodes, edges = _graph_data(conn, task_id)
    if fmt == "json":
        return {"schema_version": 1, "nodes": nodes, "edges": edges}

    lines = ["digraph tackboard {"]
    lines.append("    rankdir=LR;")
    for node in nodes:
        label = f"{node['id']}: {node['title']}"
        lines.append(f"    {_dot_quote(str(node['id']))} [label={_dot_quote(label)}];")
    for edge in edges:
        lines.append(f"    {_dot_quote(edge['source'])} -> {_dot_quote(edge['target'])};")
    lines.append("}")
    return "\n".join(lines)


def _now() -> str:
    return utcnow_iso()


__all__ = [
    "add_dependency",
    "blockers",
    "dependency_ready",
    "derived_flags",
    "export_graph",
    "find_cycle",
    "list_dependencies",
    "list_dependents",
    "remove_dependency",
]
