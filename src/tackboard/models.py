"""Domain models: enums, transition table, value limits, and row dataclasses.

Spec: section 6 (task model), section 6.3 (priority rank), section 6.4 (IDs),
section 6.5 (labels). Nothing in this module touches the database.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

ID_PREFIX = "T-"
ID_DIGITS = 6

MAX_TITLE_LENGTH = 200
MAX_DESCRIPTION_BYTES = 64 * 1024
MAX_LABELS = 20
MAX_LABEL_LENGTH = 50
MAX_BLOCKED_REASON_BYTES = 2 * 1024
MAX_REASON_LENGTH = 1024
MAX_NOTE_BYTES = 2 * 1024
#: The board-level memo's cap: the same "large free-text field" size as
#: ``description``, byte-measured on the UTF-8 encoding of the stored text.
MAX_MEMO_BYTES = 64 * 1024


class Status(StrEnum):
    """Stored workflow status (spec 6.2)."""

    BACKLOG = "backlog"
    READY = "ready"
    IN_PROGRESS = "in_progress"
    REVIEW = "review"
    DONE = "done"
    CANCELLED = "cancelled"


class Priority(StrEnum):
    """Task priority (spec 6.3)."""

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


STATUSES: tuple[str, ...] = tuple(status.value for status in Status)
PRIORITIES: tuple[str, ...] = tuple(priority.value for priority in Priority)

PRIORITY_RANK: dict[str, int] = {
    Priority.LOW.value: 1,
    Priority.NORMAL.value: 2,
    Priority.HIGH.value: 3,
    Priority.URGENT.value: 4,
}

TERMINAL_STATUSES: frozenset[str] = frozenset({Status.DONE.value, Status.CANCELLED.value})
CLAIMABLE_STATUSES: frozenset[str] = frozenset(
    {Status.READY.value, Status.IN_PROGRESS.value, Status.REVIEW.value}
)

#: Spec 6.2 — the complete transition table. Every status change is checked
#: against this map inside the single transition function.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    Status.BACKLOG.value: frozenset({Status.READY.value, Status.CANCELLED.value}),
    Status.READY.value: frozenset(
        {Status.BACKLOG.value, Status.IN_PROGRESS.value, Status.CANCELLED.value}
    ),
    Status.IN_PROGRESS.value: frozenset(
        {
            Status.READY.value,
            Status.REVIEW.value,
            Status.DONE.value,
            Status.CANCELLED.value,
        }
    ),
    Status.REVIEW.value: frozenset(
        {
            Status.IN_PROGRESS.value,
            Status.DONE.value,
            Status.READY.value,
            Status.CANCELLED.value,
        }
    ),
    Status.DONE.value: frozenset({Status.READY.value}),
    Status.CANCELLED.value: frozenset({Status.BACKLOG.value}),
}

#: Statuses that require an empty ``blocked_reason`` and finished
#: prerequisites to be entered (spec 6.2).
PREREQUISITE_GATED_STATUSES: frozenset[str] = frozenset(
    {Status.IN_PROGRESS.value, Status.REVIEW.value, Status.DONE.value}
)


class UnsetType:
    """Sentinel type for "argument not supplied" defaults."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "UNSET"

    def __bool__(self) -> bool:
        return False


#: Sentinel used as the default for partially-updatable service arguments.
UNSET = UnsetType()


@dataclass(slots=True)
class Task:
    """A row of the ``tasks`` table."""

    id: str
    sequence: int
    title: str
    description: str
    status: str
    priority: str
    assignee: str | None
    blocked_reason: str | None
    created_at: str
    updated_at: str
    completed_at: str | None
    version: int

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Task:
        return cls(
            id=row["id"],
            sequence=row["sequence"],
            title=row["title"],
            description=row["description"],
            status=row["status"],
            priority=row["priority"],
            assignee=row["assignee"],
            blocked_reason=row["blocked_reason"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            completed_at=row["completed_at"],
            version=row["version"],
        )


@dataclass(slots=True)
class Claim:
    """A row of the ``claims`` table (at most one per task)."""

    task_id: str
    agent_id: str
    claimed_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Claim:
        return cls(
            task_id=row["task_id"],
            agent_id=row["agent_id"],
            claimed_at=row["claimed_at"],
        )


@dataclass(slots=True)
class Event:
    """A row of the append-only ``events`` table."""

    id: int
    timestamp: str
    actor: str
    event_type: str
    task_id: str | None
    payload: dict[str, Any]


def utcnow_iso() -> str:
    """Return the current UTC time as fixed-width ISO-8601 with a ``Z`` suffix."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def utcstamp_iso() -> str:
    """Return a UTC stamp with microsecond precision, for backup filenames."""
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")


def task_id_for_sequence(sequence: int) -> str:
    """Render a task ID from its integer sequence (spec 6.4)."""
    return f"{ID_PREFIX}{sequence:0{ID_DIGITS}d}"


__all__ = [
    "ALLOWED_TRANSITIONS",
    "CLAIMABLE_STATUSES",
    "ID_DIGITS",
    "ID_PREFIX",
    "MAX_BLOCKED_REASON_BYTES",
    "MAX_DESCRIPTION_BYTES",
    "MAX_LABEL_LENGTH",
    "MAX_LABELS",
    "MAX_MEMO_BYTES",
    "MAX_NOTE_BYTES",
    "MAX_REASON_LENGTH",
    "MAX_TITLE_LENGTH",
    "PRIORITIES",
    "PRIORITY_RANK",
    "PREREQUISITE_GATED_STATUSES",
    "STATUSES",
    "TERMINAL_STATUSES",
    "UNSET",
    "Claim",
    "Event",
    "Priority",
    "Status",
    "Task",
    "UnsetType",
    "task_id_for_sequence",
    "utcnow_iso",
    "utcstamp_iso",
]
