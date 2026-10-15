"""Claims, the section 8.2 policy table, and ``next`` (spec 8, 10.4, 16)."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any

import pytest

from tackboard import claims, history, service
from tackboard.errors import (
    ClaimConflictError,
    InvalidTransitionError,
    UsageError,
    ValidationError,
)

MakeTask = Callable[..., dict[str, Any]]


def test_claiming_a_ready_task_starts_it(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    task = make_task(title="Claimable", status="ready")
    result = claims.claim(conn, task["id"], actor="dev")
    assert result["changed"] is True
    assert result["task"]["status"] == "in_progress"
    assert result["task"]["claimed_by"] == "dev"
    assert result["task"]["assignee"] == "dev"
    assert result["task"]["version"] == task["version"] + 1
    assert history.list_events(conn, task["id"])["items"][0]["event_type"] == "task.claimed"


def test_reclaiming_the_same_task_is_an_idempotent_no_op(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    task = make_task(title="Mine", status="ready")
    claims.claim(conn, task["id"], actor="dev")
    before = len(history.list_events(conn)["items"])
    again = claims.claim(conn, task["id"], actor="dev")
    assert again["changed"] is False
    assert len(history.list_events(conn)["items"]) == before


def test_another_agent_cannot_claim_a_held_task(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    task = make_task(title="Contested", status="ready")
    claims.claim(conn, task["id"], actor="dev")
    with pytest.raises(ClaimConflictError) as caught:
        claims.claim(conn, task["id"], actor="qa")
    assert caught.value.exit_code == 5
    assert caught.value.details["claimed_by"] == "dev"


def test_forced_takeover_replaces_the_claim_and_audits_it(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    task = make_task(title="Contested", status="ready")
    claims.claim(conn, task["id"], actor="dev")

    result = claims.claim(
        conn, task["id"], actor="qa", force=True, reason="dev went offline"
    )
    assert result["changed"] is True
    assert result["task"]["status"] == "in_progress"
    assert result["task"]["claimed_by"] == "qa"
    assert result["task"]["assignee"] == "qa"
    payload = history.list_events(conn, task["id"])["items"][0]["payload"]
    assert payload["forced"] is True
    assert payload["takeover_from"] == "dev"
    assert payload["previous_assignee"] == "dev"


def test_forced_takeover_of_a_review_claim_keeps_the_status(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    task = make_task(title="Awaiting review", status="ready")
    claims.claim(conn, task["id"], actor="dev")
    service.set_status(conn, task["id"], "review", actor="dev")

    result = claims.claim(
        conn, task["id"], actor="qa", force=True, reason="reviewer takes over"
    )
    assert result["task"]["status"] == "review"
    assert result["task"]["claimed_by"] == "qa"


def test_unclaimed_in_progress_needs_forced_recovery(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    task = make_task(title="Orphaned work", status="ready")
    claims.claim(conn, task["id"], actor="dev")
    conn.execute("DELETE FROM claims WHERE task_id = ?", (task["id"],))

    with pytest.raises(ValidationError) as caught:
        claims.claim(conn, task["id"], actor="qa")
    assert "recover" in str(caught.value)

    recovered = claims.claim(
        conn, task["id"], actor="qa", force=True, reason="recovering a stale claim"
    )
    assert recovered["changed"] is True
    assert recovered["task"]["status"] == "in_progress"
    assert recovered["task"]["claimed_by"] == "qa"


def test_mutating_another_agents_task_is_refused(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    task = make_task(title="Busy", status="ready")
    claims.claim(conn, task["id"], actor="dev")
    with pytest.raises(ClaimConflictError) as caught:
        service.update_task(conn, task["id"], actor="qa", title="Sneaky")
    assert caught.value.details["claimed_by"] == "dev"
    with pytest.raises(ClaimConflictError):
        service.set_status(conn, task["id"], "review", actor="qa")


def test_release_returns_in_progress_to_ready(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    task = make_task(title="Released", status="ready")
    claims.claim(conn, task["id"], actor="dev")
    result = claims.release(conn, task["id"], actor="dev")
    assert result["changed"] is True
    assert result["task"]["status"] == "ready"
    assert result["task"]["claimed_by"] is None
    assert result["task"]["eligible"] is True


def test_release_of_a_review_task_keeps_the_status(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    task = make_task(title="Review work", status="ready")
    claims.claim(conn, task["id"], actor="dev")
    service.set_status(conn, task["id"], "review", actor="dev")
    claims.claim(conn, task["id"], actor="dev")

    result = claims.release(conn, task["id"], actor="dev")
    assert result["task"]["status"] == "review"
    assert result["task"]["claimed_by"] is None
    assert result["task"]["reviewable"] is True


def test_release_by_the_wrong_agent_fails_but_force_works(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    task = make_task(title="Held", status="ready")
    claims.claim(conn, task["id"], actor="dev")

    with pytest.raises(ClaimConflictError):
        claims.release(conn, task["id"], actor="qa")

    forced = claims.release(
        conn, task["id"], actor="qa", force=True, reason="stale claim cleanup"
    )
    assert forced["changed"] is True
    payload = history.list_events(conn, task["id"])["items"][0]["payload"]
    assert payload["forced"] is True
    assert payload["released_agent"] == "dev"


def test_releasing_an_unclaimed_task_is_a_no_op(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    task = make_task(title="Not claimed")
    result = claims.release(conn, task["id"], actor="dev")
    assert result["changed"] is False


def test_claiming_another_agents_assignment_needs_force(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    task = make_task(title="Assigned elsewhere", status="ready", assignee="qa")
    with pytest.raises(ValidationError) as caught:
        claims.claim(conn, task["id"], actor="dev")
    assert "assigned to agent qa" in str(caught.value)

    transferred = claims.claim(
        conn, task["id"], actor="dev", force=True, reason="rebalancing"
    )
    assert transferred["task"]["assignee"] == "dev"
    payload = history.list_events(conn, task["id"])["items"][0]["payload"]
    assert payload["previous_assignee"] == "qa"
    assert payload["assignee"] == "dev"


def test_status_in_progress_acquires_the_claim(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    task = make_task(title="Started by status", status="ready")
    result = service.set_status(conn, task["id"], "in_progress", actor="dev")
    assert result["task"]["claimed_by"] == "dev"
    assert result["task"]["claimed_at"] is not None


def test_claiming_backlog_work_is_an_invalid_transition(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    task = make_task(title="Not scheduled")
    with pytest.raises(InvalidTransitionError):
        claims.claim(conn, task["id"], actor="dev")


def test_claiming_a_blocked_review_task_is_refused(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    prerequisite = make_task(title="Unfinished prerequisite")
    task = make_task(title="Odd review state", depends_on=[prerequisite["id"]])
    conn.execute("UPDATE tasks SET status = 'review' WHERE id = ?", (task["id"],))

    with pytest.raises(ValidationError) as caught:
        claims.claim(conn, task["id"], actor="dev")
    assert "not reviewable" in str(caught.value)


def test_force_requires_a_reason_for_claims(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    task = make_task(title="Contested", status="ready")
    claims.claim(conn, task["id"], actor="dev")
    with pytest.raises(ValidationError):
        claims.claim(conn, task["id"], actor="qa", force=True)


def test_list_claims_and_age_filtering(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    first = make_task(title="First", status="ready")
    second = make_task(title="Second", status="ready")
    claims.claim(conn, first["id"], actor="dev")
    claims.claim(conn, second["id"], actor="qa")

    listed = claims.list_claims(conn)
    assert listed["schema_version"] == 1
    assert {item["task_id"] for item in listed["items"]} == {first["id"], second["id"]}

    only_dev = claims.list_claims(conn, agent="dev")
    assert [item["task_id"] for item in only_dev["items"]] == [first["id"]]
    assert only_dev["items"][0]["age_seconds"] >= 0

    assert claims.list_claims(conn, older_than="0s")["items"]
    assert claims.list_claims(conn, older_than="2h")["items"] == []


def test_active_claim_lookup(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    task = make_task(title="Claimable", status="ready")
    assert claims.active_claim(conn, task["id"]) is None
    claims.claim(conn, task["id"], actor="dev")
    active = claims.active_claim(conn, task["id"])
    assert active is not None
    assert active["agent_id"] == "dev"
    claims.release(conn, task["id"], actor="dev")
    assert claims.active_claim(conn, task["id"]) is None


def test_next_orders_by_priority_then_age_then_sequence(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    make_task(title="Low", status="ready", priority="low")
    wanted = make_task(title="Urgent", status="ready", priority="urgent")
    make_task(title="Normal", status="ready")

    result = claims.next_task(conn, actor="dev")
    assert result["task"] is not None
    assert result["task"]["id"] == wanted["id"]
    assert len(result["tasks"]) == 3


def test_next_hides_other_agents_assignments_unless_asked(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    make_task(title="Someone else's", status="ready", assignee="qa")
    result = claims.next_task(conn, actor="dev")
    assert result["task"] is None

    widened = claims.next_task(conn, actor="dev", all_assignees=True)
    assert widened["task"] is not None
    assert widened["tasks"]
    assert widened["task"]["assignee"] == "qa"


def test_next_filters_by_label_and_minimum_priority(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    make_task(title="Low priority", status="ready", labels=["net"], priority="low")
    urgent = make_task(title="Urgent", status="ready", labels=["api"], priority="urgent")
    make_task(title="Unlabelled", status="ready", priority="urgent")

    assert claims.next_task(conn, actor="dev", label="api")["task"]["id"] == urgent["id"]
    urgent_any_label = claims.next_task(conn, actor="dev", min_priority="urgent")["task"]
    assert urgent_any_label["priority"] == "urgent"
    assert claims.next_task(conn, actor="dev", label="nothing")["task"] is None


def test_next_claim_selects_and_claims_atomically(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    make_task(title="First", status="ready")
    make_task(title="Second", status="ready")

    first_pick = claims.next_task(conn, actor="dev", claim=True)
    assert first_pick["task"]["claimed_by"] == "dev"
    assert first_pick["task"]["status"] == "in_progress"
    remaining = claims.next_task(conn, actor="dev", claim=True)
    assert remaining["task"]["id"] != first_pick["task"]["id"]
    assert claims.next_task(conn, actor="dev", claim=True)["task"] is None


def test_next_stage_review_picks_reviewable_work(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    task = make_task(title="Awaiting review", status="ready")
    service.set_status(conn, task["id"], "in_progress", actor="dev")
    service.set_status(conn, task["id"], "review", actor="dev")

    work = claims.next_task(conn, actor="qa", stage="work")
    assert work["task"] is None
    review = claims.next_task(conn, actor="qa", stage="review", claim=True)
    assert review["task"] is not None
    assert review["task"]["id"] == task["id"]
    assert review["task"]["status"] == "review"
    assert review["task"]["claimed_by"] == "qa"


def test_next_force_without_claim_is_a_usage_error(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    make_task(title="Any", status="ready")
    with pytest.raises(UsageError):
        claims.next_task(conn, actor="dev", force=True, reason="pointless")


def test_next_rejects_an_unknown_stage(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    with pytest.raises(ValidationError):
        claims.next_task(conn, actor="dev", stage="later")


@pytest.mark.parametrize(
    ("text", "expected"),
    [("45s", 45), ("30m", 1800), ("2h", 7200), ("1d", 86400)],
)
def test_duration_parsing(text: str, expected: int) -> None:
    assert claims.parse_duration(text) == expected


@pytest.mark.parametrize("text", ["", "2", "hours", "2x", "-1h"])
def test_duration_parsing_rejects_nonsense(text: str) -> None:
    with pytest.raises(ValidationError):
        claims.parse_duration(text)
