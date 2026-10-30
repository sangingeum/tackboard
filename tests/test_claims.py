"""Claims, the section 8.2 policy table, and ``next`` (spec 8, 10.4, 16)."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tackboard import claims, history, service
from tackboard.errors import (
    ClaimConflictError,
    InvalidTransitionError,
    UsageError,
    ValidationError,
)
from tackboard.models import utcnow_iso

MakeTask = Callable[..., dict[str, Any]]

STAMP = "%Y-%m-%dT%H:%M:%SZ"


def _backdate_claim(conn: sqlite3.Connection, task_id: str, claimed_at: str) -> None:
    """Move a stored claim's timestamp, to age it without waiting."""
    conn.execute(
        "UPDATE claims SET claimed_at = ? WHERE task_id = ?", (claimed_at, task_id)
    )


def _ago(**delta: float) -> str:
    """A UTC timestamp ``delta`` before now, in the stored ISO format."""
    now = datetime.strptime(utcnow_iso(), STAMP)
    return (now - timedelta(**delta)).strftime(STAMP)


def test_claiming_a_ready_task_starts_it(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    task = make_task(title="Claimable", status="ready")
    result = claims.claim(conn, task["id"], actor="dev")
    assert result["changed"] is True
    assert result["task"]["status"] == "in_progress"
    assert result["task"]["claimed_by"] == "dev"
    assert result["task"]["assignee"] is None
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
    assert result["task"]["assignee"] is None
    payload = history.list_events(conn, task["id"])["items"][0]["payload"]
    assert payload["forced"] is True
    assert payload["takeover_from"] == "dev"
    assert "previous_assignee" not in payload
    assert "assignee" not in payload
    assert "routed_to" not in payload


def test_forced_takeover_of_a_review_claim_keeps_the_status(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    task = make_task(title="Awaiting review", status="ready")
    claims.claim(conn, task["id"], actor="dev")
    service.set_status(conn, task["id"], "review", actor="dev")
    claims.claim(conn, task["id"], actor="dev")

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


def test_claiming_another_agents_assignment_succeeds_and_records_the_routing(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    """The assignment is a routing note: a non-assignee's claim succeeds and
    leaves the assignment alone, recording the routing mismatch in the event."""
    task = make_task(title="Assigned elsewhere", status="ready", assignee="qa")

    result = claims.claim(conn, task["id"], actor="dev")

    assert result["changed"] is True
    assert result["task"]["status"] == "in_progress"
    assert result["task"]["claimed_by"] == "dev"
    assert result["task"]["assignee"] == "qa"
    payload = history.list_events(conn, task["id"])["items"][0]["payload"]
    assert payload["routed_to"] == "qa"
    assert "forced" not in payload


def test_the_claim_and_status_entry_points_agree_on_a_card_assigned_elsewhere(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    """Issue #8 and #10: `claim` and `status … in_progress` produce the same
    state for a card assigned elsewhere, neither changes the assignment, and
    both record the routing in their event."""
    via_claim = make_task(title="Routed away", status="ready", assignee="qa")
    via_status = make_task(title="Routed away", status="ready", assignee="qa")

    claim_result = claims.claim(conn, via_claim["id"], actor="dev")
    status_result = service.set_status(
        conn, via_status["id"], "in_progress", actor="dev"
    )

    assert claim_result["task"]["status"] == "in_progress"
    assert status_result["task"]["status"] == "in_progress"
    assert claim_result["task"]["claimed_by"] == "dev"
    assert status_result["task"]["claimed_by"] == "dev"
    assert claim_result["task"]["assignee"] == "qa"
    assert status_result["task"]["assignee"] == "qa"

    claim_payload = history.list_events(conn, via_claim["id"])["items"][0]["payload"]
    status_payload = history.list_events(conn, via_status["id"])["items"][0]["payload"]
    assert claim_payload["routed_to"] == "qa"
    assert status_payload["routed_to"] == claim_payload["routed_to"]


@pytest.mark.parametrize(
    ("assignee", "actor", "recorded"),
    [
        ("qa", "dev", "qa"),
        ("qa", "qa", None),
        (None, "dev", None),
    ],
)
def test_status_in_progress_records_the_routing_only_on_a_mismatch(
    conn: sqlite3.Connection,
    make_task: MakeTask,
    assignee: str | None,
    actor: str,
    recorded: str | None,
) -> None:
    """A status move into `in_progress` records `routed_to` on the event exactly
    when the card is routed to another agent, and omits the key otherwise."""
    task = make_task(title="Routed away", status="ready", assignee=assignee)

    service.set_status(conn, task["id"], "in_progress", actor=actor)

    payload = history.list_events(conn, task["id"])["items"][0]["payload"]
    if recorded is None:
        assert "routed_to" not in payload
    else:
        assert payload["routed_to"] == recorded


@pytest.mark.parametrize("entry", ["claim", "status", "next"])
@pytest.mark.parametrize("assignee", [None, "qa"])
def test_a_claim_never_writes_the_assignment(
    conn: sqlite3.Connection,
    make_task: MakeTask,
    entry: str,
    assignee: str | None,
) -> None:
    """No claim path writes `assignee`, on an assigned or an unassigned card."""
    task = make_task(title="Somewhere", status="ready", assignee=assignee)
    if entry == "claim":
        result = claims.claim(conn, task["id"], actor="dev")
    elif entry == "status":
        result = service.set_status(conn, task["id"], "in_progress", actor="dev")
    else:
        result = claims.next_task(conn, actor="dev", claim=True, all_assignees=True)

    assert result["task"]["claimed_by"] == "dev"
    assert result["task"]["assignee"] == assignee


def test_forcing_a_claim_on_a_card_assigned_elsewhere_is_not_a_transfer(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    """The assignment-transfer force case is retired: forcing a claim on a card
    assigned elsewhere has no applicable force case and is refused."""
    task = make_task(title="Routed away", status="ready", assignee="qa")

    with pytest.raises(ValidationError) as caught:
        claims.claim(
            conn, task["id"], actor="dev", force=True, reason="rebalancing"
        )
    assert "force is not allowed here" in str(caught.value)

    shown = service.get_task(conn, task["id"])
    assert shown["status"] == "ready"
    assert shown["assignee"] == "qa"
    assert shown["claimed_by"] is None
    events = history.list_events(conn, task["id"])["items"]
    assert all("previous_assignee" not in event["payload"] for event in events)


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


def test_next_assignee_selector_restricts_to_one_route(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    """An explicit selector means exactly that route, with no unassigned fallback."""
    routed = make_task(title="Routed to alice", status="ready", assignee="alice")
    make_task(title="Unassigned", status="ready")
    make_task(title="Routed to qa", status="ready", assignee="qa")

    result = claims.next_task(conn, actor="dev", assignee="alice")

    assert [task["id"] for task in result["tasks"]] == [routed["id"]]
    assert result["task"]["id"] == routed["id"]


def test_next_assignee_empty_string_selects_unassigned_work_only(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    """An empty selector is the unassigned pool, not "no selector"."""
    unassigned = make_task(title="Unassigned", status="ready")
    make_task(title="Routed to alice", status="ready", assignee="alice")

    result = claims.next_task(conn, actor="dev", assignee="")

    assert [task["id"] for task in result["tasks"]] == [unassigned["id"]]


def test_next_assignee_claim_keeps_the_route_and_records_the_holder(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    """Selection and claim stay atomic; the assignment is not rewritten."""
    task = make_task(title="Routed to alice", status="ready", assignee="alice")

    result = claims.next_task(conn, actor="bob", assignee="alice", claim=True)

    assert result["task"]["id"] == task["id"]
    assert result["task"]["assignee"] == "alice"
    assert result["task"]["claimed_by"] == "bob"
    assert result["task"]["status"] == "in_progress"

    shown = service.get_task(conn, task["id"])
    assert shown["assignee"] == "alice"
    assert shown["claimed_by"] == "bob"


def test_next_assignee_with_all_assignees_is_a_usage_error(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    make_task(title="Any", status="ready", assignee="alice")

    with pytest.raises(UsageError) as caught:
        claims.next_task(conn, actor="dev", assignee="alice", all_assignees=True)

    message = str(caught.value)
    assert "--assignee" in message
    assert "--all-assignees" in message


def test_next_assignee_composes_with_label_and_minimum_priority(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    wanted = make_task(
        title="Alice urgent api",
        status="ready",
        assignee="alice",
        labels=["api"],
        priority="urgent",
    )
    make_task(
        title="Alice low api",
        status="ready",
        assignee="alice",
        labels=["api"],
        priority="low",
    )
    make_task(
        title="Bob urgent api",
        status="ready",
        assignee="bob",
        labels=["api"],
        priority="urgent",
    )
    make_task(
        title="Alice urgent net",
        status="ready",
        assignee="alice",
        labels=["net"],
        priority="urgent",
    )

    result = claims.next_task(
        conn, actor="dev", assignee="alice", label="api", min_priority="high"
    )

    assert [task["id"] for task in result["tasks"]] == [wanted["id"]]


def test_next_assignee_rejects_an_invalid_agent_id(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    make_task(title="Any", status="ready")

    with pytest.raises(ValidationError):
        claims.next_task(conn, actor="dev", assignee="bad id!")


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


# --------------------------------------------------------------------------- #
# forced starts (spec 6.2 case 2, spec 8.2 row 1, acceptance criterion 10)
# --------------------------------------------------------------------------- #


def _ready_task_blocked_only_by_a_prerequisite(
    conn: sqlite3.Connection, make_task: MakeTask, *, cancelled: bool
) -> tuple[dict[str, Any], dict[str, Any]]:
    """A ``ready`` task whose only eligibility failure is one prerequisite.

    With ``cancelled=True`` the prerequisite is cancelled, producing the
    ``prerequisite_cancelled`` blocker; otherwise it is left unfinished,
    producing ``prerequisite_unfinished``.
    """
    prerequisite = make_task(title="Prerequisite")
    dependent = make_task(
        title="Dependent", status="ready", depends_on=[prerequisite["id"]]
    )
    if cancelled:
        service.cancel_task(conn, prerequisite["id"], actor="dev")
    return prerequisite, dependent


def test_forced_claim_starts_a_task_blocked_only_by_a_cancelled_prerequisite(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    """Spec 8.2 row 1: a cancelled prerequisite is a forceable eligibility failure."""
    prerequisite, dependent = _ready_task_blocked_only_by_a_prerequisite(
        conn, make_task, cancelled=True
    )
    shown = service.get_task(conn, dependent["id"])
    assert shown["blockers"] == [
        {"code": "prerequisite_cancelled", "task_id": prerequisite["id"]}
    ]
    assert shown["eligible"] is False

    reason = "prerequisite is cancelled; proceeding deliberately"
    result = claims.claim(
        conn, dependent["id"], actor="dev", force=True, reason=reason
    )
    assert result["changed"] is True
    assert result["task"]["status"] == "in_progress"
    assert result["task"]["claimed_by"] == "dev"

    claimed_events = [
        event
        for event in history.list_events(conn, dependent["id"])["items"]
        if event["event_type"] == "task.claimed"
    ]
    assert len(claimed_events) == 1
    assert claimed_events[0]["payload"]["forced"] is True
    assert claimed_events[0]["payload"]["reason"] == reason


def test_forced_claim_starts_a_task_blocked_only_by_an_unfinished_prerequisite(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    """The unfinished branch the fix must not narrow."""
    _prerequisite, dependent = _ready_task_blocked_only_by_a_prerequisite(
        conn, make_task, cancelled=False
    )
    result = claims.claim(
        conn,
        dependent["id"],
        actor="dev",
        force=True,
        reason="pressing on without the prerequisite",
    )
    assert result["changed"] is True
    assert result["task"]["status"] == "in_progress"
    assert result["task"]["claimed_by"] == "dev"


def test_the_status_and_claim_entry_points_agree_on_a_forced_start(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    """Acceptance criterion 10: one policy table, one answer from both paths."""
    _prerequisite, via_status = _ready_task_blocked_only_by_a_prerequisite(
        conn, make_task, cancelled=True
    )
    _prerequisite, via_claim = _ready_task_blocked_only_by_a_prerequisite(
        conn, make_task, cancelled=True
    )
    reason = "prerequisite is cancelled; proceeding deliberately"

    status_result = service.set_status(
        conn, via_status["id"], "in_progress", actor="dev", force=True, reason=reason
    )
    claim_result = claims.claim(
        conn, via_claim["id"], actor="dev", force=True, reason=reason
    )

    assert status_result["changed"] is True
    assert claim_result["changed"] is True
    assert status_result["task"]["status"] == "in_progress"
    assert claim_result["task"]["status"] == "in_progress"
    assert status_result["task"]["claimed_by"] == "dev"
    assert claim_result["task"]["claimed_by"] == "dev"


def test_forced_claim_is_refused_when_an_explicit_blocker_is_set(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    """Force never covers an explicit ``blocked_reason`` (spec 6.2)."""
    task = make_task(title="Waiting on a vendor", status="ready")
    service.update_task(conn, task["id"], actor="dev", blocked_reason="vendor is down")

    with pytest.raises(ValidationError) as caught:
        claims.claim(
            conn, task["id"], actor="dev", force=True, reason="ignore the block"
        )
    assert caught.value.exit_code == 4
    assert "explicit blocked reason" in str(caught.value)
    assert service.get_task(conn, task["id"])["status"] == "ready"


def test_forced_claim_requires_a_non_empty_reason(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    """A forced start still needs a non-empty reason (spec 6.2)."""
    _prerequisite, dependent = _ready_task_blocked_only_by_a_prerequisite(
        conn, make_task, cancelled=True
    )
    with pytest.raises(ValidationError):
        claims.claim(conn, dependent["id"], actor="dev", force=True, reason="   ")
    assert service.get_task(conn, dependent["id"])["status"] == "ready"


@pytest.mark.parametrize("target", ["review", "done"])
def test_a_cancelled_prerequisite_still_blocks_entering_review_and_done(
    conn: sqlite3.Connection, make_task: MakeTask, target: str
) -> None:
    """The forced-start fix touches entering ``in_progress`` only."""
    _prerequisite, dependent = _ready_task_blocked_only_by_a_prerequisite(
        conn, make_task, cancelled=True
    )
    claims.claim(
        conn,
        dependent["id"],
        actor="dev",
        force=True,
        reason="prerequisite is cancelled; proceeding deliberately",
    )
    assert service.get_task(conn, dependent["id"])["status"] == "in_progress"

    with pytest.raises(ValidationError) as caught:
        service.set_status(conn, dependent["id"], target, actor="dev")
    assert "not done" in str(caught.value)
    assert service.get_task(conn, dependent["id"])["status"] == "in_progress"


def test_starting_a_claimed_review_task_keeps_the_claim_it_already_holds(
    conn: sqlite3.Connection, make_task: MakeTask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec 8.2: "if already held by actor, keep the claim" -- as it stands."""
    task = make_task(title="Awaiting a rework", status="ready")
    service.set_status(conn, task["id"], "in_progress", actor="dev")
    service.set_status(conn, task["id"], "review", actor="dev")
    claims.claim(conn, task["id"], actor="dev")
    held = service.get_task(conn, task["id"])["claimed_at"]
    assert held is not None

    monkeypatch.setattr(service, "utcnow_iso", lambda: "2099-01-01T00:00:00Z")
    service.set_status(conn, task["id"], "in_progress", actor="dev")

    assert service.get_task(conn, task["id"])["claimed_at"] == held


# --------------------------------------------------------------------------- #
# stale-claim visibility: derived fields, the --stale threshold, recover
# --------------------------------------------------------------------------- #


def test_derived_claim_age_and_staleness_on_claimed_and_unclaimed_tasks(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    held = make_task(title="Held", status="ready")
    claims.claim(conn, held["id"], actor="dev")

    fresh = service.get_task(conn, held["id"])
    assert isinstance(fresh["claim_age_seconds"], int)
    assert fresh["claim_age_seconds"] >= 0
    assert fresh["claim_stale"] is False

    _backdate_claim(conn, held["id"], "2020-01-01T00:00:00Z")
    aged = service.get_task(conn, held["id"])
    assert aged["claim_age_seconds"] > 7200
    assert aged["claim_stale"] is True

    free = make_task(title="Free")
    idle = service.get_task(conn, free["id"])
    assert idle["claimed_by"] is None
    assert idle["claim_age_seconds"] is None
    assert idle["claim_stale"] is False


def test_one_clock_per_read_ages_every_claim_equally(
    conn: sqlite3.Connection, make_task: MakeTask, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = make_task(title="First", status="ready")
    second = make_task(title="Second", status="ready")
    claims.claim(conn, first["id"], actor="dev")
    claims.claim(conn, second["id"], actor="qa")
    for task in (first, second):
        _backdate_claim(conn, task["id"], "2026-01-01T00:00:00Z")

    moments = iter(["2099-01-01T00:00:00Z", "2099-01-01T00:00:01Z"])
    monkeypatch.setattr(service, "utcnow_iso", lambda: next(moments))

    tasks = service.list_tasks(conn)
    ages = {task["claim_age_seconds"] for task in tasks if task["claimed_by"]}
    assert len(ages) == 1


def test_stale_claim_filtering_at_the_threshold_boundary(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    at_threshold = make_task(title="At threshold", status="ready")
    just_below = make_task(title="Just below", status="ready")
    claims.claim(conn, at_threshold["id"], actor="dev")
    claims.claim(conn, just_below["id"], actor="qa")
    _backdate_claim(conn, at_threshold["id"], _ago(seconds=7200))
    _backdate_claim(conn, just_below["id"], _ago(seconds=7000))

    stale = claims.list_claims(conn, stale=True)
    assert [item["task_id"] for item in stale["items"]] == [at_threshold["id"]]

    # An explicit --older-than wins over the configured threshold in both
    # directions: a lower one admits the just-below claim, a higher one hides
    # the boundary claim.
    widened = claims.list_claims(conn, older_than="1s", stale=True)
    assert {item["task_id"] for item in widened["items"]} == {
        at_threshold["id"],
        just_below["id"],
    }
    narrowed = claims.list_claims(conn, older_than="1d", stale=True)
    assert narrowed["items"] == []


def test_stale_threshold_is_read_from_the_config_key(
    conn: sqlite3.Connection, make_task: MakeTask, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = make_task(title="Aged", status="ready")
    claims.claim(conn, task["id"], actor="dev")
    _backdate_claim(conn, task["id"], _ago(hours=3))

    config = tmp_path / ".tackboard.toml"
    config.write_text(
        '[tackboard]\ndb = "project.sqlite3"\nstale_claim_after = "10d"\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    # Three hours is stale under the 2h default but not under the configured 10d.
    assert claims.list_claims(conn, older_than="2h")["items"]
    assert claims.list_claims(conn, stale=True)["items"] == []
    assert claims.stale_claim_seconds() == 10 * 86400


def test_an_invalid_stale_threshold_is_a_validation_error(
    conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / ".tackboard.toml"
    config.write_text(
        '[tackboard]\ndb = "project.sqlite3"\nstale_claim_after = "soon"\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValidationError) as caught:
        claims.list_claims(conn, stale=True)
    assert caught.value.exit_code == 4
    assert "Invalid duration" in str(caught.value)


def test_recover_releases_a_dead_claim_and_audits_the_reason(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    task = make_task(title="Abandoned", status="ready")
    claims.claim(conn, task["id"], actor="dev")

    result = claims.recover(conn, task["id"], actor="qa", reason="dev went offline")
    assert result["changed"] is True
    assert result["task"]["claimed_by"] is None
    assert result["task"]["status"] == "ready"

    event = history.list_events(conn, task["id"])["items"][0]
    assert event["event_type"] == "task.released"
    assert event["payload"]["forced"] is True
    assert event["payload"]["reason"] == "dev went offline"


def test_recover_requires_a_reason_and_adds_no_force_case(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    task = make_task(title="Contested", status="ready")
    claims.claim(conn, task["id"], actor="dev")

    with pytest.raises(ValidationError):
        claims.recover(conn, task["id"], actor="qa", reason="   ")

    # A claim the caller already holds is not a force case: the existing
    # policy refuses it and the claim stays put.
    with pytest.raises(ValidationError):
        claims.recover(conn, task["id"], actor="dev", reason="mine to release")
    assert service.get_task(conn, task["id"])["claimed_by"] == "dev"

    # Nothing to recover on a healthy unclaimed task: an idempotent no-op.
    free = make_task(title="Free")
    noop = claims.recover(conn, free["id"], actor="dev", reason="nothing here")
    assert noop["changed"] is False


def test_recover_clears_a_lost_claim_on_in_progress_work(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    task = make_task(title="Orphaned", status="ready")
    claims.claim(conn, task["id"], actor="dev")
    conn.execute("DELETE FROM claims WHERE task_id = ?", (task["id"],))

    result = claims.recover(conn, task["id"], actor="qa", reason="claim row was lost")
    assert result["changed"] is True
    assert result["task"]["status"] == "ready"
    event = history.list_events(conn, task["id"])["items"][0]
    assert event["payload"]["forced"] is True
    assert event["payload"]["reason"] == "claim row was lost"


def test_next_documents_carry_a_stale_claim_count(
    conn: sqlite3.Connection, make_task: MakeTask
) -> None:
    task = make_task(title="Held", status="ready")
    assert claims.next_task(conn, actor="dev")["stale_claims"] == 0

    claims.next_task(conn, actor="dev", claim=True)
    conn.execute(
        "UPDATE claims SET claimed_at = ? WHERE task_id = ?",
        ("2020-01-01T00:00:00Z", task["id"]),
    )
    conn.commit()

    plain = claims.next_task(conn, actor="qa")
    assert plain["stale_claims"] == 1
    assert plain["task"] is None
    assert claims.next_task(conn, actor="qa", claim=True)["stale_claims"] == 1
