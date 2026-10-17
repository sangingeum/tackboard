"""Domain service layer: task CRUD, the single transition function, notes.

Spec: section 6 (task model), section 7.4 (derived readiness), section 8.2
(the claim/status policy — implemented once, in ``apply_transition``), and
section 9 (history events). Every status change in the tool goes through
``apply_transition``.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from . import history
from .config import validate_agent_id
from .db import read_transaction, write_transaction
from .errors import (
    ClaimConflictError,
    InvalidTransitionError,
    NotFoundError,
    StaleVersionError,
    ValidationError,
)
from .models import (
    ALLOWED_TRANSITIONS,
    CLAIMABLE_STATUSES,
    MAX_BLOCKED_REASON_BYTES,
    MAX_DESCRIPTION_BYTES,
    MAX_LABEL_LENGTH,
    MAX_LABELS,
    MAX_NOTE_BYTES,
    MAX_REASON_LENGTH,
    MAX_TITLE_LENGTH,
    PREREQUISITE_GATED_STATUSES,
    PRIORITIES,
    PRIORITY_RANK,
    STATUSES,
    TERMINAL_STATUSES,
    UNSET,
    Claim,
    Task,
    UnsetType,
    task_id_for_sequence,
    utcnow_iso,
)
from .schema import database_name

_CANCELLED = "cancelled"
_DONE = "done"
_IN_PROGRESS = "in_progress"
_READY = "ready"
_REVIEW = "review"
_BACKLOG = "backlog"

_SORT_FIELDS: frozenset[str] = frozenset(
    {"priority", "status", "created_at", "updated_at", "id", "title"}
)

_FORCE_REQUIRES_REASON = "--force requires --reason with a non-empty explanation."
_FORCE_NOT_APPLICABLE = (
    "--force is not allowed here: it applies only to the enumerated cases "
    "(takeover, unfinished prerequisites, adding an unfinished prerequisite "
    "to active work, recovery of an unclaimed in_progress task)."
)


# --------------------------------------------------------------------------- #
# validation helpers
# --------------------------------------------------------------------------- #


def _actor(actor: str) -> str:
    return validate_agent_id(actor)


def _check_title(title: str) -> str:
    if not isinstance(title, str):
        raise ValidationError("Title must be a string.")
    stripped = title.strip()
    if not stripped:
        raise ValidationError("Title must not be empty.")
    if len(stripped) > MAX_TITLE_LENGTH:
        raise ValidationError(
            f"Title is too long: {len(stripped)} characters, maximum is "
            f"{MAX_TITLE_LENGTH}.",
            details={
                "field": "title",
                "max": MAX_TITLE_LENGTH,
                "unit": "characters",
                "actual": len(stripped),
            },
        )
    return stripped


def _check_description(description: str) -> str:
    if not isinstance(description, str):
        raise ValidationError("Description must be a string.")
    if len(description.encode("utf-8")) > MAX_DESCRIPTION_BYTES:
        raise ValidationError(
            f"Description is too long: maximum is {MAX_DESCRIPTION_BYTES} bytes.",
            details={
                "field": "description",
                "max": MAX_DESCRIPTION_BYTES,
                "unit": "bytes",
                "actual": len(description.encode("utf-8")),
            },
        )
    return description


def _check_priority(priority: str) -> str:
    if priority not in PRIORITIES:
        raise ValidationError(
            f"Unknown priority {priority!r}; expected one of {', '.join(PRIORITIES)}."
        )
    return priority


def _check_status(status: str) -> str:
    if status not in STATUSES:
        raise ValidationError(
            f"Unknown status {status!r}; expected one of {', '.join(STATUSES)}."
        )
    return status


def _check_assignee(assignee: str | None) -> str | None:
    if assignee is None or assignee == "":
        return None
    return validate_agent_id(assignee)


def _check_blocked_reason(reason: str | None) -> str | None:
    if reason is None:
        return None
    if not isinstance(reason, str):
        raise ValidationError("Blocked reason must be a string.")
    stripped = reason.strip()
    if not stripped:
        return None
    if len(stripped.encode("utf-8")) > MAX_BLOCKED_REASON_BYTES:
        raise ValidationError(
            f"Blocked reason is too long: maximum is {MAX_BLOCKED_REASON_BYTES} bytes.",
            details={
                "field": "blocked_reason",
                "max": MAX_BLOCKED_REASON_BYTES,
                "unit": "bytes",
                "actual": len(stripped.encode("utf-8")),
            },
        )
    return stripped


def _check_note(text: str) -> str:
    if not isinstance(text, str) or not text.strip():
        raise ValidationError("Note text must not be empty.")
    if len(text.encode("utf-8")) > MAX_NOTE_BYTES:
        raise ValidationError(
            f"Note is too long: maximum is {MAX_NOTE_BYTES} bytes.",
            details={
                "field": "note",
                "max": MAX_NOTE_BYTES,
                "unit": "bytes",
                "actual": len(text.encode("utf-8")),
            },
        )
    return text


def _check_force_reason(force: bool, reason: str | None) -> str | None:
    if not force:
        return None
    if reason is None or not reason.strip():
        raise ValidationError(_FORCE_REQUIRES_REASON)
    trimmed = reason.strip()
    if len(trimmed) > MAX_REASON_LENGTH:
        raise ValidationError(
            f"--reason is too long: maximum is {MAX_REASON_LENGTH} characters.",
            details={
                "field": "reason",
                "max": MAX_REASON_LENGTH,
                "unit": "characters",
                "actual": len(trimmed),
            },
        )
    return trimmed


def _normalize_labels(labels: Iterable[str]) -> list[tuple[str, str]]:
    """Return ``(label, label_key)`` pairs, trimmed, deduped, in first-seen order."""
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw in labels:
        if not isinstance(raw, str):
            raise ValidationError("Labels must be strings.")
        label = raw.strip()
        if not label:
            continue
        if len(label) > MAX_LABEL_LENGTH:
            raise ValidationError(
                f"Label {label!r} is too long: maximum is {MAX_LABEL_LENGTH} characters.",
                details={
                    "field": "label",
                    "max": MAX_LABEL_LENGTH,
                    "unit": "characters",
                    "actual": len(label),
                },
            )
        key = label.casefold()
        if key in seen:
            continue
        seen.add(key)
        pairs.append((label, key))
    if len(pairs) > MAX_LABELS:
        raise ValidationError(
            f"Too many labels: {len(pairs)}, maximum is {MAX_LABELS} per task.",
            details={
                "field": "labels",
                "max": MAX_LABELS,
                "unit": "labels",
                "actual": len(pairs),
            },
        )
    return pairs


def _label_key(label: str) -> str:
    return label.strip().casefold()


# --------------------------------------------------------------------------- #
# row loading and projection
# --------------------------------------------------------------------------- #


def _task_row(conn: sqlite3.Connection, task_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise NotFoundError(
            f"Task {task_id} not found in {database_name(conn)}."
        )
    return row


def _load_task(conn: sqlite3.Connection, task_id: str) -> Task:
    return Task.from_row(_task_row(conn, task_id))


def _active_claim(conn: sqlite3.Connection, task_id: str) -> Claim | None:
    row = conn.execute("SELECT * FROM claims WHERE task_id = ?", (task_id,)).fetchone()
    return Claim.from_row(row) if row is not None else None


def _assert_not_claimed_by_other(conn: sqlite3.Connection, task_id: str, actor: str) -> None:
    claim = _active_claim(conn, task_id)
    if claim is not None and claim.agent_id != actor:
        raise ClaimConflictError(
            f"Cannot modify {task_id}: it is claimed by agent {claim.agent_id}.",
            details={"claimed_by": claim.agent_id},
        )


def _labels_for(
    conn: sqlite3.Connection, task_ids: Sequence[str]
) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {task_id: [] for task_id in task_ids}
    if not task_ids:
        return result
    placeholders = ",".join("?" for _ in task_ids)
    rows = conn.execute(
        f"SELECT task_id, label FROM task_labels WHERE task_id IN ({placeholders}) "
        "ORDER BY label_key",
        list(task_ids),
    ).fetchall()
    for row in rows:
        result[str(row["task_id"])].append(str(row["label"]))
    return result


def _prerequisites_for(
    conn: sqlite3.Connection, task_ids: Sequence[str]
) -> dict[str, list[tuple[str, str]]]:
    """Return ``{task_id: [(prerequisite_id, prerequisite_status), ...]}``."""
    result: dict[str, list[tuple[str, str]]] = {task_id: [] for task_id in task_ids}
    if not task_ids:
        return result
    placeholders = ",".join("?" for _ in task_ids)
    rows = conn.execute(
        f"""
        SELECT d.task_id AS task_id, d.prerequisite_id AS prerequisite_id,
               p.status AS status
        FROM dependencies d
        JOIN tasks p ON p.id = d.prerequisite_id
        WHERE d.task_id IN ({placeholders})
        ORDER BY p.sequence
        """,
        list(task_ids),
    ).fetchall()
    for row in rows:
        result[str(row["task_id"])].append(
            (str(row["prerequisite_id"]), str(row["status"]))
        )
    return result


def _claims_for(conn: sqlite3.Connection, task_ids: Sequence[str]) -> dict[str, Claim]:
    if not task_ids:
        return {}
    placeholders = ",".join("?" for _ in task_ids)
    rows = conn.execute(
        f"SELECT * FROM claims WHERE task_id IN ({placeholders})", list(task_ids)
    ).fetchall()
    return {str(row["task_id"]): Claim.from_row(row) for row in rows}


def _blockers(
    prerequisites: Sequence[tuple[str, str]], blocked_reason: str | None
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for prerequisite_id, status in prerequisites:
        if status == _CANCELLED:
            entries.append(
                {"code": "prerequisite_cancelled", "task_id": prerequisite_id}
            )
        elif status != _DONE:
            entries.append(
                {"code": "prerequisite_unfinished", "task_id": prerequisite_id}
            )
    if blocked_reason:
        entries.append({"code": "explicit", "reason": blocked_reason})
    return entries


#: Blocker codes a forced start into ``in_progress`` is allowed to cover
#: (spec 6.2 case 2; spec 8.2 row 1): an unfinished or a cancelled
#: prerequisite. An explicit ``blocked_reason`` yields the ``explicit`` code
#: and is therefore never covered.
_FORCEABLE_PREREQUISITE_CODES: frozenset[str] = frozenset(
    {"prerequisite_unfinished", "prerequisite_cancelled"}
)


def _force_covers_prerequisites(
    prerequisites: Sequence[tuple[str, str]], blocked_reason: str | None
) -> bool:
    """The one force policy for entering ``in_progress`` (spec 6.2, 8.2).

    A forced start may proceed when there is at least one prerequisite
    blocker and every blocker is an unfinished or cancelled prerequisite.
    An explicit ``blocked_reason`` always refuses force, because it adds an
    ``explicit`` blocker. The ``status`` and ``claim`` entry points both
    consult this predicate, so the policy table lives in exactly one place.
    """
    blockers = _blockers(prerequisites, blocked_reason)
    return bool(blockers) and all(
        entry["code"] in _FORCEABLE_PREREQUISITE_CODES for entry in blockers
    )


def derived_flags(
    prerequisites: Sequence[tuple[str, str]],
    blocked_reason: str | None,
    status: str,
    claim: Claim | None,
) -> dict[str, Any]:
    """Compute the spec 7.4 derived flags from stored state."""
    dependency_ready = all(state == _DONE for _, state in prerequisites)
    blockers = _blockers(prerequisites, blocked_reason)
    blocked = status not in TERMINAL_STATUSES and bool(blockers)
    eligible = (
        status == _READY
        and dependency_ready
        and blocked_reason is None
        and claim is None
    )
    reviewable = (
        status == _REVIEW
        and claim is None
        and blocked_reason is None
        and dependency_ready
    )
    return {
        "dependency_ready": dependency_ready,
        "eligible": eligible,
        "reviewable": reviewable,
        "blocked": blocked,
        "blockers": blockers,
    }


def _project_row(
    row: sqlite3.Row,
    labels: Sequence[str],
    prerequisites: Sequence[tuple[str, str]],
    claim: Claim | None,
    *,
    full: bool,
) -> dict[str, Any]:
    flags = derived_flags(prerequisites, row["blocked_reason"], row["status"], claim)
    task: dict[str, Any] = {
        "id": row["id"],
        "title": row["title"],
    }
    if full:
        task["description"] = row["description"]
    task.update(
        {
            "status": row["status"],
            "priority": row["priority"],
            "assignee": row["assignee"],
            "labels": list(labels),
            "depends_on": [prerequisite_id for prerequisite_id, _ in prerequisites],
            "dependency_ready": flags["dependency_ready"],
            "eligible": flags["eligible"],
            "reviewable": flags["reviewable"],
            "blocked": flags["blocked"],
            "blocked_reason": row["blocked_reason"],
            "blockers": flags["blockers"],
            "claimed_by": claim.agent_id if claim is not None else None,
            "claimed_at": claim.claimed_at if claim is not None else None,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "completed_at": row["completed_at"],
            "version": row["version"],
        }
    )
    return task


def _project_rows(
    conn: sqlite3.Connection, rows: Sequence[sqlite3.Row], *, full: bool
) -> list[dict[str, Any]]:
    task_ids = [str(row["id"]) for row in rows]
    labels = _labels_for(conn, task_ids)
    prerequisites = _prerequisites_for(conn, task_ids)
    claims = _claims_for(conn, task_ids)
    return [
        _project_row(
            row,
            labels[str(row["id"])],
            prerequisites[str(row["id"])],
            claims.get(str(row["id"])),
            full=full,
        )
        for row in rows
    ]


def project_task(task: dict[str, Any], *, full: bool = False) -> dict[str, Any]:
    """Return the spec 10.5 projection of a task, with or without description."""
    if full:
        return dict(task)
    return {key: value for key, value in task.items() if key != "description"}


def project_tasks_by_id(
    conn: sqlite3.Connection,
    task_ids: Sequence[str],
    *,
    full: bool = False,
) -> list[dict[str, Any]]:
    """Project the named tasks, preserving the given order (spec 10.5).

    The board, the dependency graph and the task detail page all describe tasks
    with one projection; this is the entry point for callers that already know
    which task IDs they need.
    """
    if not task_ids:
        return []
    placeholders = ",".join("?" for _ in task_ids)
    rows = conn.execute(
        f"SELECT * FROM tasks WHERE id IN ({placeholders})", list(task_ids)
    ).fetchall()
    by_id: dict[str, sqlite3.Row] = {str(row["id"]): row for row in rows}
    ordered = [by_id[task_id] for task_id in task_ids if task_id in by_id]
    return _project_rows(conn, ordered, full=full)


# --------------------------------------------------------------------------- #
# shared mutation helpers
# --------------------------------------------------------------------------- #


def _insert_claim(conn: sqlite3.Connection, task_id: str, actor: str) -> None:
    conn.execute(
        """
        INSERT INTO claims(task_id, agent_id, claimed_at) VALUES(?, ?, ?)
        ON CONFLICT(task_id) DO UPDATE SET
            agent_id = excluded.agent_id,
            claimed_at = CASE
                WHEN claims.agent_id = excluded.agent_id
                THEN claims.claimed_at
                ELSE excluded.claimed_at
            END
        """,
        (task_id, actor, utcnow_iso()),
    )


def _clear_claim(conn: sqlite3.Connection, task_id: str) -> None:
    conn.execute("DELETE FROM claims WHERE task_id = ?", (task_id,))


def _update_task_row(
    conn: sqlite3.Connection,
    task: Task,
    *,
    status: str,
    completed_at: str | None,
) -> None:
    now = utcnow_iso()
    cursor = conn.execute(
        """
        UPDATE tasks
        SET status = ?, completed_at = ?, updated_at = ?, version = ?
        WHERE id = ? AND version = ?
        """,
        (status, completed_at, now, task.version + 1, task.id, task.version),
    )
    if cursor.rowcount != 1:
        row = conn.execute(
            "SELECT version FROM tasks WHERE id = ?", (task.id,)
        ).fetchone()
        if row is None:
            raise NotFoundError(f"Task {task.id} no longer exists.")
        raise StaleVersionError(
            f"Stale version for {task.id}: the task changed during this "
            f"operation (expected {task.version}, current {row['version']}).",
            details={"expected": task.version, "current": int(row["version"])},
        )


class _Transition:
    """Mutable plan produced by the policy checks, applied in one transaction."""

    __slots__ = (
        "acquire_claim",
        "clear_claim",
        "force_used",
        "new_status",
        "payload",
    )

    def __init__(self, status: str) -> None:
        self.new_status = status
        self.acquire_claim = False
        self.clear_claim = False
        self.force_used = False
        self.payload: dict[str, Any] = {}


# --------------------------------------------------------------------------- #
# THE single transition function
# --------------------------------------------------------------------------- #


def apply_transition(
    conn: sqlite3.Connection,
    task_id: str,
    *,
    actor: str,
    mode: str = "status",
    status: str | None = None,
    if_version: int | None = None,
    force: bool = False,
    reason: str | None = None,
    event_type: str | None = None,
    payload_extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The one transition function (spec 6.2, 8.2).

    ``mode="status"`` is the ``status``/lifecycle path, ``mode="claim"`` is
    ``claim``/``next --claim``, ``mode="release"`` is ``release``. Every status
    change in the tool flows through here, so the policy lives in one place.
    """
    actor = _actor(actor)
    if mode not in {"status", "claim", "release"}:
        raise ValidationError(f"Unknown transition mode {mode!r}.")
    forced_reason = _check_force_reason(force, reason)

    with write_transaction(conn):
        task = _load_task(conn, task_id)
        if if_version is not None and task.version != if_version:
            raise StaleVersionError(
                f"Stale version for {task_id}: expected {if_version}, "
                f"current {task.version}.",
                details={"expected": if_version, "current": task.version},
            )
        claim = _active_claim(conn, task_id)
        prerequisites = _prerequisites_for(conn, [task_id])[task_id]
        plan = _Transition(task.status)

        if mode == "status":
            assert status is not None
            changed = _plan_status(
                plan, task, claim, prerequisites, status, force=force, actor=actor
            )
            default_event = "task.status_changed"
        elif mode == "claim":
            changed = _plan_claim(
                plan, task, claim, prerequisites, force=force, actor=actor
            )
            default_event = "task.claimed"
        else:
            changed = _plan_release(plan, task, claim, force=force, actor=actor)
            default_event = "task.released"

        if not changed:
            return {"changed": False, "task": _project_task(conn, task_id)}

        if force and not plan.force_used:
            raise ValidationError(_FORCE_NOT_APPLICABLE)

        completed_at = task.completed_at
        if plan.new_status == _DONE:
            completed_at = utcnow_iso()
        elif task.status == _DONE and plan.new_status != _DONE:
            completed_at = None

        _update_task_row(
            conn,
            task,
            status=plan.new_status,
            completed_at=completed_at,
        )
        if plan.acquire_claim:
            _insert_claim(conn, task_id, actor)
        if plan.clear_claim:
            _clear_claim(conn, task_id)

        payload: dict[str, Any] = {"from": task.status, "to": plan.new_status}
        payload.update(dict(payload_extra or {}))
        payload.update(plan.payload)
        if plan.force_used:
            payload["forced"] = True
            payload["reason"] = forced_reason

        history.record_event(
            conn,
            event_type=event_type or default_event,
            actor=actor,
            task_id=task_id,
            payload=payload,
        )
        return {"changed": True, "task": _project_task(conn, task_id)}


def _plan_status(
    plan: _Transition,
    task: Task,
    claim: Claim | None,
    prerequisites: Sequence[tuple[str, str]],
    status: str,
    *,
    force: bool,
    actor: str,
) -> bool:
    """Plan a ``status`` transition. Returns False for an idempotent no-op."""
    target = _check_status(status)
    if target == task.status:
        return False
    if target not in ALLOWED_TRANSITIONS[task.status]:
        raise InvalidTransitionError(
            f"Cannot change {task.id} from {task.status} to {target}: "
            "that transition is not allowed."
        )

    if target in PREREQUISITE_GATED_STATUSES:
        if task.blocked_reason:
            raise ValidationError(
                f"Cannot move {task.id} to {target}: it has an explicit blocked "
                "reason; clear it first."
            )
        unfinished = [
            prerequisite_id
            for prerequisite_id, state in prerequisites
            if state != _DONE
        ]
        if unfinished:
            if (
                target == _IN_PROGRESS
                and force
                and _force_covers_prerequisites(prerequisites, task.blocked_reason)
            ):
                plan.force_used = True
            else:
                raise ValidationError(
                    f"Cannot start {task.id}: prerequisite {unfinished[0]} is not done."
                )

    leaving_claimed_state = (
        task.status in {_IN_PROGRESS, _REVIEW} and target != _IN_PROGRESS
    )
    if (
        claim is not None
        and claim.agent_id != actor
        and (leaving_claimed_state or target == _IN_PROGRESS)
    ):
        raise ClaimConflictError(
            f"Cannot modify {task.id}: it is claimed by agent {claim.agent_id}.",
            details={"claimed_by": claim.agent_id},
        )
    if leaving_claimed_state and claim is None and task.status == _IN_PROGRESS:
        if not force:
            raise ValidationError(
                f"Cannot move {task.id} out of in_progress: it has no claim. "
                "Use --force --reason to recover it."
            )
        plan.force_used = True
    if target == _IN_PROGRESS:
        plan.acquire_claim = True
        # The assignment is a routing note: entering in_progress takes a claim
        # but never writes it. When the card is routed to a different agent,
        # record that routing in the event exactly as the claim path does, so
        # the mismatch is visible in history rather than silently normalised.
        if task.assignee is not None and task.assignee != actor:
            plan.payload["routed_to"] = task.assignee
    if task.status in {_IN_PROGRESS, _REVIEW} and target != _IN_PROGRESS:
        plan.clear_claim = claim is not None

    plan.new_status = target
    return True


def _plan_claim(
    plan: _Transition,
    task: Task,
    claim: Claim | None,
    prerequisites: Sequence[tuple[str, str]],
    *,
    force: bool,
    actor: str,
) -> bool:
    """Plan a ``claim`` (or ``next --claim``). Returns False for an idempotent no-op."""
    if claim is not None and claim.agent_id == actor:
        return False
    if claim is not None:
        if not force:
            raise ClaimConflictError(
                f"Cannot claim {task.id}: already claimed by agent {claim.agent_id}.",
                details={"claimed_by": claim.agent_id},
            )
        plan.force_used = True
        plan.payload["takeover_from"] = claim.agent_id

    if task.status not in CLAIMABLE_STATUSES:
        raise InvalidTransitionError(
            f"Cannot claim {task.id}: its status is {task.status}."
        )

    if task.status == _READY:
        flags = derived_flags(prerequisites, task.blocked_reason, task.status, None)
        if not flags["eligible"]:
            if force and _force_covers_prerequisites(
                prerequisites, task.blocked_reason
            ):
                plan.force_used = True
            elif task.blocked_reason:
                raise ValidationError(
                    f"Cannot claim {task.id}: it has an explicit blocked reason; "
                    "clear it first."
                )
            else:
                raise ValidationError(
                    f"Cannot claim {task.id}: it is not eligible for work."
                )
        plan.new_status = _IN_PROGRESS
    elif task.status == _REVIEW:
        flags = derived_flags(prerequisites, task.blocked_reason, task.status, None)
        if not flags["reviewable"]:
            raise ValidationError(
                f"Cannot claim {task.id} for review: it is not reviewable."
            )
        plan.new_status = _REVIEW
    else:  # unclaimed in_progress: recovery only (spec 6.2 case 5)
        if not force:
            raise ValidationError(
                f"Cannot claim {task.id}: it is in_progress without a claim. "
                "Use --force --reason to recover it."
            )
        plan.force_used = True
        plan.new_status = _IN_PROGRESS

    plan.acquire_claim = True
    # The assignment is a routing note: a claim never writes it. When the card
    # is routed to a different agent, record that routing in the claim event so
    # the mismatch is visible in history rather than silently repaired.
    if task.assignee is not None and task.assignee != actor:
        plan.payload["routed_to"] = task.assignee
    return True


def _plan_release(
    plan: _Transition,
    task: Task,
    claim: Claim | None,
    *,
    force: bool,
    actor: str,
) -> bool:
    """Plan a ``release``. Returns False for an idempotent no-op."""
    if claim is None:
        if task.status == _IN_PROGRESS:
            if not force:
                raise ValidationError(
                    f"Cannot release {task.id}: it is in_progress without a claim. "
                    "Use --force --reason to recover it."
                )
            plan.force_used = True
            plan.new_status = _READY
            return True
        return False
    if claim.agent_id != actor:
        if not force:
            raise ClaimConflictError(
                f"Cannot release {task.id}: it is claimed by agent {claim.agent_id}.",
                details={"claimed_by": claim.agent_id},
            )
        plan.force_used = True
        plan.payload["released_agent"] = claim.agent_id
    plan.clear_claim = True
    if task.status == _IN_PROGRESS:
        plan.new_status = _READY
    return True


# --------------------------------------------------------------------------- #
# reads
# --------------------------------------------------------------------------- #


def _project_task(conn: sqlite3.Connection, task_id: str) -> dict[str, Any]:
    row = _task_row(conn, task_id)
    return _project_rows(conn, [row], full=True)[0]


def get_task(conn: sqlite3.Connection, task_id: str) -> dict[str, Any]:
    """Return the full spec 10.5 task object, including description."""
    with read_transaction(conn):
        return _project_task(conn, task_id)


# --------------------------------------------------------------------------- #
# public operations
# --------------------------------------------------------------------------- #


def _set_status(
    conn: sqlite3.Connection,
    task_id: str,
    status: str,
    *,
    actor: str,
    if_version: int | None = None,
    force: bool = False,
    reason: str | None = None,
    event_type: str | None = None,
) -> dict[str, Any]:
    return apply_transition(
        conn,
        task_id,
        actor=actor,
        mode="status",
        status=status,
        if_version=if_version,
        force=force,
        reason=reason,
        event_type=event_type,
    )


def set_status(
    conn: sqlite3.Connection,
    task_id: str,
    status: str,
    *,
    actor: str,
    if_version: int | None = None,
    force: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """THE single transition function (spec 6.2)."""
    return _set_status(
        conn,
        task_id,
        status,
        actor=actor,
        if_version=if_version,
        force=force,
        reason=reason,
        event_type="task.status_changed",
    )


def complete_task(
    conn: sqlite3.Connection,
    task_id: str,
    *,
    actor: str,
    if_version: int | None = None,
    force: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """Shortcut for ``status ... done``."""
    return _set_status(
        conn,
        task_id,
        _DONE,
        actor=actor,
        if_version=if_version,
        force=force,
        reason=reason,
        event_type="task.completed",
    )


def reopen_task(
    conn: sqlite3.Connection,
    task_id: str,
    *,
    actor: str,
    if_version: int | None = None,
    force: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """Shortcut for ``status ... ready``; reports active dependents (spec 7.3)."""
    result = _set_status(
        conn,
        task_id,
        _READY,
        actor=actor,
        if_version=if_version,
        force=force,
        reason=reason,
        event_type="task.reopened",
    )
    if result["changed"]:
        result["active_dependents"] = _active_dependents(conn, task_id)
    return result


def cancel_task(
    conn: sqlite3.Connection,
    task_id: str,
    *,
    actor: str,
    if_version: int | None = None,
    force: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """Shortcut for ``status ... cancelled``."""
    return _set_status(
        conn,
        task_id,
        _CANCELLED,
        actor=actor,
        if_version=if_version,
        force=force,
        reason=reason,
        event_type="task.cancelled",
    )


def restore_task(
    conn: sqlite3.Connection,
    task_id: str,
    *,
    actor: str,
    if_version: int | None = None,
    force: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """Shortcut for ``status ... backlog``."""
    return _set_status(
        conn,
        task_id,
        _BACKLOG,
        actor=actor,
        if_version=if_version,
        force=force,
        reason=reason,
        event_type="task.restored",
    )


def _active_dependents(conn: sqlite3.Connection, task_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT t.* FROM tasks t
        JOIN dependencies d ON d.task_id = t.id
        WHERE d.prerequisite_id = ? AND t.status IN ('in_progress', 'review')
        ORDER BY t.sequence
        """,
        (task_id,),
    ).fetchall()
    return _project_rows(conn, rows, full=False)


def create_task(
    conn: sqlite3.Connection,
    *,
    actor: str,
    title: str,
    description: str = "",
    priority: str = "normal",
    assignee: str | None = None,
    labels: Iterable[str] = (),
    status: str = "backlog",
    depends_on: Iterable[str] = (),
    if_version: int | None = None,
) -> dict[str, Any]:
    """Create a task and its dependency edges atomically (spec 6.6)."""
    actor = _actor(actor)
    clean_title = _check_title(title)
    clean_description = _check_description(description)
    clean_priority = _check_priority(priority)
    clean_assignee = _check_assignee(assignee)
    clean_status = _check_status(status)
    if clean_status in {_IN_PROGRESS, _REVIEW, _DONE}:
        raise ValidationError(
            f"Cannot create a task as {clean_status}; use claim and status "
            "transitions instead."
        )
    if if_version is not None:
        raise ValidationError("--if-version does not apply to task creation.")
    label_pairs = _normalize_labels(labels)
    prerequisite_ids = list(dict.fromkeys(str(item) for item in depends_on))

    with write_transaction(conn):
        for prerequisite_id in prerequisite_ids:
            _task_row(conn, prerequisite_id)
        row = conn.execute(
            "SELECT value FROM metadata WHERE key = 'next_sequence'"
        ).fetchone()
        sequence = int(row["value"]) if row is not None else 1
        task_id = task_id_for_sequence(sequence)
        conn.execute(
            "UPDATE metadata SET value = ? WHERE key = 'next_sequence'",
            (str(sequence + 1),),
        )
        now = utcnow_iso()
        conn.execute(
            """
            INSERT INTO tasks(
                id, sequence, title, description, status, priority, assignee,
                blocked_reason, created_at, updated_at, completed_at, version
            ) VALUES(?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, NULL, 1)
            """,
            (
                task_id,
                sequence,
                clean_title,
                clean_description,
                clean_status,
                clean_priority,
                clean_assignee,
                now,
                now,
            ),
        )
        for label, key in label_pairs:
            conn.execute(
                "INSERT INTO task_labels(task_id, label, label_key) VALUES(?, ?, ?)",
                (task_id, label, key),
            )
        for prerequisite_id in prerequisite_ids:
            conn.execute(
                "INSERT INTO dependencies(prerequisite_id, task_id) VALUES(?, ?)",
                (prerequisite_id, task_id),
            )
        history.record_event(
            conn,
            event_type="task.created",
            actor=actor,
            task_id=task_id,
            payload={
                "status": clean_status,
                "priority": clean_priority,
                "assignee": clean_assignee,
                "labels": [label for label, _ in label_pairs],
                "depends_on": prerequisite_ids,
            },
        )
        return {"changed": True, "task": _project_task(conn, task_id)}


def update_task(
    conn: sqlite3.Connection,
    task_id: str,
    *,
    actor: str,
    title: str | UnsetType = UNSET,
    description: str | UnsetType = UNSET,
    priority: str | UnsetType = UNSET,
    assignee: str | UnsetType | None = UNSET,
    blocked_reason: str | UnsetType | None = UNSET,
    add_labels: Iterable[str] = (),
    remove_labels: Iterable[str] = (),
    replace_labels: Iterable[str] | UnsetType = UNSET,
    if_version: int | None = None,
) -> dict[str, Any]:
    """Update mutable task fields; a no-op changes nothing (spec 6.7)."""
    actor = _actor(actor)

    with write_transaction(conn):
        task = _load_task(conn, task_id)
        if if_version is not None and task.version != if_version:
            raise StaleVersionError(
                f"Stale version for {task_id}: expected {if_version}, "
                f"current {task.version}.",
                details={"expected": if_version, "current": task.version},
            )

        changes: dict[str, Any] = {}
        if not isinstance(title, UnsetType):
            new_title = _check_title(title)
            if new_title != task.title:
                changes["title"] = new_title
        if not isinstance(description, UnsetType):
            new_description = _check_description(description)
            if new_description != task.description:
                changes["description"] = new_description
        if not isinstance(priority, UnsetType):
            new_priority = _check_priority(priority)
            if new_priority != task.priority:
                changes["priority"] = new_priority
        if not isinstance(assignee, UnsetType):
            new_assignee = _check_assignee(assignee)
            if new_assignee != task.assignee:
                changes["assignee"] = new_assignee
        if not isinstance(blocked_reason, UnsetType):
            new_blocked_reason = _check_blocked_reason(blocked_reason)
            if new_blocked_reason != task.blocked_reason:
                changes["blocked_reason"] = new_blocked_reason

        labels: list[tuple[str, str]] | None = None
        current_labels = _labels_for(conn, [task_id])[task_id]
        if not isinstance(replace_labels, UnsetType):
            pairs = _normalize_labels(replace_labels)
        else:
            pairs = _normalize_labels(current_labels)
            if add_labels:
                pairs = _normalize_labels(
                    [label for label, _ in pairs] + list(add_labels)
                )
            if remove_labels:
                drop = {_label_key(label) for label in remove_labels}
                pairs = [(label, key) for label, key in pairs if key not in drop]
        if [key for _, key in pairs] != [_label_key(label) for label in current_labels]:
            labels = pairs

        if not changes and labels is None:
            return {"changed": False, "task": _project_task(conn, task_id)}

        _assert_not_claimed_by_other(conn, task_id, actor)
        if labels is not None:
            conn.execute("DELETE FROM task_labels WHERE task_id = ?", (task_id,))
            for label, key in labels:
                conn.execute(
                    "INSERT INTO task_labels(task_id, label, label_key) "
                    "VALUES(?, ?, ?)",
                    (task_id, label, key),
                )
        if changes:
            columns = ", ".join(f"{column} = ?" for column in changes)
            conn.execute(
                f"UPDATE tasks SET {columns}, updated_at = ?, version = ? "
                "WHERE id = ? AND version = ?",
                [
                    *changes.values(),
                    utcnow_iso(),
                    task.version + 1,
                    task_id,
                    task.version,
                ],
            )
        else:
            conn.execute(
                "UPDATE tasks SET updated_at = ?, version = ? WHERE id = ? AND version = ?",
                (utcnow_iso(), task.version + 1, task_id, task.version),
            )

        payload: dict[str, Any] = {"fields": sorted(changes)}
        if "assignee" in changes:
            payload["previous_assignee"] = task.assignee
        if labels is not None:
            payload["labels"] = [label for label, _ in labels]
        history.record_event(
            conn,
            event_type="task.updated",
            actor=actor,
            task_id=task_id,
            payload=payload,
        )
        return {"changed": True, "task": _project_task(conn, task_id)}


def add_note(
    conn: sqlite3.Connection,
    task_id: str,
    text: str,
    *,
    actor: str,
) -> dict[str, Any]:
    """Append a note event — never touches ``updated_at`` or ``version`` (9.1)."""
    actor = _actor(actor)
    clean_text = _check_note(text)
    with write_transaction(conn):
        _task_row(conn, task_id)
        event = history.record_event(
            conn,
            event_type="note",
            actor=actor,
            task_id=task_id,
            payload={"text": clean_text},
        )
        return {"changed": True, "event": event}


def list_tasks(
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
) -> list[dict[str, Any]]:
    """The spec 10.2 filter/sort contract."""
    if status is not None:
        _check_status(status)
    if priority is not None:
        _check_priority(priority)
    if sort is not None and sort not in _SORT_FIELDS:
        raise ValidationError(
            f"Unknown sort field {sort!r}; expected one of "
            f"{', '.join(sorted(_SORT_FIELDS))}."
        )
    if limit is not None and limit < 0:
        raise ValidationError("--limit must not be negative.")

    sql = "SELECT t.* FROM tasks t"
    params: list[Any] = []
    if label is not None:
        sql += " JOIN task_labels l ON l.task_id = t.id AND l.label_key = ?"
        params.append(_label_key(label))
    conditions: list[str] = []
    if status is not None:
        conditions.append("t.status = ?")
        params.append(status)
    if assignee is not None:
        conditions.append("t.assignee = ?")
        params.append(assignee)
    if priority is not None:
        conditions.append("t.priority = ?")
        params.append(priority)
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)

    with read_transaction(conn):
        rows = conn.execute(sql, params).fetchall()
        tasks = _project_rows(conn, rows, full=full)

    if blocked is not None:
        tasks = [task for task in tasks if task["blocked"] is blocked]
    if eligible is not None:
        tasks = [task for task in tasks if task["eligible"] is eligible]

    tasks.sort(key=_sort_key)
    if sort is not None and sort != "priority":
        tasks.sort(key=_secondary_sort_key(sort))
    if reverse:
        tasks.reverse()
    if limit is not None:
        tasks = tasks[:limit]
    return tasks


def _sort_key(task: Mapping[str, Any]) -> tuple[int, str, int]:
    """Default ordering: priority rank descending, then created_at, then sequence."""
    sequence = _sequence_of(task)
    return (-PRIORITY_RANK[str(task["priority"])], str(task["created_at"]), sequence)


def _secondary_sort_key(sort: str) -> Any:
    def key(task: Mapping[str, Any]) -> tuple[Any, int]:
        if sort == "title":
            return (str(task["title"]).casefold(), _sequence_of(task))
        if sort == "id":
            return (str(task["id"]), _sequence_of(task))
        return (str(task[sort]), _sequence_of(task))

    return key


def _sequence_of(task: Mapping[str, Any]) -> int:
    return int(str(task["id"]).split("-", 1)[1])


def counts(conn: sqlite3.Connection, tasks: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """The spec 10.2 count contract over a filtered set."""
    counts_map: dict[str, Any] = {"tasks": 0}
    for status in STATUSES:
        counts_map[status] = 0
    for key in ("eligible", "reviewable", "blocked", "claimed"):
        counts_map[key] = 0
    for task in tasks:
        counts_map["tasks"] += 1
        counts_map[str(task["status"])] += 1
        if task["eligible"]:
            counts_map["eligible"] += 1
        if task["reviewable"]:
            counts_map["reviewable"] += 1
        if task["blocked"]:
            counts_map["blocked"] += 1
        if task["claimed_by"] is not None:
            counts_map["claimed"] += 1
    return counts_map


__all__ = [
    "add_note",
    "apply_transition",
    "cancel_task",
    "complete_task",
    "counts",
    "create_task",
    "derived_flags",
    "get_task",
    "list_tasks",
    "project_task",
    "project_tasks_by_id",
    "reopen_task",
    "restore_task",
    "set_status",
    "update_task",
]
