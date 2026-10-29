"""``tack reject``: review back to in_progress with a mandatory reason (4.7)."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tackboard import claims, cli, history, schema, service
from tackboard.errors import ValidationError
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
