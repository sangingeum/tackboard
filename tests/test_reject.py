"""``tack reject``: review back to in_progress with a mandatory reason (4.7)."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tackboard import claims, cli, history, schema, service
from tackboard.errors import ClaimConflictError, StaleVersionError, ValidationError
from tackboard.models import MAX_REASON_LENGTH

DEFAULT_ACTOR = "dev"
RunCli = Callable[..., tuple[int, str, str]]


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TACKBOARD_DB", raising=False)
    monkeypatch.delenv("TACKBOARD_AGENT", raising=False)


@pytest.fixture
def db_file(tmp_path: Path) -> Path:
    path = tmp_path / "project.sqlite3"
    schema.init_database(path, label="project")
    return path


@pytest.fixture
def run(capsys: pytest.CaptureFixture[str]) -> RunCli:
    def _run(*argv: str) -> tuple[int, str, str]:
        code = cli.main(list(argv))
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return _run


def _document(stdout: str) -> dict[str, Any]:
    text = stdout.strip()
    assert text.startswith("{"), f"stdout is not a JSON document: {stdout!r}"
    return json.loads(text)


def _review_task(
    conn: Any, make_task: Callable[..., dict[str, Any]], *, title: str = "review me"
) -> dict[str, Any]:
    task = make_task(title=title, status="ready")
    claims.claim(conn, task["id"], actor=DEFAULT_ACTOR)
    service.set_status(conn, task["id"], "review", actor=DEFAULT_ACTOR)
    return service.get_task(conn, task["id"])


# --------------------------------------------------------------------------- #
# the happy path
# --------------------------------------------------------------------------- #


def test_reject_returns_review_to_in_progress_with_one_event(
    conn: Any, make_task: Callable[..., dict[str, Any]]
) -> None:
    task = _review_task(conn, make_task)

    result = service.reject_task(
        conn, task["id"], actor=DEFAULT_ACTOR, reason="  missing tests  "
    )

    rejected = result["task"]
    assert rejected["status"] == "in_progress"
    assert rejected["claimed_by"] == DEFAULT_ACTOR
    assert rejected["rejections"] == 1
    assert rejected["last_rejection_reason"] == "missing tests"

    event = history.list_events(conn, task["id"])["items"][0]
    assert event["event_type"] == "task.rejected"
    assert event["payload"] == {
        "from": "review",
        "to": "in_progress",
        "reason": "missing tests",
    }
    # Reject is not a forced transition and never claims one.
    assert "forced" not in event["payload"]


def test_rejections_are_counted_from_history(
    conn: Any, make_task: Callable[..., dict[str, Any]]
) -> None:
    task = _review_task(conn, make_task)

    service.reject_task(conn, task["id"], actor=DEFAULT_ACTOR, reason="first")
    service.set_status(conn, task["id"], "review", actor=DEFAULT_ACTOR)
    result = service.reject_task(
        conn, task["id"], actor=DEFAULT_ACTOR, reason="second"
    )

    assert result["task"]["rejections"] == 2
    assert result["task"]["last_rejection_reason"] == "second"

    events = [
        item
        for item in history.list_events(conn, task["id"])["items"]
        if item["event_type"] == "task.rejected"
    ]
    assert [item["payload"]["reason"] for item in events] == ["second", "first"]


def test_a_task_without_rejections_reports_zero(
    conn: Any, make_task: Callable[..., dict[str, Any]]
) -> None:
    task = make_task(title="clean")
    projection = service.get_task(conn, task["id"])
    assert projection["rejections"] == 0
    assert projection["last_rejection_reason"] is None


# --------------------------------------------------------------------------- #
# refused rejections
# --------------------------------------------------------------------------- #


def test_rejecting_a_task_that_is_not_in_review_fails(
    conn: Any, make_task: Callable[..., dict[str, Any]]
) -> None:
    task = make_task(title="backlog work")
    with pytest.raises(ValidationError) as caught:
        service.reject_task(conn, task["id"], actor=DEFAULT_ACTOR, reason="nope")
    assert "review" in str(caught.value)
    assert service.get_task(conn, task["id"])["status"] == "backlog"


@pytest.mark.parametrize("reason", [None, "", "   "])
def test_reject_requires_a_reason(
    conn: Any, make_task: Callable[..., dict[str, Any]], reason: str | None
) -> None:
    task = _review_task(conn, make_task)
    with pytest.raises(ValidationError) as caught:
        service.reject_task(conn, task["id"], actor=DEFAULT_ACTOR, reason=reason)
    assert "--reason" in str(caught.value)
    assert service.get_task(conn, task["id"])["status"] == "review"


def test_a_reason_over_the_limit_is_refused(
    conn: Any, make_task: Callable[..., dict[str, Any]]
) -> None:
    task = _review_task(conn, make_task)
    with pytest.raises(ValidationError) as caught:
        service.reject_task(
            conn, task["id"], actor=DEFAULT_ACTOR, reason="x" * (MAX_REASON_LENGTH + 1)
        )
    assert str(MAX_REASON_LENGTH) in str(caught.value)


def test_reject_under_a_foreign_claim_is_refused_until_the_claim_is_released(
    conn: Any, make_task: Callable[..., dict[str, Any]]
) -> None:
    reviewer = "qa"
    author = "author"
    task = make_task(title="claimed review", status="ready")
    claims.claim(conn, task["id"], actor=author)
    service.set_status(conn, task["id"], "review", actor=author)
    # Moving to review releases the work claim; a reviewer then claims it for
    # review, which is the claim `reject` runs into.
    claims.claim(conn, task["id"], actor=author)

    with pytest.raises(ClaimConflictError) as caught:
        service.reject_task(conn, task["id"], actor=reviewer, reason="needs work")
    assert author in str(caught.value)
    still = service.get_task(conn, task["id"])
    assert still["status"] == "review"
    assert still["claimed_by"] == author

    claims.release(
        conn,
        task["id"],
        actor=reviewer,
        force=True,
        reason=f"recovering a dead leg held by {author}",
    )
    result = service.reject_task(conn, task["id"], actor=reviewer, reason="needs work")

    rejected = result["task"]
    assert rejected["status"] == "in_progress"
    assert rejected["claimed_by"] == author
    assert rejected["rejections"] == 1
    assert rejected["last_rejection_reason"] == "needs work"


# --------------------------------------------------------------------------- #
# handing the claim back to the author
# --------------------------------------------------------------------------- #


def test_reject_hands_the_claim_back_to_the_last_in_progress_actor(
    conn: Any, make_task: Callable[..., dict[str, Any]]
) -> None:
    """The author who last worked the task gets the claim back, not the reviewer."""
    author = "dev"
    reviewer = "qa"
    task = make_task(title="hand back", status="ready", actor=author)
    claims.claim(conn, task["id"], actor=author)
    service.set_status(conn, task["id"], "review", actor=author)
    # Moving to review releases the work claim; the reviewer claims it next.
    claims.claim(conn, task["id"], actor=reviewer)

    result = service.reject_task(
        conn, task["id"], actor=reviewer, reason="tests missing"
    )

    rejected = result["task"]
    assert rejected["status"] == "in_progress"
    assert rejected["claimed_by"] == author
    event = history.list_events(conn, task["id"])["items"][0]
    assert event["event_type"] == "task.rejected"
    assert event["actor"] == reviewer
    assert event["payload"] == {
        "from": "review",
        "to": "in_progress",
        "reason": "tests missing",
    }


def test_a_second_rejection_still_hands_the_claim_to_the_author(
    conn: Any, make_task: Callable[..., dict[str, Any]]
) -> None:
    """A prior rejection event must not name the reviewer as the author."""
    author = "dev"
    reviewer = "qa"
    task = make_task(title="repeat", status="ready", actor=author)
    claims.claim(conn, task["id"], actor=author)
    service.set_status(conn, task["id"], "review", actor=author)

    first = service.reject_task(conn, task["id"], actor=reviewer, reason="first")
    assert first["task"]["claimed_by"] == author

    service.set_status(conn, task["id"], "review", actor=author)
    second = service.reject_task(conn, task["id"], actor=reviewer, reason="second")
    assert second["task"]["claimed_by"] == author


def test_reject_falls_back_to_the_creator_without_an_in_progress_entry(
    conn: Any, make_task: Callable[..., dict[str, Any]]
) -> None:
    """A review row whose in_progress entry is missing from history."""
    task = make_task(title="legacy", status="ready", actor="alice")
    conn.execute("UPDATE tasks SET status = 'review' WHERE id = ?", (task["id"],))

    result = service.reject_task(conn, task["id"], actor="qa", reason="legacy")

    assert result["task"]["status"] == "in_progress"
    assert result["task"]["claimed_by"] == "alice"


def test_reject_keeps_the_claim_with_the_reviewer_when_history_is_empty(
    conn: Any,
) -> None:
    """Last-resort fallback: an in_progress task is never left unclaimed."""
    now = "2026-10-30T00:00:00Z"
    conn.execute(
        "INSERT INTO tasks(id, sequence, title, description, status, priority, "
        "created_at, updated_at, version) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("T-000099", 99, "hand edited", "", "review", "normal", now, now, 1),
    )

    result = service.reject_task(conn, "T-000099", actor="qa", reason="legacy")

    assert result["task"]["status"] == "in_progress"
    assert result["task"]["claimed_by"] == "qa"


def test_reject_if_version_conflict_changes_nothing(
    conn: Any, make_task: Callable[..., dict[str, Any]]
) -> None:
    task = _review_task(conn, make_task)
    version = service.get_task(conn, task["id"])["version"]

    with pytest.raises(StaleVersionError):
        service.reject_task(
            conn, task["id"], actor="qa", reason="bad", if_version=version - 1
        )

    still = service.get_task(conn, task["id"])
    assert still["status"] == "review"
    assert still["claimed_by"] is None
    assert still["rejections"] == 0


# --------------------------------------------------------------------------- #
# the CLI surface
# --------------------------------------------------------------------------- #


def _make_review_task(run: RunCli, db: Path) -> str:
    code, out, err = run(
        "create",
        "review me",
        "--status",
        "ready",
        "--agent",
        DEFAULT_ACTOR,
        "--db",
        str(db),
        "--json",
    )
    assert code == 0, err
    task_id = str(_document(out)["task"]["id"])
    for argv in (
        ("claim", task_id),
        ("status", task_id, "review"),
    ):
        code, _, err = run(
            *argv, "--agent", DEFAULT_ACTOR, "--db", str(db), "--json"
        )
        assert code == 0, err
    return task_id


def test_reject_on_the_cli_returns_the_rejected_task(
    run: RunCli, db_file: Path
) -> None:
    task_id = _make_review_task(run, db_file)

    code, out, err = run(
        "reject",
        task_id,
        "--reason",
        "needs a changelog entry",
        "--agent",
        DEFAULT_ACTOR,
        "--db",
        str(db_file),
        "--json",
    )

    assert code == 0, err
    task = _document(out)["task"]
    assert task["status"] == "in_progress"
    assert task["rejections"] == 1
    assert task["last_rejection_reason"] == "needs a changelog entry"


def test_reject_without_a_reason_is_a_validation_error(
    run: RunCli, db_file: Path
) -> None:
    task_id = _make_review_task(run, db_file)

    code, out, err = run(
        "reject", task_id, "--agent", DEFAULT_ACTOR, "--db", str(db_file), "--json"
    )

    assert code == 4
    assert err == ""
    assert _document(out)["error"]["code"] == "validation"


def test_reject_on_a_non_review_task_is_exit_four(
    run: RunCli, db_file: Path
) -> None:
    code, out, err = run(
        "create", "backlog", "--agent", DEFAULT_ACTOR, "--db", str(db_file), "--json"
    )
    assert code == 0, err
    task_id = str(_document(out)["task"]["id"])

    code, out, err = run(
        "reject",
        task_id,
        "--reason",
        "not now",
        "--agent",
        DEFAULT_ACTOR,
        "--db",
        str(db_file),
        "--json",
    )

    assert code == 4
    assert err == ""
    error = _document(out)["error"]
    assert error["code"] == "validation"
    assert "review" in error["message"]


def test_the_cli_reject_hands_the_claim_back_to_the_author(
    run: RunCli, db_file: Path
) -> None:
    """The plan's sequence: next --claim dev, review dev, reject qa, dev resumes."""
    code, out, err = run(
        "create", "acceptance", "--status", "ready", "--agent", "dev",
        "--db", str(db_file), "--json",
    )
    assert code == 0, err
    task_id = str(_document(out)["task"]["id"])

    code, out, err = run(
        "next", "--claim", "--agent", "dev", "--db", str(db_file), "--json"
    )
    assert code == 0, err
    assert _document(out)["task"]["id"] == task_id

    code, _, err = run(
        "status", task_id, "review", "--agent", "dev",
        "--db", str(db_file), "--json",
    )
    assert code == 0, err

    code, out, err = run(
        "reject", task_id, "--reason", "bad", "--agent", "qa",
        "--db", str(db_file), "--json",
    )
    assert code == 0, err
    task = _document(out)["task"]
    assert task["status"] == "in_progress"
    assert task["claimed_by"] == "dev"

    # The author resumes without a forced claim.
    code, _, err = run(
        "note", task_id, "addressed the review", "--agent", "dev",
        "--db", str(db_file), "--json",
    )
    assert code == 0, err
    code, out, err = run(
        "complete", task_id, "--agent", "dev", "--db", str(db_file), "--json"
    )
    assert code == 0, err
    assert _document(out)["task"]["status"] == "done"


def test_doctor_is_clean_after_a_reject(run: RunCli, db_file: Path) -> None:
    task_id = _make_review_task(run, db_file)
    code, _, err = run(
        "reject", task_id, "--reason", "bad", "--agent", "qa",
        "--db", str(db_file), "--json",
    )
    assert code == 0, err

    report = schema.doctor(db_file)
    codes = {item["code"] for item in report["warnings"]}
    assert "in_progress_without_claim" not in codes
    assert "claim_on_inactive_task" not in codes
    assert report["ok"] is True
