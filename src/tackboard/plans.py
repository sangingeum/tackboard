"""Atomic plan application: create a whole task DAG in one command.

A plan is a JSON document — ``{"tasks": [...]}`` — whose elements carry local,
file-scoped handles (``key``) and reference each other by those handles in
``depends_on``. Validation is complete and pure before anything is written:
unknown keys, duplicate handles, dangling references, invalid field values and
cycles are all rejected without touching the database. An accepted plan creates
its rows, labels and edges in one ``BEGIN IMMEDIATE`` transaction, appends one
``task.created`` event per task and one board-level ``plan.applied`` event.

A plan is create-only and never attaches to an existing board task: handles
resolve only inside the file. Applying the same file twice creates a second set
of tasks; nothing is updated or re-created.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from . import dependencies, history, service
from .config import validate_agent_id
from .db import write_transaction
from .errors import CycleError, ValidationError

#: The only top-level key a plan may carry.
PLAN_FIELDS: frozenset[str] = frozenset({"tasks"})

#: The fields a plan task may carry. Anything else is a validation error, never
#: silently ignored: a typo in ``depends_on`` must not produce a plan without
#: the edge its author intended.
TASK_FIELDS: frozenset[str] = frozenset(
    {
        "key",
        "title",
        "description",
        "status",
        "priority",
        "assignee",
        "labels",
        "depends_on",
    }
)

#: Defaults decided for plan elements (documented in README and SKILL).
DEFAULT_STATUS = "ready"
DEFAULT_PRIORITY = "normal"

#: The board-level event appended once per accepted batch.
PLAN_EVENT_TYPE = "plan.applied"


@dataclass(slots=True, frozen=True)
class PlanTask:
    """One validated plan element, ready to be inserted."""

    key: str | None
    title: str
    description: str
    status: str
    priority: str
    assignee: str | None
    label_pairs: tuple[tuple[str, str], ...]
    depends_on: tuple[str, ...]


def _parse_json(text: str, source: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValidationError(
            f"Cannot read plan {source}: invalid JSON ({exc})."
        ) from exc


def _unknown_keys(raw: dict[str, Any], allowed: frozenset[str], where: str) -> None:
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ValidationError(
            f"{where} has unknown keys: {', '.join(unknown)}; "
            f"allowed keys are: {', '.join(sorted(allowed))}."
        )


def _string_list(raw: Any, *, field: str, where: str) -> list[str]:
    if not isinstance(raw, list):
        raise ValidationError(f"{where}: '{field}' must be an array of strings.")
    values: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            raise ValidationError(f"{where}: '{field}' must contain only strings.")
        values.append(item)
    return values


def _build_task(raw: Any, *, index: int, source: str) -> PlanTask:
    where = f"{source}: tasks[{index}]"
    if not isinstance(raw, dict):
        raise ValidationError(f"{where} must be a JSON object.")
    _unknown_keys(raw, TASK_FIELDS, where)

    key: str | None = None
    if "key" in raw:
        candidate = raw["key"]
        if not isinstance(candidate, str) or not candidate.strip():
            raise ValidationError(f"{where}: 'key' must be a non-empty string.")
        key = candidate

    if "title" not in raw:
        raise ValidationError(f"{where}: 'title' is required.")
    title = service._check_title(raw["title"])

    description = raw.get("description", "")
    if not isinstance(description, str):
        raise ValidationError(f"{where}: 'description' must be a string.")
    description = service._check_description(description)

    status = raw.get("status", DEFAULT_STATUS)
    if not isinstance(status, str):
        raise ValidationError(f"{where}: 'status' must be a string.")
    status = service._check_initial_status(status)

    priority = raw.get("priority", DEFAULT_PRIORITY)
    if not isinstance(priority, str):
        raise ValidationError(f"{where}: 'priority' must be a string.")
    priority = service._check_priority(priority)

    assignee: str | None = None
    if "assignee" in raw and raw["assignee"] is not None:
        if not isinstance(raw["assignee"], str):
            raise ValidationError(f"{where}: 'assignee' must be a string.")
        assignee = service._check_assignee(raw["assignee"])

    labels = _string_list(raw.get("labels", []), field="labels", where=where)
    label_pairs = tuple(service._normalize_labels(labels))

    depends_on = _string_list(
        raw.get("depends_on", []), field="depends_on", where=where
    )
    # Dedupe while keeping the author's order, exactly as ``create`` does.
    resolved = tuple(dict.fromkeys(depends_on))
    return PlanTask(
        key=key,
        title=title,
        description=description,
        status=status,
        priority=priority,
        assignee=assignee,
        label_pairs=label_pairs,
        depends_on=resolved,
    )


def _check_references(tasks: list[PlanTask], source: str) -> None:
    """Every handle must be unique and every reference must land in the file."""
    declared: set[str] = set()
    for task in tasks:
        if task.key is None:
            continue
        if task.key in declared:
            raise ValidationError(
                f"Plan {source}: duplicate key {task.key!r}; "
                "keys must be unique within the file."
            )
        declared.add(task.key)
    for task in tasks:
        for prerequisite in task.depends_on:
            if prerequisite not in declared:
                where = "a task without a key" if task.key is None else f"task {task.key!r}"
                raise ValidationError(
                    f"Plan {source}: {where} depends on {prerequisite!r}, which is "
                    "not a key in this file; depends_on is file-scoped."
                )
            if prerequisite == task.key:
                raise ValidationError(
                    f"Plan {source}: task {task.key!r} cannot depend on itself."
                )


def _check_cycles(tasks: list[PlanTask], source: str) -> None:
    """Reject a cycle among the batch's own edges using the shared walker."""
    edges: dict[str, list[str]] = {}
    for task in tasks:
        if task.key is None or not task.depends_on:
            continue
        edges[task.key] = list(task.depends_on)
    cycle = dependencies.find_cycle_in_edges(edges)
    if cycle is not None:
        raise CycleError(
            f"Plan {source} contains a dependency cycle "
            f"({' -> '.join(cycle)}); nothing was created."
        )


def load_plan(text: str, *, source: str) -> list[PlanTask]:
    """Parse and fully validate a plan document; raise on the first problem.

    Pure: no database access, no writes. A returned batch is guaranteed to be
    internally consistent — unique handles, resolvable references, valid field
    values and no cycle.
    """
    document = _parse_json(text, source)
    if not isinstance(document, dict):
        raise ValidationError(
            f"Plan {source} must be a JSON object with one key, 'tasks'."
        )
    _unknown_keys(document, PLAN_FIELDS, f"Plan {source}")
    if "tasks" not in document:
        raise ValidationError(f"Plan {source} has no 'tasks' array.")
    raw_tasks = document["tasks"]
    if not isinstance(raw_tasks, list):
        raise ValidationError(f"Plan {source}: 'tasks' must be an array.")
    if not raw_tasks:
        raise ValidationError(f"Plan {source}: 'tasks' must not be empty.")

    tasks = [
        _build_task(raw, index=index, source=source)
        for index, raw in enumerate(raw_tasks, start=1)
    ]
    _check_references(tasks, source)
    _check_cycles(tasks, source)
    return tasks


def _document(
    tasks: list[PlanTask],
    *,
    ids: list[str | None],
    source: str,
    dry_run: bool,
) -> dict[str, Any]:
    """The ``plan apply`` JSON document: created tasks by id, key and title."""
    return {
        "schema_version": 1,
        "dry_run": dry_run,
        "source": source,
        "count": len(tasks),
        "tasks": [
            {"id": task_id, "key": task.key, "title": task.title}
            for task, task_id in zip(tasks, ids, strict=True)
        ],
    }


def apply_plan(
    conn: sqlite3.Connection,
    *,
    actor: str,
    tasks: list[PlanTask],
    source: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Create the batch in one ``BEGIN IMMEDIATE`` transaction.

    ``dry_run`` validates and reports only: no row, edge or event is written.
    A failure at any point rolls the whole batch back — the board keeps the
    task and event counts it had before the call.
    """
    actor = validate_agent_id(actor)
    _check_cycles(tasks, source)
    if dry_run:
        return _document(tasks, ids=[None] * len(tasks), source=source, dry_run=True)

    ids: list[str] = []
    with write_transaction(conn):
        for task in tasks:
            ids.append(
                service._insert_task_row(
                    conn,
                    title=task.title,
                    description=task.description,
                    status=task.status,
                    priority=task.priority,
                    assignee=task.assignee,
                    label_pairs=task.label_pairs,
                )
            )
        by_key = {
            task.key: task_id
            for task, task_id in zip(tasks, ids, strict=True)
            if task.key is not None
        }
        for task, task_id in zip(tasks, ids, strict=True):
            prerequisite_ids = [by_key[key] for key in task.depends_on]
            for prerequisite_id in prerequisite_ids:
                service._insert_dependency_edge(conn, task_id, prerequisite_id)
            service._record_created_event(
                conn,
                actor=actor,
                task_id=task_id,
                status=task.status,
                priority=task.priority,
                assignee=task.assignee,
                label_pairs=task.label_pairs,
                prerequisite_ids=prerequisite_ids,
            )
        history.record_event(
            conn,
            event_type=PLAN_EVENT_TYPE,
            actor=actor,
            task_id=None,
            payload={"file": source, "tasks": len(tasks), "ids": list(ids)},
        )
    return _document(tasks, ids=list(ids), source=source, dry_run=False)


__all__ = [
    "DEFAULT_PRIORITY",
    "DEFAULT_STATUS",
    "PLAN_EVENT_TYPE",
    "PLAN_FIELDS",
    "TASK_FIELDS",
    "PlanTask",
    "apply_plan",
    "load_plan",
]
