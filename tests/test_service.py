"""Task lifecycle, limits, transitions, and projections (spec 6, 7.4, 10.2)."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import pytest

from tackboard import claims, history, service
from tackboard.errors import (
    InvalidTransitionError,
    NotFoundError,
    StaleVersionError,
    UsageError,
    ValidationError,
)
from tackboard.models import (
    MAX_BLOCKED_REASON_BYTES,
    MAX_DESCRIPTION_BYTES,
    MAX_LABELS,
    MAX_NOTE_BYTES,
    MAX_REASON_LENGTH,
    MAX_TITLE_LENGTH,
)

MakeTask = Callable[..., dict[str, Any]]

TASK_KEYS = {
    "id",
    "title",
    "description",
    "status",
    "priority",
    "assignee",
    "labels",
    "depends_on",
    "dependency_ready",
    "eligible",
    "reviewable",
    "blocked",
    "blocked_reason",
    "blockers",
    "claimed_by",
    "claimed_at",
    "claim_age_seconds",
    "claim_stale",
    "rejections",
    "last_rejection_reason",
    "created_at",
    "updated_at",
    "completed_at",
    "version",
}

#: shortest status path used to build fixtures for the transition table tests
PATHS: dict[str, list[str]] = {
    "backlog": [],
    "ready": ["ready"],
    "in_progress": ["ready", "in_progress"],
    "review": ["ready", "in_progress", "review"],
    "done": ["ready", "in_progress", "done"],
    "cancelled": ["cancelled"],
}


def drive(conn: sqlite3.Connection, make_task: MakeTask, status: str) -> dict[str, Any]:
    task = make_task(title=f"Task in {status}")
    for step in PATHS[status]:
        service.set_status(conn, task["id"], step, actor="dev")
    return service.get_task(conn, task["id"])


def test_create_task_defaults(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    task = make_task(title="First task")
    assert set(task) == TASK_KEYS
    assert task["id"] == "T-000001"
    assert task["status"] == "backlog"
    assert task["priority"] == "normal"
    assert task["assignee"] is None
    assert task["labels"] == []
    assert task["depends_on"] == []
    assert task["dependency_ready"] is True
    assert task["eligible"] is False
    assert task["reviewable"] is False
    assert task["blocked"] is False
    assert task["blockers"] == []
    assert task["claimed_by"] is None
    assert task["completed_at"] is None
    assert task["version"] == 1
    assert task["created_at"] == task["updated_at"]


def test_ids_only_increase(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    first = make_task(title="One")
    second = make_task(title="Two")
    service.cancel_task(conn, first["id"], actor="dev")
    third = make_task(title="Three")
    assert [first["id"], second["id"], third["id"]] == [
        "T-000001",
        "T-000002",
        "T-000003",
    ]


@pytest.mark.parametrize("title", ["", "   "])
def test_create_rejects_an_empty_title(
    conn: sqlite3.Connection, title: str
) -> None:
    with pytest.raises(ValidationError):
        service.create_task(conn, actor="dev", title=title)


def test_create_rejects_an_overlong_title(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValidationError) as caught:
        service.create_task(conn, actor="dev", title="x" * (MAX_TITLE_LENGTH + 1))
    assert caught.value.details == {
        "field": "title",
        "max": MAX_TITLE_LENGTH,
        "unit": "characters",
        "actual": MAX_TITLE_LENGTH + 1,
    }


def test_create_rejects_an_overlong_description(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValidationError) as caught:
        service.create_task(
            conn, actor="dev", title="Big", description="x" * (MAX_DESCRIPTION_BYTES + 1)
        )
    assert caught.value.details == {
        "field": "description",
        "max": MAX_DESCRIPTION_BYTES,
        "unit": "bytes",
        "actual": MAX_DESCRIPTION_BYTES + 1,
    }


def test_length_limits_name_the_field_in_details(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    """A length violation is recoverable programmatically, not only by prose."""
    task = make_task(title="Limited")

    with pytest.raises(ValidationError) as caught:
        service.update_task(
            conn,
            task["id"],
            actor="dev",
            blocked_reason="x" * (MAX_BLOCKED_REASON_BYTES + 1),
        )
    assert caught.value.details["field"] == "blocked_reason"
    assert caught.value.details["max"] == MAX_BLOCKED_REASON_BYTES
    assert caught.value.details["unit"] == "bytes"

    with pytest.raises(ValidationError) as caught:
        service.add_note(conn, task["id"], "x" * (MAX_NOTE_BYTES + 1), actor="dev")
    assert caught.value.details["field"] == "note"
    assert caught.value.details["max"] == MAX_NOTE_BYTES

    with pytest.raises(ValidationError) as caught:
        service.set_status(
            conn,
            task["id"],
            "ready",
            actor="dev",
            force=True,
            reason="x" * (MAX_REASON_LENGTH + 1),
        )
    assert caught.value.details["field"] == "reason"
    assert caught.value.details["max"] == MAX_REASON_LENGTH
    assert caught.value.details["unit"] == "characters"


@pytest.mark.parametrize(
    ("field", "value"),
    [("priority", "whenever"), ("status", "blocked"), ("status", "urgent")],
)
def test_create_rejects_invalid_enums(
    conn: sqlite3.Connection, field: str, value: str
) -> None:
    kwargs: dict[str, Any] = {"actor": "dev", "title": "Bad enum", field: value}
    with pytest.raises(ValidationError):
        service.create_task(conn, **kwargs)


@pytest.mark.parametrize("status", ["in_progress", "review", "done"])
def test_create_rejects_a_terminal_initial_status(
    conn: sqlite3.Connection, status: str
) -> None:
    with pytest.raises(ValidationError) as caught:
        service.create_task(conn, actor="dev", title="Too eager", status=status)
    assert "claim" in str(caught.value)


def test_create_rejects_invalid_agent_identifiers(conn: sqlite3.Connection) -> None:
    with pytest.raises(UsageError):
        service.create_task(conn, actor="bad agent", title="Bad actor")
    with pytest.raises(UsageError):
        service.create_task(conn, actor="dev", title="Bad assignee", assignee="bad agent")


def test_create_rejects_too_many_or_overlong_labels(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValidationError) as caught:
        service.create_task(
            conn,
            actor="dev",
            title="Many labels",
            labels=[f"l{i}" for i in range(MAX_LABELS + 1)],
        )
    assert caught.value.details["field"] == "labels"
    assert caught.value.details["max"] == MAX_LABELS

    with pytest.raises(ValidationError) as caught:
        service.create_task(conn, actor="dev", title="Long label", labels=["x" * 51])
    assert caught.value.details == {
        "field": "label",
        "max": 50,
        "unit": "characters",
        "actual": 51,
    }


def test_labels_are_trimmed_and_deduplicated_case_insensitively(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    task = make_task(title="Labelled", labels=[" Net ", "net", "API", ""])
    assert task["labels"] == ["API", "Net"]


def test_create_with_dependencies_is_atomic(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    prerequisite = make_task(title="Prerequisite")
    dependent = make_task(title="Dependent", depends_on=[prerequisite["id"]])
    assert dependent["depends_on"] == [prerequisite["id"]]
    assert dependent["dependency_ready"] is False
    assert dependent["blocked"] is True
    assert dependent["blockers"] == [
        {"code": "prerequisite_unfinished", "task_id": prerequisite["id"]}
    ]

    with pytest.raises(NotFoundError):
        service.create_task(
            conn, actor="dev", title="Doomed", depends_on=["T-999999"]
        )
    # the failed creation consumed no sequence number
    assert make_task(title="After the failure")["id"] == "T-000003"


def test_get_task_keeps_the_description_and_projections_drop_it(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    task = make_task(title="Described", description="long form")
    assert service.get_task(conn, task["id"])["description"] == "long form"
    projection = service.project_task(service.get_task(conn, task["id"]))
    assert "description" not in projection
    assert "description" in service.project_task(
        service.get_task(conn, task["id"]), full=True
    )


def test_missing_task_names_the_task_and_the_database(
    conn: sqlite3.Connection, db_path: Any
) -> None:
    with pytest.raises(NotFoundError) as caught:
        service.get_task(conn, "T-000404")
    assert caught.value.exit_code == 3
    assert "T-000404" in str(caught.value)
    assert db_path.name in str(caught.value)


def test_update_changes_fields_and_increments_the_version_once(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    task = make_task(title="Original")
    result = service.update_task(
        conn,
        task["id"],
        actor="dev",
        title="Renamed",
        description="now described",
        priority="urgent",
        assignee="qa",
    )
    assert result["changed"] is True
    updated = result["task"]
    assert updated["title"] == "Renamed"
    assert updated["description"] == "now described"
    assert updated["priority"] == "urgent"
    assert updated["assignee"] == "qa"
    assert updated["version"] == task["version"] + 1
    assert updated["updated_at"] >= task["updated_at"]
    payload = history.list_events(conn, task["id"])["items"][0]["payload"]
    assert payload["fields"] == ["assignee", "description", "priority", "title"]
    assert payload["previous_assignee"] is None
    assert payload["assignee"] == "qa"


def test_update_can_clear_assignee_and_blocked_reason(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    task = make_task(title="Assigned", assignee="qa")
    service.update_task(conn, task["id"], actor="dev", blocked_reason="waiting on vendor")
    blocked = service.get_task(conn, task["id"])
    assert blocked["assignee"] == "qa"
    assert blocked["blocked_reason"] == "waiting on vendor"
    assert blocked["blocked"] is True

    cleared = service.update_task(
        conn, task["id"], actor="dev", assignee="", blocked_reason="   "
    )["task"]
    assert cleared["assignee"] is None
    assert cleared["blocked_reason"] is None
    payload = history.list_events(conn, task["id"])["items"][0]["payload"]
    assert payload["previous_assignee"] == "qa"
    assert payload["assignee"] is None


def test_update_labels_add_remove_and_replace(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    task = make_task(title="Labels", labels=["api"])
    added = service.update_task(
        conn, task["id"], actor="dev", add_labels=["net", "API"]
    )["task"]
    assert added["labels"] == ["api", "net"]

    removed = service.update_task(
        conn, task["id"], actor="dev", remove_labels=["API"]
    )["task"]
    assert removed["labels"] == ["net"]

    replaced = service.update_task(
        conn, task["id"], actor="dev", replace_labels=["only"]
    )["task"]
    assert replaced["labels"] == ["only"]


def test_update_no_op_changes_nothing(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    task = make_task(title="Unchanged", labels=["api"])
    before = len(history.list_events(conn)["items"])
    result = service.update_task(
        conn, task["id"], actor="dev", title="Unchanged", add_labels=["API"]
    )
    assert result["changed"] is False
    assert result["task"]["version"] == task["version"]
    assert len(history.list_events(conn)["items"]) == before


def test_if_version_guards_updates(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    task = make_task(title="Versioned")
    with pytest.raises(StaleVersionError) as caught:
        service.update_task(conn, task["id"], actor="dev", title="New", if_version=99)
    assert caught.value.exit_code == 5
    assert caught.value.details == {"expected": 99, "current": task["version"]}
    assert service.update_task(
        conn, task["id"], actor="dev", title="New", if_version=task["version"]
    )["changed"] is True


def test_a_lost_row_count_race_reports_the_stored_version(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    """The optimistic row-count conflict carries ``expected``/``current`` too.

    The row update is exercised directly: inside ``BEGIN IMMEDIATE`` the row
    cannot move, so the public path cannot be made to lose the race.
    """
    task = make_task(title="Racy")
    stale = replace(service._load_task(conn, task["id"]), version=99)
    with pytest.raises(StaleVersionError) as caught:
        service._update_task_row(conn, stale, status="ready", completed_at=None)
    assert caught.value.exit_code == 5
    assert caught.value.details == {"expected": 99, "current": task["version"]}


ALLOWED = [
    ("backlog", "ready"),
    ("backlog", "cancelled"),
    ("ready", "backlog"),
    ("ready", "cancelled"),
    ("in_progress", "ready"),
    ("in_progress", "review"),
    ("in_progress", "done"),
    ("in_progress", "cancelled"),
    ("review", "in_progress"),
    ("review", "done"),
    ("review", "ready"),
    ("review", "cancelled"),
    ("done", "ready"),
    ("cancelled", "backlog"),
]

REJECTED = [
    ("backlog", "in_progress"),
    ("backlog", "review"),
    ("backlog", "done"),
    ("ready", "done"),
    ("ready", "review"),
    ("done", "cancelled"),
    ("done", "in_progress"),
    ("cancelled", "ready"),
]


@pytest.mark.parametrize(("source", "target"), ALLOWED)
def test_every_allowed_transition(
    conn: sqlite3.Connection, make_task: MakeTask, source: str, target: str
) -> None:
    task = drive(conn, make_task, source)
    result = service.set_status(conn, task["id"], target, actor="dev")
    assert result["changed"] is True
    assert result["task"]["status"] == target


@pytest.mark.parametrize(("source", "target"), REJECTED)
def test_rejected_transitions(
    conn: sqlite3.Connection, make_task: MakeTask, source: str, target: str
) -> None:
    task = drive(conn, make_task, source)
    with pytest.raises(InvalidTransitionError):
        service.set_status(conn, task["id"], target, actor="dev")


def test_same_status_is_an_idempotent_no_op(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    task = make_task(title="Already ready", status="ready")
    before = len(history.list_events(conn)["items"])
    result = service.set_status(conn, task["id"], "ready", actor="dev")
    assert result["changed"] is False
    assert result["task"]["version"] == task["version"]
    assert len(history.list_events(conn)["items"]) == before


def test_completed_at_is_set_and_cleared(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    task = drive(conn, make_task, "in_progress")
    completed = service.complete_task(conn, task["id"], actor="dev")["task"]
    assert completed["status"] == "done"
    assert completed["completed_at"] == completed["updated_at"]

    reopened = service.reopen_task(conn, task["id"], actor="dev")["task"]
    assert reopened["status"] == "ready"
    assert reopened["completed_at"] is None


def test_lifecycle_shortcuts_are_idempotent_no_ops(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    done = drive(conn, make_task, "done")
    assert service.complete_task(conn, done["id"], actor="dev")["changed"] is False

    ready = drive(conn, make_task, "ready")
    assert service.reopen_task(conn, ready["id"], actor="dev")["changed"] is False

    cancelled = drive(conn, make_task, "cancelled")
    assert service.cancel_task(conn, cancelled["id"], actor="dev")["changed"] is False

    backlog = make_task(title="Backlog shortcut")
    assert service.restore_task(conn, backlog["id"], actor="dev")["changed"] is False


def test_shortcut_uses_its_own_transition_validation(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    backlog = make_task(title="Not completable")
    with pytest.raises(InvalidTransitionError):
        service.complete_task(conn, backlog["id"], actor="dev")

    cancelled = drive(conn, make_task, "cancelled")
    with pytest.raises(InvalidTransitionError):
        service.reopen_task(conn, cancelled["id"], actor="dev")

    completed = drive(conn, make_task, "done")
    with pytest.raises(InvalidTransitionError):
        service.cancel_task(conn, completed["id"], actor="dev")


def test_entering_done_requires_an_empty_blocked_reason(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    task = make_task(title="Blocked work", status="ready")
    claims.claim(conn, task["id"], actor="dev")
    service.update_task(conn, task["id"], actor="dev", blocked_reason="vendor is down")
    with pytest.raises(ValidationError) as caught:
        service.complete_task(conn, task["id"], actor="dev")
    assert "explicit blocked reason" in str(caught.value)


def test_unfinished_prerequisites_block_starting_but_force_overrides(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    prerequisite = make_task(title="Outstanding")
    task = make_task(title="Waiting", status="ready", depends_on=[prerequisite["id"]])

    with pytest.raises(ValidationError):
        service.set_status(conn, task["id"], "in_progress", actor="dev")
    with pytest.raises(ValidationError):
        claims.claim(conn, task["id"], actor="dev")

    forced = service.set_status(
        conn, task["id"], "in_progress", actor="dev", force=True, reason="prereq lands today"
    )
    assert forced["changed"] is True
    assert forced["task"]["status"] == "in_progress"
    events = history.list_events(conn, task["id"])["items"]
    assert events[0]["payload"]["forced"] is True
    assert events[0]["payload"]["reason"] == "prereq lands today"

    # The override never permits review or done while prerequisites are unfinished.
    with pytest.raises(ValidationError):
        service.set_status(
            conn, task["id"], "review", actor="dev", force=True, reason="let me review"
        )


@pytest.mark.parametrize("reason", [None, "", "   ", "x" * 1025])
def test_force_requires_a_usable_reason(
    conn: sqlite3.Connection, make_task: MakeTask, reason: str | None
) -> None:
    task = make_task(title="Forced", status="ready")
    with pytest.raises(ValidationError):
        service.set_status(
            conn, task["id"], "in_progress", actor="dev", force=True, reason=reason
        )


def test_force_is_rejected_where_no_enumerated_case_applies(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    task = make_task(title="Plain")
    with pytest.raises(ValidationError) as caught:
        service.set_status(
            conn, task["id"], "ready", actor="dev", force=True, reason="just because"
        )
    assert "not allowed" in str(caught.value)


def test_notes_are_event_only(conn: sqlite3.Connection, make_task: MakeTask) -> None:
    task = make_task(title="Annotated")
    result = service.add_note(conn, task["id"], "handoff: see PR 12", actor="dev")
    assert result["changed"] is True
    assert result["event"]["event_type"] == "note"
    assert result["event"]["payload"]["text"] == "handoff: see PR 12"

    with pytest.raises(ValidationError):
        service.add_note(conn, task["id"], "   ", actor="dev")
    with pytest.raises(ValidationError):
        service.add_note(conn, task["id"], "x" * 2049, actor="dev")


def test_list_filters(conn: sqlite3.Connection, make_task: MakeTask) -> None:
    mine = make_task(
        title="Mine", status="ready", priority="high", labels=["net"], assignee="dev"
    )
    other = make_task(
        title="Other", priority="urgent", labels=["api"], assignee="qa"
    )
    blocked = make_task(title="Blocked", status="ready", depends_on=[mine["id"]])

    assert [t["id"] for t in service.list_tasks(conn, status="ready")] == [
        mine["id"],
        blocked["id"],
    ]
    assert [t["id"] for t in service.list_tasks(conn, assignee="qa")] == [other["id"]]
    assert [t["id"] for t in service.list_tasks(conn, label="NET")] == [mine["id"]]
    assert [t["id"] for t in service.list_tasks(conn, priority="urgent")] == [other["id"]]
    assert [t["id"] for t in service.list_tasks(conn, eligible=True)] == [mine["id"]]
    assert [t["id"] for t in service.list_tasks(conn, blocked=True)] == [blocked["id"]]
    assert [t["id"] for t in service.list_tasks(conn, limit=1)] == [other["id"]]


def test_list_default_ordering_is_priority_then_age(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    low = make_task(title="Low", priority="low")
    urgent_late = make_task(title="Urgent late", priority="urgent")
    normal = make_task(title="Normal")
    urgent_early = make_task(title="Urgent early", priority="urgent")
    assert [task["id"] for task in service.list_tasks(conn)] == [
        urgent_late["id"],
        urgent_early["id"],
        normal["id"],
        low["id"],
    ]


def test_list_sort_and_reverse(conn: sqlite3.Connection, make_task: MakeTask) -> None:
    make_task(title="Beta")
    make_task(title="Alpha")
    ascending = [task["title"] for task in service.list_tasks(conn, sort="title")]
    assert ascending == ["Alpha", "Beta"]
    descending = [
        task["title"] for task in service.list_tasks(conn, sort="title", reverse=True)
    ]
    assert descending == ["Beta", "Alpha"]


def test_list_rejects_unknown_sort_and_status(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    make_task(title="Whatever")
    with pytest.raises(ValidationError):
        service.list_tasks(conn, sort="colour")
    with pytest.raises(ValidationError):
        service.list_tasks(conn, status="blocked")
    with pytest.raises(ValidationError):
        service.list_tasks(conn, limit=-1)


def test_counts_over_a_filtered_set(conn: sqlite3.Connection, make_task: MakeTask) -> None:
    ready = make_task(title="Ready work", status="ready")
    make_task(title="Waiting", status="ready", depends_on=[ready["id"]])
    drive(conn, make_task, "done")

    everything = service.counts(conn, service.list_tasks(conn))
    assert everything["tasks"] == 3
    assert everything["ready"] == 2
    assert everything["done"] == 1
    assert everything["eligible"] == 1
    assert everything["blocked"] == 1
    assert everything["claimed"] == 0

    filtered = service.counts(conn, service.list_tasks(conn, status="ready"))
    assert filtered["tasks"] == 2
    assert filtered["done"] == 0
