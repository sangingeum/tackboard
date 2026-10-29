"""Typed handoff references on notes (roadmap 4.6).

The kind vocabulary is closed and every kind has its own validation. Refs are
event-only: they live in the note event payload and never touch a table, a
column or a schema version.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tackboard import cli, history, schema, service
from tackboard.errors import ValidationError
from tackboard.models import MAX_NOTE_BYTES, MAX_NOTE_REFS

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


def _create(run: RunCli, db: Path, title: str, *extra: str) -> str:
    code, out, err = run(
        "create", title, "--agent", DEFAULT_ACTOR, "--db", str(db), "--json", *extra
    )
    assert code == 0, err
    return str(_document(out)["task"]["id"])


def _note(run: RunCli, db: Path, task_id: str, *refs: str) -> tuple[int, str, str]:
    argv = ["note", task_id, "handoff"]
    for ref in refs:
        argv.extend(["--ref", ref])
    argv.extend(["--agent", DEFAULT_ACTOR, "--db", str(db), "--json"])
    return run(*argv)


# --------------------------------------------------------------------------- #
# accepted kinds
# --------------------------------------------------------------------------- #


def test_every_ref_kind_is_accepted_and_stored(
    conn: sqlite3.Connection, make_task: Callable[..., dict[str, Any]]
) -> None:
    target = make_task(title="target")
    other = make_task(title="other")

    result = service.add_note(
        conn,
        target["id"],
        "handoff",
        actor=DEFAULT_ACTOR,
        refs=(
            "commit:abc1234",
            "url:https://example.com/pr/7",
            "path:src/tackboard/service.py",
            f"task:{other['id']}",
        ),
    )

    assert result["event"]["payload"]["refs"] == [
        {"kind": "commit", "value": "abc1234"},
        {"kind": "url", "value": "https://example.com/pr/7"},
        {"kind": "path", "value": "src/tackboard/service.py"},
        {"kind": "task", "value": other["id"]},
    ]
    # A note never touches the task's version (spec 9.1).
    assert service.get_task(conn, target["id"])["version"] == target["version"]


def test_refs_come_back_through_show_and_history(run: RunCli, db_file: Path) -> None:
    _create(run, db_file, "target")
    other = _create(run, db_file, "other")

    code, _, err = _note(
        run,
        db_file,
        "T-000001",
        "commit:abc1234",
        "url:https://example.com/pr/7",
        "path:src/tackboard/cli.py",
        f"task:{other}",
    )
    assert code == 0, err
    expected = [
        {"kind": "commit", "value": "abc1234"},
        {"kind": "url", "value": "https://example.com/pr/7"},
        {"kind": "path", "value": "src/tackboard/cli.py"},
        {"kind": "task", "value": other},
    ]

    code, out, _ = run("show", "T-000001", "--json", "--db", str(db_file))
    assert code == 0
    assert _document(out)["notes"][0]["payload"]["refs"] == expected

    code, out, _ = run("history", "T-000001", "--json", "--db", str(db_file))
    assert code == 0
    notes = [
        item
        for item in _document(out)["items"]
        if item["event_type"] == "note"
    ]
    assert [note["payload"]["refs"] for note in notes] == [expected]


def test_a_note_without_refs_keeps_an_empty_array(
    conn: sqlite3.Connection, make_task: Callable[..., dict[str, Any]]
) -> None:
    task = make_task(title="no refs")
    service.add_note(conn, task["id"], "plain", actor=DEFAULT_ACTOR)

    notes = history.list_notes(conn, task["id"])
    assert notes[0]["payload"] == {"text": "plain", "refs": []}


def test_a_legacy_note_reads_as_an_empty_refs_array(
    conn: sqlite3.Connection, make_task: Callable[..., dict[str, Any]]
) -> None:
    """Rows written before refs existed are read, never rewritten."""
    task = make_task(title="legacy")
    history.record_event(
        conn,
        event_type="note",
        actor=DEFAULT_ACTOR,
        task_id=task["id"],
        payload={"text": "old"},
    )

    assert history.list_notes(conn, task["id"])[0]["payload"]["refs"] == []


def test_identical_refs_collapse_to_one(
    conn: sqlite3.Connection, make_task: Callable[..., dict[str, Any]]
) -> None:
    task = make_task(title="dupes")
    result = service.add_note(
        conn,
        task["id"],
        "dupes",
        actor=DEFAULT_ACTOR,
        refs=("path:a.py", "path:a.py"),
    )
    assert result["event"]["payload"]["refs"] == [{"kind": "path", "value": "a.py"}]


# --------------------------------------------------------------------------- #
# rejected kinds and values
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("ref", "fragment"),
    [
        ("sha1:abc1234", "Unknown ref kind"),
        ("nocolon", "KIND:VALUE"),
        ("commit:", "empty value"),
        ("commit:abc", "7-40 hexadecimal"),
        ("commit:zzzzzzz", "7-40 hexadecimal"),
        ("url:example.com/pr/1", "absolute http(s) URL"),
        ("url:ftp://example.com/pr/1", "absolute http(s) URL"),
        ("url:https://exa mple.com", "whitespace"),
        ("path:/etc/passwd", "repository-relative"),
        ("path:../escape.py", "repository-relative"),
        ("path:src/./x.py", "repository-relative"),
        ("task:T-999999", "existing task"),
    ],
)
def test_bad_refs_are_validation_errors(
    conn: sqlite3.Connection,
    make_task: Callable[..., dict[str, Any]],
    ref: str,
    fragment: str,
) -> None:
    task = make_task(title="bad ref")
    with pytest.raises(ValidationError) as caught:
        service.add_note(conn, task["id"], "bad", actor=DEFAULT_ACTOR, refs=(ref,))
    assert fragment in str(caught.value)


def test_the_unknown_kind_error_names_the_vocabulary(
    run: RunCli, db_file: Path
) -> None:
    _create(run, db_file, "target")

    code, out, err = _note(run, db_file, "T-000001", "sha1:abc1234")

    assert code == 4
    assert err == ""
    error = _document(out)["error"]
    assert error["code"] == "validation"
    assert error["details"]["allowed"] == ["commit", "url", "path", "task"]
    for kind in ("commit", "url", "path", "task"):
        assert kind in error["message"]


def test_the_ref_cap_is_enforced(run: RunCli, db_file: Path) -> None:
    _create(run, db_file, "target")
    at_cap = [f"path:src/file{index}.py" for index in range(MAX_NOTE_REFS)]
    assert MAX_NOTE_REFS == 20

    code, _, err = _note(run, db_file, "T-000001", *at_cap)
    assert code == 0, err

    code, out, err = _note(
        run, db_file, "T-000001", *at_cap, "path:src/one-too-many.py"
    )
    assert code == 4
    assert err == ""
    error = _document(out)["error"]
    assert error["details"]["max"] == MAX_NOTE_REFS
    assert error["details"]["actual"] == MAX_NOTE_REFS + 1


def test_the_note_text_cap_still_applies_and_refs_do_not_count(
    conn: sqlite3.Connection, make_task: Callable[..., dict[str, Any]]
) -> None:
    task = make_task(title="caps")

    at_cap = "x" * MAX_NOTE_BYTES
    service.add_note(
        conn, task["id"], at_cap, actor=DEFAULT_ACTOR, refs=("path:a.py",)
    )

    with pytest.raises(ValidationError) as caught:
        service.add_note(
            conn,
            task["id"],
            at_cap + "x",
            actor=DEFAULT_ACTOR,
            refs=("path:a.py",),
        )
    assert "note" in str(caught.value).lower()


def test_a_missing_task_ref_is_a_validation_error(
    run: RunCli, db_file: Path
) -> None:
    _create(run, db_file, "target")

    code, out, err = _note(run, db_file, "T-000001", "task:T-000404")

    assert code == 4
    assert err == ""
    assert _document(out)["error"]["code"] == "validation"
