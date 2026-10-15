"""Claims and multi-agent concurrency (spec section 8).

The claim/status policy lives in exactly one place — ``service.apply_transition``
— so every entry point applies the same table. This module is the public claim
surface plus the atomic ``next --claim`` selector.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from typing import Any

from .config import validate_agent_id
from .db import read_transaction, write_transaction
from .errors import UsageError, ValidationError
from .models import PRIORITY_RANK, utcnow_iso
from .service import (
    _check_priority,
    _label_key,
    _project_rows,
    apply_transition,
)

STAGES: tuple[str, ...] = ("work", "review")

_DURATION_RE = re.compile(r"\s*(\d+)\s*([smhd])\s*\Z")
_DURATION_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_duration(text: str) -> int:
    """Parse a duration such as ``2h``, ``30m``, ``45s`` or ``1d`` into seconds."""
    match = _DURATION_RE.match(text)
    if match is None:
        raise ValidationError(
            f"Invalid duration {text!r}; use a number with a unit suffix "
            "(s, m, h, d), for example 2h."
        )
    amount, unit = match.groups()
    return int(amount) * _DURATION_UNITS[unit]


def _age_seconds(claimed_at: str) -> int:
    moment = datetime.strptime(claimed_at, "%Y-%m-%dT%H:%M:%SZ")
    now = datetime.strptime(utcnow_iso(), "%Y-%m-%dT%H:%M:%SZ")
    return max(0, int((now - moment).total_seconds()))


def active_claim(conn: sqlite3.Connection, task_id: str) -> dict[str, Any] | None:
    """Return the active claim on a task, or ``None``."""
    row = conn.execute("SELECT * FROM claims WHERE task_id = ?", (task_id,)).fetchone()
    if row is None:
        return None
    return {
        "task_id": row["task_id"],
        "agent_id": row["agent_id"],
        "claimed_at": row["claimed_at"],
        "age_seconds": _age_seconds(row["claimed_at"]),
    }


def claim(
    conn: sqlite3.Connection,
    task_id: str,
    *,
    actor: str,
    force: bool = False,
    reason: str | None = None,
    if_version: int | None = None,
) -> dict[str, Any]:
    """Claim a task (spec 8.2); the status change and claim commit together."""
    return apply_transition(
        conn,
        task_id,
        actor=actor,
        mode="claim",
        if_version=if_version,
        force=force,
        reason=reason,
        event_type="task.claimed",
    )


def release(
    conn: sqlite3.Connection,
    task_id: str,
    *,
    actor: str,
    force: bool = False,
    reason: str | None = None,
    if_version: int | None = None,
) -> dict[str, Any]:
    """Release a claim (spec 8.2); ``in_progress`` returns to ``ready``."""
    return apply_transition(
        conn,
        task_id,
        actor=actor,
        mode="release",
        if_version=if_version,
        force=force,
        reason=reason,
        event_type="task.released",
    )


def list_claims(
    conn: sqlite3.Connection,
    *,
    agent: str | None = None,
    older_than: str | None = None,
) -> dict[str, Any]:
    """Inspect active claims, optionally by agent or minimum age (spec 8.1)."""
    if agent is not None:
        validate_agent_id(agent)
    threshold = parse_duration(older_than) if older_than is not None else None

    with read_transaction(conn):
        sql = """
            SELECT c.task_id AS task_id, c.agent_id AS agent_id,
                   c.claimed_at AS claimed_at
            FROM claims c JOIN tasks t ON t.id = c.task_id
        """
        params: list[Any] = []
        if agent is not None:
            sql += " WHERE c.agent_id = ?"
            params.append(agent)
        sql += " ORDER BY c.claimed_at, c.task_id"
        rows = conn.execute(sql, params).fetchall()
        tasks = {
            task["id"]: task
            for task in _project_rows(
                conn,
                conn.execute(
                    """
                    SELECT t.* FROM tasks t
                    JOIN claims c ON c.task_id = t.id
                    """
                ).fetchall(),
                full=False,
            )
        }

    items: list[dict[str, Any]] = []
    for row in rows:
        age = _age_seconds(row["claimed_at"])
        if threshold is not None and age < threshold:
            continue
        items.append(
            {
                "task_id": row["task_id"],
                "agent_id": row["agent_id"],
                "claimed_at": row["claimed_at"],
                "age_seconds": age,
                "task": tasks.get(str(row["task_id"])),
            }
        )
    return {"schema_version": 1, "items": items}


def _candidates(
    conn: sqlite3.Connection,
    *,
    actor: str,
    stage: str,
    label: str | None,
    min_priority: str | None,
    all_assignees: bool,
    full: bool,
) -> list[dict[str, Any]]:
    sql = "SELECT t.* FROM tasks t"
    params: list[Any] = []
    if label is not None:
        sql += " JOIN task_labels l ON l.task_id = t.id AND l.label_key = ?"
        params.append(_label_key(label))
    sql += " WHERE t.status = ?"
    params.append("ready" if stage == "work" else "review")
    rows = conn.execute(sql, params).fetchall()

    tasks = _project_rows(conn, rows, full=full)
    flag = "eligible" if stage == "work" else "reviewable"
    tasks = [task for task in tasks if task[flag]]
    if not all_assignees:
        tasks = [task for task in tasks if task["assignee"] in (None, actor)]
    if min_priority is not None:
        floor = PRIORITY_RANK[min_priority]
        tasks = [
            task for task in tasks if PRIORITY_RANK[str(task["priority"])] >= floor
        ]
    tasks.sort(key=_next_sort_key)
    return tasks


def _next_sort_key(task: dict[str, Any]) -> tuple[int, str, int]:
    sequence = int(str(task["id"]).split("-", 1)[1])
    return (-PRIORITY_RANK[str(task["priority"])], str(task["created_at"]), sequence)


def next_task(
    conn: sqlite3.Connection,
    *,
    actor: str,
    stage: str = "work",
    claim: bool = False,
    force: bool = False,
    reason: str | None = None,
    label: str | None = None,
    min_priority: str | None = None,
    all_assignees: bool = False,
    full: bool = False,
) -> dict[str, Any]:
    """Select the next task for ``actor``; ``claim=True`` selects and claims in
    one ``BEGIN IMMEDIATE`` transaction (spec 10.4)."""
    actor = validate_agent_id(actor)
    if stage not in STAGES:
        raise ValidationError(
            f"Unknown stage {stage!r}; expected one of {', '.join(STAGES)}."
        )
    if force and not claim:
        raise UsageError("--force is only meaningful together with --claim.")
    if min_priority is not None:
        _check_priority(min_priority)

    arguments: dict[str, Any] = {
        "actor": actor,
        "stage": stage,
        "label": label,
        "min_priority": min_priority,
        "all_assignees": all_assignees,
        "full": full,
    }

    if claim:
        with write_transaction(conn):
            candidates = _candidates(conn, **arguments)
            if not candidates:
                return {"task": None, "tasks": []}
            result = apply_transition(
                conn,
                str(candidates[0]["id"]),
                actor=actor,
                mode="claim",
                force=force,
                reason=reason,
                event_type="task.claimed",
            )
            claimed_id = str(result["task"]["id"])
            candidates = [task for task in candidates if task["id"] != claimed_id]
            return {"task": result["task"], "tasks": candidates}

    with read_transaction(conn):
        candidates = _candidates(conn, **arguments)
    return {"task": candidates[0] if candidates else None, "tasks": candidates}


__all__ = [
    "STAGES",
    "active_claim",
    "claim",
    "list_claims",
    "next_task",
    "parse_duration",
    "release",
]
