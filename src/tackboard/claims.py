"""Claims and multi-agent concurrency (spec section 8).

The claim/status policy lives in exactly one place — ``service.apply_transition``
— so every entry point applies the same table. This module is the public claim
surface plus the atomic ``next --claim`` selector.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from .config import stale_claim_after, validate_agent_id
from .db import read_transaction, write_transaction
from .errors import UsageError, ValidationError
from .models import PRIORITY_RANK, utcnow_iso
from .service import (
    _check_priority,
    _label_key,
    _last_in_progress_actor,
    _project_rows,
    apply_transition,
)

STAGES: tuple[str, ...] = ("work", "review")


class _Unset:
    """Sentinel type for "no assignee selector was given".

    ``None`` cannot serve as the default: an explicit empty-string selector
    (``--assignee ""``) means "unassigned work only", so the absence of the
    option needs its own marker to keep today's routing behaviour intact.
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return "_UNSET"


_UNSET: Final = _Unset()

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


def stale_claim_seconds(start: Path | None = None) -> int:
    """The default stale-claim threshold in seconds.

    Reads the optional ``[tackboard] stale_claim_after`` key from the nearest
    ``.tackboard.toml`` (default ``2h``). An unparseable value raises the
    duration parser's validation error — never a silent fallback.
    """
    return parse_duration(stale_claim_after(start if start is not None else Path.cwd()))


def _age_seconds(claimed_at: str, now: str | None = None) -> int:
    moment = datetime.strptime(claimed_at, "%Y-%m-%dT%H:%M:%SZ")
    current = now if now is not None else utcnow_iso()
    elapsed = datetime.strptime(current, "%Y-%m-%dT%H:%M:%SZ") - moment
    return max(0, int(elapsed.total_seconds()))


def stale_claim_count(conn: sqlite3.Connection) -> int:
    """How many active claims are at least the configured threshold old.

    A count, not a list: it is a hint that recovery needs attention, small
    enough to ride along in every ``next`` document. ``claims --stale`` remains
    the command that names them.
    """
    threshold = stale_claim_seconds()
    now = utcnow_iso()
    rows = conn.execute("SELECT claimed_at FROM claims").fetchall()
    return sum(1 for row in rows if _age_seconds(str(row["claimed_at"]), now) >= threshold)


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


def recover(
    conn: sqlite3.Connection,
    task_id: str,
    *,
    actor: str,
    reason: str,
    if_version: int | None = None,
) -> dict[str, Any]:
    """Resolve a dead claim by releasing it with an audited reason.

    A thin wrapper over ``release --force --reason``: it maps to the two
    enumerated force cases that resolve a claim nobody is working — a claim
    held by another agent, and an ``in_progress`` task that has lost its claim.
    It adds no transition and no force case of its own; ``apply_transition``
    still decides whether the forced release applies. A healthy unclaimed task
    is an idempotent no-op; a claim the caller already holds is refused by the
    existing force check (plain ``release`` is the command for that).
    """
    return release(
        conn,
        task_id,
        actor=actor,
        force=True,
        reason=reason,
        if_version=if_version,
    )


def list_claims(
    conn: sqlite3.Connection,
    *,
    agent: str | None = None,
    older_than: str | None = None,
    stale: bool = False,
) -> dict[str, Any]:
    """Inspect active claims, optionally by agent or minimum age (spec 8.1).

    ``stale=True`` filters to claims at least as old as the configured
    ``stale_claim_after`` threshold; an explicit ``older_than`` wins over it.
    """
    if agent is not None:
        validate_agent_id(agent)
    if older_than is not None:
        threshold: int | None = parse_duration(older_than)
    elif stale:
        threshold = stale_claim_seconds()
    else:
        threshold = None
    now = utcnow_iso()

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
                now=now,
            )
        }

    items: list[dict[str, Any]] = []
    for row in rows:
        age = _age_seconds(row["claimed_at"], now)
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
    assignee: str | None | _Unset = _UNSET,
    full: bool,
    different_from_author: bool = False,
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
    if assignee is _UNSET:
        if not all_assignees:
            tasks = [task for task in tasks if task["assignee"] in (None, actor)]
    else:
        # An explicit selector replaces the default "unassigned or mine"
        # disjunction: "" means the unassigned pool only; any other value means
        # exactly that route, with no unassigned fallback.
        selector = None if assignee == "" else assignee
        tasks = [task for task in tasks if task["assignee"] == selector]
    if min_priority is not None:
        floor = PRIORITY_RANK[min_priority]
        tasks = [
            task for task in tasks if PRIORITY_RANK[str(task["priority"])] >= floor
        ]
    if different_from_author:
        authors = _last_in_progress_actor(conn, [str(task["id"]) for task in tasks])
        tasks = [task for task in tasks if authors.get(str(task["id"])) != actor]
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
    assignee: str | None | _Unset = _UNSET,
    full: bool = False,
    different_from_author: bool = False,
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
    if different_from_author and stage != "review":
        raise UsageError(
            "--different-from-author applies only to --stage review."
        )
    if different_from_author and not claim:
        raise UsageError(
            "--different-from-author is only meaningful together with --claim."
        )
    if assignee is not _UNSET and assignee is not None:
        if assignee != "":
            try:
                validate_agent_id(str(assignee))
            except UsageError as exc:
                # A malformed route is a value error (exit 4), unlike the
                # usage-error class the validator raises for --agent.
                raise ValidationError(str(exc)) from None
        if all_assignees:
            raise UsageError(
                "--assignee cannot be combined with --all-assignees; "
                "choose one selection policy."
            )

    arguments: dict[str, Any] = {
        "actor": actor,
        "stage": stage,
        "label": label,
        "min_priority": min_priority,
        "all_assignees": all_assignees,
        "assignee": assignee,
        "full": full,
        "different_from_author": different_from_author,
    }

    if claim:
        with write_transaction(conn):
            candidates = _candidates(conn, **arguments)
            if not candidates:
                return {"task": None, "tasks": [], "stale_claims": stale_claim_count(conn)}
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
            return {
                "task": result["task"],
                "tasks": candidates,
                "stale_claims": stale_claim_count(conn),
            }

    with read_transaction(conn):
        candidates = _candidates(conn, **arguments)
        stale_claims = stale_claim_count(conn)
    return {
        "task": candidates[0] if candidates else None,
        "tasks": candidates,
        "stale_claims": stale_claims,
    }


__all__ = [
    "STAGES",
    "active_claim",
    "claim",
    "list_claims",
    "next_task",
    "parse_duration",
    "recover",
    "release",
    "stale_claim_count",
    "stale_claim_seconds",
]
