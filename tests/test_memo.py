"""The board-level memo: storage, revisions, the CLI contract, concurrency.

The memo is board-scoped document state, so it is tested on its own terms: the
revision table and its migration, the guarded whole-document writes against the
guard-free append, the CLI's documents and exit codes, and — the property that
matters most — that it never leaks into the task surface.
"""

from __future__ import annotations

import io
import json
import sqlite3
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tackboard import cli, memo, schema, service  # noqa: E402
from tackboard.errors import (  # noqa: E402
    NotFoundError,
    StaleRevError,
    UsageError,
    ValidationError,
)

TESTS_DIR = Path(__file__).resolve().parent
APPEND_WORKER = str(TESTS_DIR / "_worker_memo_append.py")
TIMEOUT_SECONDS = 120

DEFAULT_ACTOR = "dev"

#: The exact read-document keys of ``tack memo --json``.
MEMO_DOCUMENT_KEYS = frozenset(
    {"schema_version", "rev", "text", "author", "created_at", "bytes"}
)

RunCli = Callable[..., tuple[int, str, str]]


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never inherit project selection or an actor from the developer's shell."""
    monkeypatch.delenv("TACKBOARD_DB", raising=False)
    monkeypatch.delenv("TACKBOARD_AGENT", raising=False)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """An initialized, empty project database."""
    path = tmp_path / "project.sqlite3"
    schema.init_database(path, label="project")
    return path


@pytest.fixture
def conn(db_path: Path):
    connection = schema.open_database(db_path)
    yield connection
    connection.close()


@pytest.fixture
def run(capsys: pytest.CaptureFixture[str]) -> RunCli:
    """Invoke the CLI in process, returning (exit code, stdout, stderr)."""

    def _run(*argv: str) -> tuple[int, str, str]:
        code = cli.main(list(argv))
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return _run


def _json_document(stdout: str) -> dict[str, Any]:
    text = stdout.strip()
    assert text.startswith("{"), f"stdout is not a JSON document: {stdout!r}"
    assert "\n" not in text, f"stdout carries more than one line: {stdout!r}"
    document = json.loads(text)
    assert document["schema_version"] == 1
    return document


def _cli(run: RunCli, db: Path, *argv: str) -> tuple[int, dict[str, Any]]:
    code, out, err = run(*argv, "--db", str(db), "--json")
    document = _json_document(out) if out.strip() else {}
    return code, document if code == 0 else {**document, "_stderr": err}


def _write(run: RunCli, db: Path, *argv: str) -> tuple[int, dict[str, Any]]:
    """Invoke the CLI as an acting agent (``--agent`` follows the subcommand)."""
    code, out, err = run(*argv, "--agent", DEFAULT_ACTOR, "--db", str(db), "--json")
    document = _json_document(out) if out.strip() else {}
    return code, document if code == 0 else {**document, "_stderr": err}


def _text(run: RunCli, db: Path, *argv: str) -> tuple[int, str]:
    """Invoke the CLI and return (exit code, raw stdout)."""
    code, out, _err = run(*argv, "--db", str(db), "--json")
    return code, out


def _raw(path: Path) -> sqlite3.Connection:
    """A connection with foreign keys off, for seeding corrupt or v1 states."""
    connection = sqlite3.connect(path, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=OFF")
    return connection


def _as_v1_board(path: Path, *, seed_task: bool = True) -> None:
    """Return a board to the previous release's shape: schema 1, no memo table.

    Nothing else about the file changes, so this is the exact thing the previous
    version left behind: a v1 board opened by the v2 build must be refused with
    exit 6 until ``tack migrate`` runs.
    """
    if seed_task:
        connection = schema.open_database(path)
        try:
            service.create_task(connection, actor="dev", title="Seeded before")
        finally:
            connection.close()
    raw = _raw(path)
    try:
        raw.execute("DROP TRIGGER IF EXISTS memo_revisions_no_update")
        raw.execute("DROP TRIGGER IF EXISTS memo_revisions_no_delete")
        raw.execute("DROP TABLE IF EXISTS memo_revisions")
        raw.execute("PRAGMA user_version=1")
    finally:
        raw.close()


def _corrupt_memo_table(path: Path) -> None:
    """A board that still claims ``user_version`` 2 but has no memo table.

    The file is otherwise intact, so the memo path reaches for a
    ``memo_revisions`` table that is not there — the corrupt state ``doctor``
    names ``memo_table_invalid``.
    """
    raw = _raw(path)
    try:
        raw.execute("DROP TRIGGER IF EXISTS memo_revisions_no_update")
        raw.execute("DROP TRIGGER IF EXISTS memo_revisions_no_delete")
        raw.execute("DROP TABLE IF EXISTS memo_revisions")
        raw.execute("PRAGMA user_version=2")
    finally:
        raw.close()


def _malformed_memo_table(path: Path) -> None:
    """A board that claims ``user_version`` 2 but whose memo table is unusable.

    The table exists with the wrong columns, so a "can I select from it" probe
    would pass while the real query fails — the second corrupt shape the
    ``memo_table_invalid`` condition covers.
    """
    raw = _raw(path)
    try:
        raw.execute("DROP TRIGGER IF EXISTS memo_revisions_no_update")
        raw.execute("DROP TRIGGER IF EXISTS memo_revisions_no_delete")
        raw.execute("DROP TABLE IF EXISTS memo_revisions")
        raw.execute("CREATE TABLE memo_revisions (zzz TEXT)")
        raw.execute("PRAGMA user_version=2")
    finally:
        raw.close()


# --------------------------------------------------------------------------- #
# storage and the revision counter
# --------------------------------------------------------------------------- #


def test_a_new_board_has_no_memo(conn: Any) -> None:
    document = memo.read_memo(conn)
    assert document == {
        "rev": 0,
        "text": "",
        "author": None,
        "created_at": None,
        "bytes": 0,
    }
    assert memo.current_rev(conn) == 0
    assert memo.memo_history(conn) == {"current_rev": 0, "items": []}


def test_the_first_write_is_rev_one_and_needs_no_guard(conn: Any) -> None:
    result = memo.replace_memo(conn, actor=DEFAULT_ACTOR, text="hello", reason="start")
    assert result["changed"] is True
    assert result["memo"]["rev"] == 1
    assert result["memo"]["author"] == DEFAULT_ACTOR
    assert result["memo"]["bytes"] == 5
    assert memo.current_rev(conn) == 1
    # A revision's stamp is when it was created; there is no updated_at.
    assert set(result["memo"]) == {"rev", "text", "author", "created_at", "bytes"}


def test_a_replace_on_a_non_empty_memo_needs_a_guard(conn: Any) -> None:
    memo.replace_memo(conn, actor=DEFAULT_ACTOR, text="one", reason="start")
    with pytest.raises(UsageError) as caught:
        memo.replace_memo(conn, actor=DEFAULT_ACTOR, text="two", reason="again")
    assert caught.value.exit_code == 2
    assert "--if-rev" in caught.value.message
    assert "--force" in caught.value.message
    assert memo.current_rev(conn) == 1


def test_a_replace_requires_a_reason(conn: Any) -> None:
    with pytest.raises(ValidationError) as caught:
        memo.replace_memo(conn, actor=DEFAULT_ACTOR, text="hello")
    assert caught.value.exit_code == 4
    assert memo.current_rev(conn) == 0


def test_an_identical_replace_is_an_idempotent_no_op(conn: Any) -> None:
    memo.replace_memo(conn, actor=DEFAULT_ACTOR, text="same", reason="first")
    result = memo.replace_memo(
        conn, actor=DEFAULT_ACTOR, text="same", reason="second", if_rev=1
    )
    assert result["changed"] is False
    assert result["memo"]["rev"] == 1
    assert memo.current_rev(conn) == 1
    # A no-op writes no revision and no event.
    events = [item for item in memo.memo_history(conn)["items"]]
    assert len(events) == 1


def test_a_stale_guard_is_exit_5_and_not_retryable(conn: Any) -> None:
    memo.replace_memo(conn, actor=DEFAULT_ACTOR, text="one", reason="first")
    with pytest.raises(StaleRevError) as caught:
        memo.replace_memo(
            conn, actor=DEFAULT_ACTOR, text="two", reason="second", if_rev=7
        )
    assert caught.value.code == "stale_rev"
    assert caught.value.exit_code == 5
    assert caught.value.retryable is False
    assert caught.value.details == {"expected": 7, "current": 1}
    assert memo.current_rev(conn) == 1


def test_force_records_the_revision_it_displaced(conn: Any) -> None:
    memo.replace_memo(conn, actor=DEFAULT_ACTOR, text="one", reason="first")
    result = memo.replace_memo(
        conn, actor=DEFAULT_ACTOR, text="two", reason="overriding", force=True
    )
    assert result["changed"] is True
    assert result["memo"]["rev"] == 2
    events = memo.memo_history(conn)["items"]
    assert events[0]["reason"] == "overriding"
    # The audit lives in the event log: forced, with the displaced revision.
    from tackboard import history

    event = history.list_events(conn, limit=1)["items"][0]
    assert event["event_type"] == "memo.replaced"
    assert event["task_id"] is None
    assert event["payload"]["forced"] is True
    assert event["payload"]["previous_rev"] == 1
    assert event["payload"]["rev"] == 2
    assert "text" not in event["payload"]


def test_force_without_a_reason_is_refused(conn: Any) -> None:
    memo.replace_memo(conn, actor=DEFAULT_ACTOR, text="one", reason="first")
    with pytest.raises(ValidationError):
        memo.replace_memo(conn, actor=DEFAULT_ACTOR, text="two", force=True)


def test_force_is_refused_on_append(conn: Any) -> None:
    memo.replace_memo(conn, actor=DEFAULT_ACTOR, text="one", reason="first")
    with pytest.raises(UsageError) as caught:
        memo.append_memo(
            conn, actor=DEFAULT_ACTOR, text="more", reason="why", force=True
        )
    assert caught.value.exit_code == 2


# --------------------------------------------------------------------------- #
# append
# --------------------------------------------------------------------------- #


def test_append_inserts_one_newline_and_transforms_nothing_else(conn: Any) -> None:
    memo.replace_memo(conn, actor=DEFAULT_ACTOR, text="first line", reason="start")
    result = memo.append_memo(conn, actor=DEFAULT_ACTOR, text="second line")
    assert result["memo"]["text"] == "first line\nsecond line"
    # A memo already ending with a newline gets no extra one, and no trimming.
    result = memo.append_memo(conn, actor=DEFAULT_ACTOR, text="third\n")
    assert result["memo"]["text"] == "first line\nsecond line\nthird\n"
    assert result["changed"] is True
    assert memo.current_rev(conn) == 3


def test_an_append_needs_no_guard_even_when_the_memo_is_non_empty(conn: Any) -> None:
    memo.replace_memo(conn, actor=DEFAULT_ACTOR, text="one", reason="start")
    result = memo.append_memo(conn, actor=DEFAULT_ACTOR, text="two")
    assert result["changed"] is True
    assert result["memo"]["rev"] == 2


def test_an_append_never_erases_what_is_there(conn: Any) -> None:
    memo.replace_memo(conn, actor=DEFAULT_ACTOR, text="kept", reason="start")
    memo.append_memo(conn, actor=DEFAULT_ACTOR, text="added")
    document = memo.read_memo(conn)
    assert "kept" in document["text"]
    assert "added" in document["text"]


def test_empty_and_whitespace_appends_are_validation_errors(conn: Any) -> None:
    for text in ("", "   ", "\n"):
        with pytest.raises(ValidationError) as caught:
            memo.append_memo(conn, actor=DEFAULT_ACTOR, text=text)
        assert caught.value.exit_code == 4
    assert memo.current_rev(conn) == 0


def test_an_append_may_carry_a_reason(conn: Any) -> None:
    memo.replace_memo(conn, actor=DEFAULT_ACTOR, text="one", reason="start")
    memo.append_memo(conn, actor=DEFAULT_ACTOR, text="two", reason="because")
    assert memo.memo_history(conn)["items"][0]["reason"] == "because"


# --------------------------------------------------------------------------- #
# restore and clear
# --------------------------------------------------------------------------- #


def test_restore_linearises_the_history(conn: Any) -> None:
    memo.replace_memo(conn, actor=DEFAULT_ACTOR, text="one", reason="first")
    memo.append_memo(conn, actor=DEFAULT_ACTOR, text="two")
    result = memo.restore_memo(
        conn, actor=DEFAULT_ACTOR, rev=1, reason="roll back", if_rev=2
    )
    assert result["changed"] is True
    assert result["memo"]["rev"] == 3
    assert result["memo"]["text"] == "one"
    items = memo.memo_history(conn)["items"]
    assert [item["rev"] for item in items] == [3, 2, 1]
    # Every revision still carries its own text, so nothing was lost.
    assert memo.read_memo(conn, 2)["text"] == "one\ntwo"


def test_restoring_the_head_is_a_no_op(conn: Any) -> None:
    memo.replace_memo(conn, actor=DEFAULT_ACTOR, text="one", reason="first")
    result = memo.restore_memo(
        conn, actor=DEFAULT_ACTOR, rev=1, reason="again", if_rev=1
    )
    assert result["changed"] is False
    assert memo.current_rev(conn) == 1


def test_restore_requires_a_reason(conn: Any) -> None:
    memo.replace_memo(conn, actor=DEFAULT_ACTOR, text="one", reason="first")
    with pytest.raises(ValidationError):
        memo.restore_memo(conn, actor=DEFAULT_ACTOR, rev=1, if_rev=1)


def test_restore_zero_is_not_a_revision(conn: Any) -> None:
    memo.replace_memo(conn, actor=DEFAULT_ACTOR, text="one", reason="first")
    with pytest.raises(NotFoundError) as caught:
        memo.restore_memo(conn, actor=DEFAULT_ACTOR, rev=0, reason="nothing")
    assert caught.value.exit_code == 3


def test_restore_past_the_head_is_exit_3(conn: Any) -> None:
    memo.replace_memo(conn, actor=DEFAULT_ACTOR, text="one", reason="first")
    with pytest.raises(NotFoundError) as caught:
        memo.restore_memo(conn, actor=DEFAULT_ACTOR, rev=4, reason="nothing")
    assert caught.value.details == {"rev": 4, "current": 1}


def test_clear_writes_an_empty_revision_and_keeps_the_history(conn: Any) -> None:
    memo.replace_memo(conn, actor=DEFAULT_ACTOR, text="one", reason="first")
    result = memo.clear_memo(
        conn, actor=DEFAULT_ACTOR, reason="starting again", if_rev=1
    )
    assert result["changed"] is True
    assert result["memo"]["text"] == ""
    assert result["memo"]["rev"] == 2
    assert [item["rev"] for item in memo.memo_history(conn)["items"]] == [2, 1]
    assert memo.read_memo(conn, 1)["text"] == "one"


def test_clear_requires_a_reason(conn: Any) -> None:
    memo.replace_memo(conn, actor=DEFAULT_ACTOR, text="one", reason="first")
    with pytest.raises(ValidationError):
        memo.clear_memo(conn, actor=DEFAULT_ACTOR, if_rev=1)


def test_clearing_an_empty_board_changes_nothing(conn: Any) -> None:
    result = memo.clear_memo(conn, actor=DEFAULT_ACTOR, reason="nothing to clear")
    assert result["changed"] is False
    assert memo.current_rev(conn) == 0


# --------------------------------------------------------------------------- #
# the size cap
# --------------------------------------------------------------------------- #


def test_the_cap_is_sixty_four_kibibytes_measured_in_bytes(conn: Any) -> None:
    exact = "a" * (memo.MAX_MEMO_BYTES)
    assert memo.replace_memo(conn, actor=DEFAULT_ACTOR, text=exact, reason="big")[
        "memo"
    ]["bytes"] == memo.MAX_MEMO_BYTES

    too_big = "a" * (memo.MAX_MEMO_BYTES + 1)
    with pytest.raises(ValidationError) as caught:
        memo.replace_memo(
            conn, actor=DEFAULT_ACTOR, text=too_big, reason="bigger", if_rev=1
        )
    assert caught.value.exit_code == 4
    assert caught.value.details == {
        "field": "memo",
        "max": memo.MAX_MEMO_BYTES,
        "unit": "bytes",
        "actual": memo.MAX_MEMO_BYTES + 1,
    }
    assert memo.current_rev(conn) == 1


def test_the_cap_measures_utf8_bytes_not_characters(conn: Any) -> None:
    # "é" is two UTF-8 bytes, so half the character count fills the cap.
    half = "é" * (memo.MAX_MEMO_BYTES // 2)
    result = memo.replace_memo(conn, actor=DEFAULT_ACTOR, text=half, reason="accents")
    assert result["memo"]["bytes"] == memo.MAX_MEMO_BYTES
    with pytest.raises(ValidationError):
        memo.replace_memo(
            conn, actor=DEFAULT_ACTOR, text=half + "é", reason="more", if_rev=1
        )


def test_the_cap_applies_to_the_joined_append(conn: Any) -> None:
    half = "a" * (memo.MAX_MEMO_BYTES // 2)
    memo.replace_memo(conn, actor=DEFAULT_ACTOR, text=half, reason="start")
    with pytest.raises(ValidationError):
        memo.append_memo(conn, actor=DEFAULT_ACTOR, text=half)


# --------------------------------------------------------------------------- #
# dry run
# --------------------------------------------------------------------------- #


def test_dry_run_writes_nothing_and_reports_the_size(conn: Any) -> None:
    memo.replace_memo(conn, actor=DEFAULT_ACTOR, text="one", reason="first")
    result = memo.replace_memo(
        conn, actor=DEFAULT_ACTOR, text="two", reason="next", if_rev=1, dry_run=True
    )
    assert result["dry_run"] is True
    assert result["changed"] is True
    assert result["memo"]["rev"] == 2
    assert result["memo"]["bytes"] == 3
    assert memo.current_rev(conn) == 1
    from tackboard import history

    assert history.list_events(conn, limit=5)["items"]  # only the first write's event
    assert all(
        item["event_type"] == "memo.replaced" and item["payload"]["rev"] == 1
        for item in history.list_events(conn, limit=5)["items"]
    )


def test_dry_run_reports_a_failure_with_the_real_exit_code(
    run: RunCli, db_path: Path
) -> None:
    code, document = _write(
        run,
        db_path,
        "memo",
        "x" * (memo.MAX_MEMO_BYTES + 1),
        "--reason",
        "r",
        "--dry-run",
    )
    assert code == 4
    assert document["error"]["code"] == "validation"
    assert document["error"]["details"]["field"] == "memo"

    code, document = _write(run, db_path, "memo", "one", "--reason", "r")
    assert code == 0
    code, document = _write(
        run, db_path, "memo", "two", "--reason", "r", "--if-rev", "9", "--dry-run"
    )
    assert code == 5
    assert document["error"]["code"] == "stale_rev"
    assert document["error"]["retryable"] is False
    assert document["error"]["details"] == {"expected": 9, "current": 1}

    code, document = _write(run, db_path, "memo", "two", "--reason", "r", "--dry-run")
    assert code == 2
    assert document["error"]["code"] == "usage"


def test_dry_run_needs_no_actor(run: RunCli, db_path: Path) -> None:
    code, document = _cli(run, db_path, "memo", "text", "--reason", "r", "--dry-run")
    assert code == 0
    assert document["dry_run"] is True
    assert document["max"] == memo.MAX_MEMO_BYTES
    assert document["bytes"] == len("text")
    assert document["remaining"] == memo.MAX_MEMO_BYTES - len("text")
    # With no actor at all the predicted revision simply has none.
    assert document["memo"]["author"] is None

    # When an actor is available the predicted revision names it, so a dry run
    # describes the revision the write would actually create.
    code, document = _write(run, db_path, "memo", "text", "--reason", "r", "--dry-run")
    assert code == 0
    assert document["memo"]["author"] == DEFAULT_ACTOR
    assert memo.current_rev(schema.open_database(db_path)) == 0


# --------------------------------------------------------------------------- #
# diff
# --------------------------------------------------------------------------- #


def _seed_two_revisions(conn: Any) -> None:
    memo.replace_memo(conn, actor=DEFAULT_ACTOR, text="alpha\nbeta\n", reason="one")
    memo.append_memo(conn, actor=DEFAULT_ACTOR, text="gamma")


def test_diff_between_two_revisions(conn: Any) -> None:
    _seed_two_revisions(conn)
    result = memo.diff_memos(conn, 1, 2)
    assert result["from"] == 1
    assert result["to"] == 2
    assert result["added"] == 1
    assert result["removed"] == 0
    assert result["unified"].splitlines() == [
        "--- rev 1",
        "+++ rev 2",
        "@@ -1,2 +1,3 @@",
        " alpha",
        " beta",
        "+gamma",
    ]


def test_diff_is_directional(conn: Any) -> None:
    _seed_two_revisions(conn)
    result = memo.diff_memos(conn, 2, 1)
    assert result["added"] == 0
    assert result["removed"] == 1


def test_diff_to_head_when_only_one_revision_is_given(conn: Any) -> None:
    _seed_two_revisions(conn)
    assert memo.diff_memos(conn, 1) == memo.diff_memos(conn, 1, 2)


def test_diff_from_revision_zero_is_everything_added(conn: Any) -> None:
    memo.replace_memo(conn, actor=DEFAULT_ACTOR, text="alpha\nbeta", reason="one")
    result = memo.diff_memos(conn, 0, 1)
    assert result["added"] == 2
    assert result["removed"] == 0
    assert result["unified"].splitlines()[2:] == ["@@ -0,0 +1,2 @@", "+alpha", "+beta"]


def test_diff_of_equal_revisions_is_empty(conn: Any) -> None:
    _seed_two_revisions(conn)
    result = memo.diff_memos(conn, 2, 2)
    assert result == {"from": 2, "to": 2, "added": 0, "removed": 0, "unified": ""}


def test_diff_of_a_missing_revision_is_exit_3(conn: Any) -> None:
    _seed_two_revisions(conn)
    with pytest.raises(NotFoundError) as caught:
        memo.diff_memos(conn, 9)
    assert caught.value.exit_code == 3
    assert caught.value.details == {"rev": 9, "current": 2}


def test_diff_context_is_three_lines(conn: Any) -> None:
    body = "\n".join(f"line {index}" for index in range(1, 13))
    memo.replace_memo(conn, actor=DEFAULT_ACTOR, text=body, reason="one")
    changed = body.replace("line 6", "line six")
    memo.replace_memo(conn, actor=DEFAULT_ACTOR, text=changed, reason="two", if_rev=1)
    unified = memo.diff_memos(conn, 1, 2)["unified"]
    assert "line 3" in unified  # three lines of leading context
    assert "line 9" in unified  # three lines of trailing context
    assert "line 10" not in unified


# --------------------------------------------------------------------------- #
# CLI documents and exit codes
# --------------------------------------------------------------------------- #


def test_reading_an_empty_memo_is_deterministic(run: RunCli, db_path: Path) -> None:
    code, document = _cli(run, db_path, "memo")
    assert code == 0
    assert set(document) == MEMO_DOCUMENT_KEYS
    assert document == {
        "schema_version": 1,
        "rev": 0,
        "text": "",
        "author": None,
        "created_at": None,
        "bytes": 0,
    }
    assert "updated_at" not in document


def test_reads_need_no_actor(run: RunCli, db_path: Path) -> None:
    for argv in (
        ("memo",),
        ("memo", "--rev", "0"),
        ("memo", "--history"),
        ("memo", "--diff"),
    ):
        code, _document = _cli(run, db_path, *argv)
        assert code == 0, argv


def test_writes_need_an_actor(run: RunCli, db_path: Path) -> None:
    code, document = _cli(run, db_path, "memo", "text", "--reason", "r")
    assert code == 2
    assert document["error"]["code"] == "usage"


def test_the_write_envelope_names_the_new_rev(run: RunCli, db_path: Path) -> None:
    code, document = _write(run, db_path, "memo", "hello", "--reason", "start")
    assert code == 0
    assert document["schema_version"] == 1
    assert document["changed"] is True
    assert document["memo"]["rev"] == 1
    assert document["memo"]["text"] == "hello"
    assert document["memo"]["bytes"] == 5


def test_the_write_envelope_reports_a_no_op(run: RunCli, db_path: Path) -> None:
    _write(run, db_path, "memo", "hello", "--reason", "start")
    code, document = _write(
        run, db_path, "memo", "hello", "--reason", "again", "--if-rev", "1"
    )
    assert code == 0
    assert document["changed"] is False
    assert document["memo"]["rev"] == 1


def test_read_rev_and_history_documents(run: RunCli, db_path: Path) -> None:
    _write(run, db_path, "memo", "one", "--reason", "first")
    _write(run, db_path, "memo", "--append", "two")

    code, document = _cli(run, db_path, "memo", "--rev", "1")
    assert code == 0
    assert document["text"] == "one"
    assert set(document) == MEMO_DOCUMENT_KEYS

    code, document = _cli(run, db_path, "memo", "--history")
    assert code == 0
    assert document["current_rev"] == 2
    assert [item["rev"] for item in document["items"]] == [2, 1]
    assert set(document["items"][0]) == {
        "rev",
        "author",
        "created_at",
        "bytes",
        "reason",
    }

    code, document = _cli(run, db_path, "memo", "--history", "--limit", "1")
    assert [item["rev"] for item in document["items"]] == [2]


def test_a_missing_revision_read_is_exit_3(run: RunCli, db_path: Path) -> None:
    code, document = _cli(run, db_path, "memo", "--rev", "3")
    assert code == 3
    assert document["error"]["code"] == "not_found"
    assert document["error"]["details"] == {"rev": 3, "current": 0}


def test_the_diff_document_shape_and_bare_form(run: RunCli, db_path: Path) -> None:
    _write(run, db_path, "memo", "alpha\nbeta\n", "--reason", "one")
    _write(run, db_path, "memo", "--append", "gamma")

    code, document = _cli(run, db_path, "memo", "--diff", "1", "2")
    assert code == 0
    assert set(document) == {
        "schema_version",
        "from",
        "to",
        "added",
        "removed",
        "unified",
    }
    assert (document["from"], document["to"]) == (1, 2)
    assert document["added"] == 1

    bare_code, bare = _cli(run, db_path, "memo", "--diff")
    assert bare_code == 0
    assert (bare["from"], bare["to"]) == (1, 2)

    headless, headless_document = _cli(run, db_path, "memo", "--diff", "1")
    assert headless == 0
    assert headless_document == document


def test_a_bare_diff_on_an_empty_memo_is_empty(run: RunCli, db_path: Path) -> None:
    code, document = _cli(run, db_path, "memo", "--diff")
    assert code == 0
    assert (document["from"], document["to"]) == (0, 0)
    assert document["unified"] == ""


def test_choosing_two_modes_is_a_usage_error(run: RunCli, db_path: Path) -> None:
    for argv in (
        ("memo", "text", "--append", "more", "--reason", "r"),
        ("memo", "--file", "-", "--rev", "1"),
        ("memo", "--clear", "--history"),
        ("memo", "--restore", "1", "--append", "x", "--reason", "r"),
    ):
        code, document = _write(run, db_path, *argv)
        assert code == 2, argv
        assert document["error"]["code"] == "usage"


def test_empty_positional_text_points_at_clear(run: RunCli, db_path: Path) -> None:
    code, document = _write(run, db_path, "memo", "", "--reason", "r")
    assert code == 2
    assert "--clear" in document["error"]["message"]


def test_the_memo_file_form_matches_the_description_file_form(
    run: RunCli, db_path: Path, tmp_path: Path
) -> None:
    """One fixture, both path surfaces: same result and same error contract.

    ``memo --file`` and ``create --description-file`` read through one helper,
    so valid UTF-8, an absent path and invalid UTF-8 must agree exactly.
    """
    valid = tmp_path / "text.txt"
    valid.write_text("from a file\n", encoding="utf-8")
    absent = tmp_path / "absent.txt"
    invalid = tmp_path / "invalid.txt"
    invalid.write_bytes(b"\xff\xfe\x00not utf-8")

    memo_code, memo_document = _write(
        run, db_path, "memo", "--file", str(valid), "--reason", "r"
    )
    create_code, create_document = _write(
        run, db_path, "create", "a task", "--description-file", str(valid)
    )
    assert (memo_code, create_code) == (0, 0)
    # UTF-8 verbatim: no trailing-newline stripping, on either surface.
    assert memo_document["memo"]["text"] == "from a file\n"
    assert create_document["task"]["description"] == "from a file\n"

    memo_code, memo_document = _write(
        run, db_path, "memo", "--file", str(absent), "--reason", "r"
    )
    create_code, create_document = _write(
        run, db_path, "create", "another task", "--description-file", str(absent)
    )
    assert (memo_code, create_code) == (3, 3)
    assert memo_document["error"]["code"] == create_document["error"]["code"]
    assert memo_document["error"]["code"] == "not_found"

    memo_code, memo_document = _write(
        run, db_path, "memo", "--file", str(invalid), "--reason", "r"
    )
    create_code, create_document = _write(
        run, db_path, "create", "a third task", "--description-file", str(invalid)
    )
    assert (memo_code, create_code) == (4, 4)
    assert memo_document["error"]["code"] == create_document["error"]["code"]
    assert memo_document["error"]["code"] == "validation"
    for document in (memo_document, create_document):
        assert str(invalid) in document["error"]["message"]


def test_invalid_utf8_on_stdin_is_a_validation_error(
    run: RunCli, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stdin bytes are decoded by Python; a failure lands where a bad file does."""
    monkeypatch.setattr(
        "sys.stdin", io.TextIOWrapper(io.BytesIO(b"\xff\xfe"), encoding="utf-8")
    )
    code, document = _write(run, db_path, "create", "task", "--description-file", "-")
    assert code == 4
    assert document["error"]["code"] == "validation"


def test_read_modifiers_are_usage_errors(run: RunCli, db_path: Path) -> None:
    for argv in (
        ("memo", "--history", "--reason", "r"),
        ("memo", "--diff", "--force"),
        ("memo", "--rev", "1", "--dry-run"),
        ("memo", "text", "--limit", "1", "--reason", "r"),
    ):
        code, document = _cli(run, db_path, *argv)
        assert code == 2, argv


def test_three_diff_revisions_are_a_usage_error(run: RunCli, db_path: Path) -> None:
    code, document = _cli(run, db_path, "memo", "--diff", "1", "2", "3")
    assert code == 2
    assert document["error"]["code"] == "usage"


def test_edit_without_a_terminal_is_exit_2(run: RunCli, db_path: Path) -> None:
    code, document = _write(run, db_path, "memo", "--edit", "--reason", "r")
    assert code == 2
    assert document["error"]["code"] == "usage"
    assert "--file" in document["error"]["message"]
    assert memo.current_rev(schema.open_database(db_path)) == 0


def test_a_write_with_an_unknown_actor_shape_is_rejected(run: RunCli, db_path: Path) -> None:
    code, out, _err = run(
        "memo",
        "text",
        "--reason",
        "r",
        "--agent",
        "not an agent",
        "--db",
        str(db_path),
        "--json",
    )
    assert code == 2
    assert _json_document(out)["error"]["code"] == "usage"


# --------------------------------------------------------------------------- #
# migration, backup and doctor
# --------------------------------------------------------------------------- #


def test_a_v1_board_is_refused_until_migrated(db_path: Path) -> None:
    _as_v1_board(db_path)
    with pytest.raises(schema.SchemaError) as caught:
        schema.open_database(db_path)
    assert caught.value.exit_code == 6
    assert "tack migrate" in caught.value.message


def test_migration_from_one_to_two_adds_the_memo_table(db_path: Path) -> None:
    _as_v1_board(db_path)
    result = schema.migrate_database(db_path)
    assert result["migrated"] is True
    assert result["from_version"] == 1
    assert result["to_version"] == schema.SCHEMA_VERSION == 2
    assert result["database_schema_version"] == 2
    assert Path(result["backup"]).exists()

    connection = schema.open_database(db_path)
    try:
        # The task the v1 board carried survived, and the memo now works.
        assert service.list_tasks(connection)[0]["title"] == "Seeded before"
        assert memo.read_memo(connection)["rev"] == 0
        memo.replace_memo(connection, actor=DEFAULT_ACTOR, text="after", reason="first")
        assert memo.current_rev(connection) == 1
        raw = connection.execute("PRAGMA user_version").fetchone()[0]
        assert raw == 2
    finally:
        connection.close()

    report = schema.doctor(db_path)
    assert report["errors"] == []


def test_migration_is_idempotent_at_version_two(db_path: Path) -> None:
    assert schema.migrate_database(db_path)["migrated"] is False


def test_backup_round_trip_preserves_every_revision(db_path: Path, tmp_path: Path) -> None:
    connection = schema.open_database(db_path)
    try:
        memo.replace_memo(connection, actor=DEFAULT_ACTOR, text="one", reason="first")
        memo.append_memo(connection, actor=DEFAULT_ACTOR, text="two")
        memo.replace_memo(
            connection, actor="other", text="three", reason="third", if_rev=2
        )
    finally:
        connection.close()

    destination = tmp_path / "snapshot.sqlite3"
    schema.backup_database(db_path, destination)

    restored = schema.open_database(destination)
    try:
        assert memo.current_rev(restored) == 3
        assert memo.read_memo(restored, 1)["text"] == "one"
        assert memo.read_memo(restored, 2)["author"] == DEFAULT_ACTOR
        assert memo.read_memo(restored, 3)["text"] == "three"
        assert [item["rev"] for item in memo.memo_history(restored)["items"]] == [3, 2, 1]
    finally:
        restored.close()


def test_doctor_is_quiet_about_a_healthy_memo(db_path: Path) -> None:
    connection = schema.open_database(db_path)
    try:
        memo.replace_memo(connection, actor=DEFAULT_ACTOR, text="fine", reason="first")
    finally:
        connection.close()
    report = schema.doctor(db_path)
    assert report["ok"] is True
    assert report["errors"] == []


def _insert_revision(path: Path, rev: int, text: str, author: str) -> None:
    """Write a revision row directly, bypassing the writer (seeded corruption)."""
    raw = _raw(path)
    try:
        raw.execute(
            "INSERT INTO memo_revisions(rev, text, author, created_at, reason) "
            "VALUES(?, ?, ?, ?, NULL)",
            (rev, text, author, "2026-01-01T00:00:00Z"),
        )
    finally:
        raw.close()


def test_doctor_detects_a_revision_gap(db_path: Path) -> None:
    connection = schema.open_database(db_path)
    try:
        memo.replace_memo(connection, actor=DEFAULT_ACTOR, text="one", reason="first")
        memo.append_memo(connection, actor=DEFAULT_ACTOR, text="two")
    finally:
        connection.close()
    _insert_revision(db_path, 5, "tampered", DEFAULT_ACTOR)

    report = schema.doctor(db_path)
    findings = [entry for entry in report["errors"] if entry["code"] == "memo_revision_gap"]
    assert len(findings) == 1
    assert findings[0]["expected"] == 3
    assert findings[0]["found"] == 5
    assert report["ok"] is False


def test_doctor_detects_an_oversize_revision(db_path: Path) -> None:
    _insert_revision(db_path, 1, "a" * (memo.MAX_MEMO_BYTES + 1), DEFAULT_ACTOR)
    report = schema.doctor(db_path)
    findings = [
        entry for entry in report["errors"] if entry["code"] == "memo_revision_oversize"
    ]
    assert len(findings) == 1
    assert findings[0]["rev"] == 1
    assert findings[0]["bytes"] == memo.MAX_MEMO_BYTES + 1
    assert findings[0]["max"] == memo.MAX_MEMO_BYTES


def test_doctor_detects_a_blank_author(db_path: Path) -> None:
    _insert_revision(db_path, 1, "text", "   ")
    report = schema.doctor(db_path)
    assert any(entry["code"] == "memo_revision_author" for entry in report["errors"])


def test_doctor_detects_a_missing_memo_table(db_path: Path) -> None:
    _corrupt_memo_table(db_path)
    report = schema.doctor(db_path)
    assert any(entry["code"] == "memo_table_invalid" for entry in report["errors"])


def test_doctor_detects_an_unusable_memo_table(db_path: Path) -> None:
    _malformed_memo_table(db_path)
    report = schema.doctor(db_path)
    findings = [
        entry for entry in report["errors"] if entry["code"] == "memo_table_invalid"
    ]
    assert len(findings) == 1
    assert findings[0]["message"] == memo.MEMO_TABLE_INVALID_MESSAGE
    assert report["ok"] is False


def test_a_memo_read_on_a_missing_table_is_a_schema_error(
    run: RunCli, db_path: Path
) -> None:
    _corrupt_memo_table(db_path)
    code, document = _cli(run, db_path, "memo")
    assert code == 6
    error = document["error"]
    assert error["code"] == "memo_table_invalid"
    assert error["exit_code"] == 6
    assert error["retryable"] is False
    # A corrupt board must not read like a tool crash.
    assert "OperationalError" not in error["message"]
    assert error["message"] == memo.MEMO_TABLE_INVALID_MESSAGE


def test_a_memo_write_on_a_missing_table_is_a_schema_error(
    run: RunCli, db_path: Path
) -> None:
    _corrupt_memo_table(db_path)
    code, document = _write(run, db_path, "memo", "--append", "hello")
    assert code == 6
    error = document["error"]
    assert error["code"] == "memo_table_invalid"
    assert error["exit_code"] == 6
    assert "OperationalError" not in error["message"]
    assert error["message"] == memo.MEMO_TABLE_INVALID_MESSAGE


def test_a_memo_read_on_an_unusable_table_is_a_schema_error(
    run: RunCli, db_path: Path
) -> None:
    # The table exists with the wrong columns, so only a guard wrapped around
    # the real query — not a "does the table answer" probe — catches this shape.
    _malformed_memo_table(db_path)
    code, document = _cli(run, db_path, "memo")
    assert code == 6
    error = document["error"]
    assert error["code"] == "memo_table_invalid"
    assert error["exit_code"] == 6
    assert error["retryable"] is False
    assert "OperationalError" not in error["message"]
    assert "no such column" not in error["message"]
    assert error["message"] == memo.MEMO_TABLE_INVALID_MESSAGE


def test_a_memo_write_on_an_unusable_table_is_a_schema_error(
    run: RunCli, db_path: Path
) -> None:
    _malformed_memo_table(db_path)
    code, document = _write(run, db_path, "memo", "--append", "hello")
    assert code == 6
    error = document["error"]
    assert error["code"] == "memo_table_invalid"
    assert error["exit_code"] == 6
    assert "OperationalError" not in error["message"]
    assert error["message"] == memo.MEMO_TABLE_INVALID_MESSAGE


def test_the_memo_table_is_append_only(db_path: Path, conn: Any) -> None:
    memo.replace_memo(conn, actor=DEFAULT_ACTOR, text="one", reason="first")
    for statement in (
        "UPDATE memo_revisions SET text = 'tampered' WHERE rev = 1",
        "DELETE FROM memo_revisions WHERE rev = 1",
    ):
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute(statement)


# --------------------------------------------------------------------------- #
# concurrency
# --------------------------------------------------------------------------- #


def _run_append_workers(
    db: Path, invocations: list[list[str]]
) -> list[dict[str, object]]:
    processes = [
        subprocess.Popen(
            [sys.executable, APPEND_WORKER, *arguments],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for arguments in invocations
    ]
    results: list[dict[str, object]] = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=TIMEOUT_SECONDS)
        assert process.returncode == 0, stderr
        results.append(json.loads(stdout.strip()))
    return results


def test_four_processes_append_and_all_survive(db_path: Path) -> None:
    connection = schema.open_database(db_path)
    try:
        memo.replace_memo(connection, actor="seed", text="seed", reason="first")
    finally:
        connection.close()

    agents = [f"agent-{index}" for index in range(4)]
    results = _run_append_workers(
        db_path, [[str(db_path), agent, f"line from {agent}"] for agent in agents]
    )

    assert all(result["changed"] for result in results)
    # Every append got its own revision: 1 (seed) .. 5.
    assert sorted(int(str(result["rev"])) for result in results) == [2, 3, 4, 5]

    connection = schema.open_database(db_path)
    try:
        text = memo.read_memo(connection)["text"]
        assert "seed" in text
        for agent in agents:
            assert f"line from {agent}" in text, text
        assert memo.current_rev(connection) == 5
    finally:
        connection.close()


# --------------------------------------------------------------------------- #
# the memo never leaks into the task surface
# --------------------------------------------------------------------------- #


def test_the_memo_never_leaks_into_the_task_surface(run: RunCli, db_path: Path) -> None:
    _write(run, db_path, "create", "Prerequisite", "--status", "ready")
    _write(
        run,
        db_path,
        "create",
        "Dependent",
        "--status",
        "ready",
        "--depends-on",
        "T-000001",
    )
    _write(run, db_path, "claim", "T-000001")

    def snapshot() -> dict[str, str]:
        out: dict[str, str] = {}
        for key, argv in (
            ("board", ("board",)),
            ("list", ("list",)),
            ("counts", ("board", "--count")),
            ("next", ("next", "--agent", DEFAULT_ACTOR)),
            ("graph", ("graph",)),
            ("deps", ("deps", "T-000002")),
            ("history", ("history", "T-000002")),
            ("doctor", ("doctor",)),
        ):
            code, out_text = _text(run, db_path, *argv)
            assert code in {0, 6}, (key, out_text)
            out[key] = out_text
        return out

    before = snapshot()
    rows_before = _row_counts(db_path)

    _write(run, db_path, "memo", "standing note", "--reason", "first")
    _write(run, db_path, "memo", "--append", "more")
    _write(run, db_path, "memo", "--append", "even more")

    after = snapshot()
    assert after == before
    assert _row_counts(db_path) == rows_before

    # The memo's own events are board-level: they carry no task id, so
    # ``history <TASK_ID>`` is untouched while the board-wide log grows.
    code, board_history = _text(run, db_path, "history")
    assert code == 0
    memo_events = [
        item
        for item in _json_document(board_history)["items"]
        if str(item["event_type"]).startswith("memo.")
    ]
    assert len(memo_events) == 3
    assert all(item["task_id"] is None for item in memo_events)


def _row_counts(path: Path) -> dict[str, int]:
    raw = _raw(path)
    try:
        return {
            table: int(raw.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("tasks", "claims", "dependencies", "task_labels")
        }
    finally:
        raw.close()
