"""Dependency DAG: edges, cycles, readiness, exports (spec 7, section 16)."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any

import pytest

from tackboard import dependencies, history, service
from tackboard.errors import CycleError, NotFoundError, StaleVersionError, ValidationError

MakeTask = Callable[..., dict[str, Any]]
MakeDone = Callable[..., dict[str, Any]]


def test_add_and_list_a_dependency(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    prerequisite = make_task(title="Foundation")
    dependent = make_task(title="Built on it")

    result = dependencies.add_dependency(
        conn, dependent["id"], prerequisite["id"], actor="dev"
    )
    assert result["changed"] is True
    assert result["task"]["depends_on"] == [prerequisite["id"]]
    assert result["task"]["version"] == dependent["version"] + 1

    listed = dependencies.list_dependencies(conn, dependent["id"])
    assert listed["schema_version"] == 1
    assert listed["task_id"] == dependent["id"]
    assert [task["id"] for task in listed["tasks"]] == [prerequisite["id"]]

    dependents = dependencies.list_dependents(conn, prerequisite["id"])
    assert [task["id"] for task in dependents["tasks"]] == [dependent["id"]]


def test_duplicate_add_is_an_idempotent_success(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    prerequisite = make_task(title="Foundation")
    dependent = make_task(title="Dependent")
    dependencies.add_dependency(conn, dependent["id"], prerequisite["id"], actor="dev")
    before = len(history.list_events(conn)["items"])

    again = dependencies.add_dependency(
        conn, dependent["id"], prerequisite["id"], actor="dev"
    )
    assert again["changed"] is False
    assert again["task"]["version"] == 2  # one edge added once
    assert len(history.list_events(conn)["items"]) == before


def test_removing_a_missing_edge_is_an_idempotent_success(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    first = make_task(title="First")
    second = make_task(title="Second")
    result = dependencies.remove_dependency(conn, second["id"], first["id"], actor="dev")
    assert result["changed"] is False
    assert result["task"]["version"] == 1


def test_remove_dependency_records_an_event(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    prerequisite = make_task(title="Foundation")
    dependent = make_task(title="Dependent")
    dependencies.add_dependency(conn, dependent["id"], prerequisite["id"], actor="dev")
    result = dependencies.remove_dependency(
        conn, dependent["id"], prerequisite["id"], actor="dev"
    )
    assert result["changed"] is True
    assert result["task"]["depends_on"] == []
    assert history.list_events(conn, dependent["id"])["items"][0]["event_type"] == (
        "dependency.removed"
    )


def test_self_dependency_is_rejected(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    task = make_task(title="Loop")
    with pytest.raises(ValidationError) as caught:
        dependencies.add_dependency(conn, task["id"], task["id"], actor="dev")
    assert "itself" in str(caught.value)


def test_missing_ids_are_rejected(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    task = make_task(title="Real")
    with pytest.raises(NotFoundError):
        dependencies.add_dependency(conn, task["id"], "T-000999", actor="dev")
    with pytest.raises(NotFoundError):
        dependencies.add_dependency(conn, "T-000999", task["id"], actor="dev")
    with pytest.raises(NotFoundError):
        dependencies.list_dependencies(conn, "T-000999")


def test_cycles_are_rejected_and_leave_no_partial_state(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    first = make_task(title="First")
    second = make_task(title="Second")
    dependencies.add_dependency(conn, second["id"], first["id"], actor="dev")

    with pytest.raises(CycleError) as caught:
        dependencies.add_dependency(conn, first["id"], second["id"], actor="dev")
    assert caught.value.exit_code == 4
    assert first["id"] in str(caught.value) and second["id"] in str(caught.value)

    assert service.get_task(conn, first["id"])["depends_on"] == []
    assert service.get_task(conn, first["id"])["version"] == 1


def test_adding_an_unfinished_prerequisite_to_done_work_is_refused(
    conn: sqlite3.Connection, make_task: MakeTask, make_done: MakeDone
) -> None:
    finished = make_done(title="Finished")
    unfinished = make_task(title="Unfinished")
    with pytest.raises(ValidationError):
        dependencies.add_dependency(conn, finished["id"], unfinished["id"], actor="dev")


def test_a_cancelled_task_cannot_gain_prerequisites(
    conn: sqlite3.Connection, make_task: MakeTask, make_done: MakeDone
) -> None:
    finished = make_done(title="Finished")
    cancelled = make_task(title="Cancelled", status="ready")
    service.cancel_task(conn, cancelled["id"], actor="dev")
    with pytest.raises(ValidationError):
        dependencies.add_dependency(conn, cancelled["id"], finished["id"], actor="dev")


def test_unfinished_prerequisite_on_active_work_needs_force(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    active = make_task(title="Active", status="ready")
    service.set_status(conn, active["id"], "in_progress", actor="dev")
    late = make_task(title="Discovered late")

    with pytest.raises(ValidationError):
        dependencies.add_dependency(conn, active["id"], late["id"], actor="dev")
    with pytest.raises(ValidationError):
        dependencies.add_dependency(conn, active["id"], late["id"], actor="dev", force=True)

    forced = dependencies.add_dependency(
        conn, active["id"], late["id"], actor="dev", force=True, reason="discovered downstream"
    )
    assert forced["changed"] is True
    assert forced["task"]["blocked"] is True
    event = history.list_events(conn, active["id"])["items"][0]
    assert event["payload"]["forced"] is True
    assert event["payload"]["reason"] == "discovered downstream"


def test_force_is_refused_where_no_enumerated_case_applies(
    conn: sqlite3.Connection, make_task: MakeTask, make_done: MakeDone
) -> None:
    finished = make_done(title="Finished")
    waiting = make_task(title="Waiting")
    with pytest.raises(ValidationError):
        dependencies.add_dependency(
            conn, waiting["id"], finished["id"], actor="dev", force=True, reason="nope"
        )


def test_readiness_follows_the_prerequisite(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    prerequisite = make_task(title="Prerequisite")
    dependent = make_task(
        title="Dependent", status="ready", depends_on=[prerequisite["id"]]
    )
    assert dependencies.dependency_ready(conn, dependent["id"]) is False

    service.set_status(conn, prerequisite["id"], "ready", actor="dev")
    assert dependencies.dependency_ready(conn, dependent["id"]) is False

    service.set_status(conn, prerequisite["id"], "in_progress", actor="dev")
    service.complete_task(conn, prerequisite["id"], actor="dev")
    assert dependencies.dependency_ready(conn, dependent["id"]) is True
    assert service.get_task(conn, dependent["id"])["eligible"] is True


def test_cancelled_prerequisites_block_with_their_own_code(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    prerequisite = make_task(title="Abandoned")
    dependent = make_task(title="Depending", depends_on=[prerequisite["id"]])
    service.cancel_task(conn, prerequisite["id"], actor="dev")

    assert dependencies.blockers(conn, dependent["id"]) == [
        {"code": "prerequisite_cancelled", "task_id": prerequisite["id"]}
    ]
    assert service.get_task(conn, dependent["id"])["blocked"] is True


def test_reopening_a_prerequisite_reports_active_dependents(
    conn: sqlite3.Connection, make_task: MakeTask, make_done: MakeDone
) -> None:
    prerequisite = make_done(title="Prerequisite")
    dependent = make_task(title="Dependent", status="ready", depends_on=[prerequisite["id"]])
    service.set_status(conn, dependent["id"], "in_progress", actor="dev")

    result = service.reopen_task(conn, prerequisite["id"], actor="dev")
    assert result["changed"] is True
    assert [task["id"] for task in result["active_dependents"]] == [dependent["id"]]
    flags = dependencies.derived_flags(conn, dependent["id"])
    assert flags["blocked"] is True
    assert flags["blockers"] == [
        {"code": "prerequisite_unfinished", "task_id": prerequisite["id"]}
    ]
    assert flags["claimed_by"] == "dev"
    assert flags["claimed_at"] is not None


def test_derived_flags_for_an_unblocked_ready_task(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    task = make_task(title="Ready", status="ready")
    flags = dependencies.derived_flags(conn, task["id"])
    assert flags["dependency_ready"] is True
    assert flags["eligible"] is True
    assert flags["reviewable"] is False
    assert flags["blocked"] is False
    assert flags["claimed_by"] is None


def test_status_change_version_guard_applies_to_edges(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    first = make_task(title="First")
    second = make_task(title="Second")
    with pytest.raises(StaleVersionError):
        dependencies.add_dependency(
            conn, second["id"], first["id"], actor="dev", if_version=42
        )


def test_json_graph_export(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    first = make_task(title="First")
    second = make_task(title="Second")
    third = make_task(title="Third")
    dependencies.add_dependency(conn, second["id"], first["id"], actor="dev")
    dependencies.add_dependency(conn, third["id"], second["id"], actor="dev")

    graph = dependencies.export_graph(conn)
    assert graph["schema_version"] == 1
    assert {node["id"] for node in graph["nodes"]} == {first["id"], second["id"], third["id"]}
    assert graph["edges"] == [
        {"source": first["id"], "target": second["id"]},
        {"source": second["id"], "target": third["id"]},
    ]

    neighbourhood = dependencies.export_graph(conn, second["id"])
    assert {node["id"] for node in neighbourhood["nodes"]} == {
        first["id"],
        second["id"],
        third["id"],
    }


def test_dot_export_quotes_and_escapes(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    awkward = make_task(title='He said "hi" \\ and left')
    other = make_task(title="Plain")
    dependencies.add_dependency(conn, other["id"], awkward["id"], actor="dev")

    dot = dependencies.export_graph(conn, fmt="dot")
    assert isinstance(dot, str)
    assert dot.startswith("digraph tackboard {")
    assert dot.rstrip().endswith("}")
    assert '\\"hi\\"' in dot
    assert "\\\\" in dot
    assert f'"{awkward["id"]}" -> "{other["id"]}"' in dot


def test_unknown_graph_format_is_rejected(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    make_task(title="Any")
    with pytest.raises(ValidationError):
        dependencies.export_graph(conn, fmt="yaml")


def test_find_cycle_returns_none_on_an_acyclic_graph(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    first = make_task(title="First")
    second = make_task(title="Second")
    dependencies.add_dependency(conn, second["id"], first["id"], actor="dev")
    assert dependencies.find_cycle(conn) is None
