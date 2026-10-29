"""Reviewer separation: ``next --stage review --claim --different-from-author``.

Opt-in only. It filters the candidate list inside the same ``BEGIN IMMEDIATE``
select-and-claim transaction, so the pick and the claim stay atomic. The author
of a review item is derived from history: the actor of its most recent
``in_progress`` entry.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tackboard import claims, cli, schema, service
from tackboard.errors import UsageError

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


def _reviewed_by(
    conn: Any, who: str, *, title: str, actor: str = "dev"
) -> dict[str, Any]:
    task = service.create_task(
        conn, actor=actor, title=title, status="ready"
    )["task"]
    claims.claim(conn, task["id"], actor=who)
    service.set_status(conn, task["id"], "review", actor=who)
    return service.get_task(conn, task["id"])


def test_author_is_skipped_and_the_reviewer_gets_the_other_task(
    conn: Any,
) -> None:
    mine = _reviewed_by(conn, "alice", title="alice's")
    theirs = _reviewed_by(conn, "bob", title="bob's")

    result = claims.next_task(
        conn,
        actor="alice",
        stage="review",
        claim=True,
        different_from_author=True,
    )

    assert result["task"] is not None
    assert result["task"]["id"] == theirs["id"]
    assert result["task"]["status"] == "review"
    assert service.get_task(conn, theirs["id"])["claimed_by"] == "alice"
    assert service.get_task(conn, mine["id"])["claimed_by"] is None


def test_an_author_with_no_other_review_item_gets_nothing(conn: Any) -> None:
    _reviewed_by(conn, "alice", title="alice's only")

    result = claims.next_task(
        conn,
        actor="alice",
        stage="review",
        claim=True,
        different_from_author=True,
    )

    assert result["task"] is None
    assert result["tasks"] == []


def test_default_behaviour_still_picks_the_authors_own_item(conn: Any) -> None:
    mine = _reviewed_by(conn, "alice", title="alice's own")

    result = claims.next_task(conn, actor="alice", stage="review", claim=True)

    assert result["task"] is not None
    assert result["task"]["id"] == mine["id"]


def test_the_flag_needs_the_review_stage(conn: Any) -> None:
    with pytest.raises(UsageError):
        claims.next_task(
            conn, actor="alice", stage="work", claim=True, different_from_author=True
        )


def test_the_flag_needs_the_claim(conn: Any) -> None:
    with pytest.raises(UsageError):
        claims.next_task(
            conn,
            actor="alice",
            stage="review",
            claim=False,
            different_from_author=True,
        )


def test_the_cli_flag_skips_the_author(run: RunCli, db_file: Path) -> None:
    for who, title in (("alice", "alice's"), ("bob", "bob's")):
        code, out, err = run(
            "create",
            title,
            "--status",
            "ready",
            "--agent",
            DEFAULT_ACTOR,
            "--db",
            str(db_file),
            "--json",
        )
        assert code == 0, err
        task_id = str(_document(out)["task"]["id"])
        for argv in (("claim", task_id), ("status", task_id, "review")):
            code, _, err = run(
                *argv, "--agent", who, "--db", str(db_file), "--json"
            )
            assert code == 0, err

    code, out, err = run(
        "next",
        "--stage",
        "review",
        "--claim",
        "--different-from-author",
        "--agent",
        "alice",
        "--db",
        str(db_file),
        "--json",
    )

    assert code == 0, err
    assert _document(out)["task"]["id"] == "T-000002"


def test_the_cli_flag_outside_review_is_a_usage_error(
    run: RunCli, db_file: Path
) -> None:
    code, out, err = run(
        "next",
        "--stage",
        "work",
        "--claim",
        "--different-from-author",
        "--agent",
        "alice",
        "--db",
        str(db_file),
        "--json",
    )

    assert code == 2
    error = _document(out)["error"]
    assert error["code"] == "usage"
    assert "--stage review" in error["message"]
