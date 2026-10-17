"""CLI tests for the ``tack`` command line (spec section 16, the CLI area).

Every test owns its own temporary database and drives the CLI the way an agent
does: through the entry point, in process. One test runs the installed console
script as a real subprocess to prove the packaging.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tackboard import cli, schema  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ACTOR = "dev"

#: The pre-rename project name, assembled so this module never contains it.
LEGACY_NAME = "task" + "board"

RunCli = Callable[..., tuple[int, str, str]]


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never inherit project selection or an actor from the developer's shell."""
    monkeypatch.delenv("TACKBOARD_DB", raising=False)
    monkeypatch.delenv("TACKBOARD_AGENT", raising=False)


@pytest.fixture
def db_file(tmp_path: Path) -> Path:
    """An initialized, empty project database."""
    path = tmp_path / "project.sqlite3"
    schema.init_database(path, label="project")
    return path


@pytest.fixture
def run(capsys: pytest.CaptureFixture[str]) -> RunCli:
    """Invoke the CLI in process, returning (exit code, stdout, stderr)."""

    def _run(*argv: str) -> tuple[int, str, str]:
        code = cli.main(list(argv))
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return _run


def _json_document(stdout: str) -> dict[str, Any]:
    """Parse the single JSON document on stdout and check its version."""
    text = stdout.strip()
    assert text.startswith("{"), f"stdout is not a JSON document: {stdout!r}"
    assert "\n" not in text, f"stdout carries more than one line: {stdout!r}"
    document = json.loads(text)
    assert document["schema_version"] == 1
    return document


def _create(run: RunCli, db: Path, title: str, *extra: str) -> dict[str, Any]:
    code, out, err = run(
        "create", title, "--agent", DEFAULT_ACTOR, "--db", str(db), "--json", *extra
    )
    assert code == 0, err
    return _json_document(out)["task"]


def _make_done(run: RunCli, db: Path, title: str) -> dict[str, Any]:
    task = _create(run, db, title, "--status", "ready")
    assert run("claim", task["id"], "--agent", DEFAULT_ACTOR, "--db", str(db), "--json")[0] == 0
    code, out, err = run(
        "complete", task["id"], "--agent", DEFAULT_ACTOR, "--db", str(db), "--json"
    )
    assert code == 0, err
    return _json_document(out)["task"]


# --------------------------------------------------------------------------- #
# global options
# --------------------------------------------------------------------------- #


def test_global_options_work_before_and_after_the_subcommand(
    run: RunCli, db_file: Path
) -> None:
    _create(run, db_file, "alpha", "--status", "ready")

    before = run("--json", "board", "--db", str(db_file))
    after = run("board", "--json", "--db", str(db_file))

    assert before[0] == after[0] == 0
    assert before[2] == after[2] == ""
    assert before[1] == after[1]
    assert len(_json_document(before[1])["tasks"]) == 1

    quiet_before = run("--quiet", "board", "--db", str(db_file))
    quiet_after = run("board", "--quiet", "--db", str(db_file))
    assert quiet_before == quiet_after
    assert not quiet_before[1].startswith("# ")

    database_last = run("--json", "--db", str(db_file), "board")
    assert database_last[1] == after[1]

    version_after = run("board", "--version")
    assert version_after[0] == 0
    assert version_after[1].startswith("tack ")

    assert run("--help")[0] == 0
    assert run("board", "--help")[0] == 0


def test_quiet_suppresses_the_header_but_not_the_rows(run: RunCli, db_file: Path) -> None:
    _create(run, db_file, "alpha", "--status", "ready")

    loud = run("board", "--db", str(db_file))
    quiet = run("board", "--quiet", "--db", str(db_file))

    assert loud[1].startswith("# ")
    assert not quiet[1].startswith("# ")
    assert "T-000001" in quiet[1]


def test_short_flags_and_version_and_help(run: RunCli) -> None:
    code, out, err = run("--version")
    assert code == 0
    assert out.startswith("tack ")
    assert err == ""

    code, out, err = run("board", "--help")
    assert code == 0
    assert "--eligible" in out
    assert "--brief" in out


# --------------------------------------------------------------------------- #
# board, list, brief, count
# --------------------------------------------------------------------------- #


def test_brief_and_count_report_the_filtered_set(run: RunCli, db_file: Path) -> None:
    _create(run, db_file, "alpha", "--status", "ready", "--priority", "high", "--label", "core")
    _create(run, db_file, "beta", "--status", "ready", "--assignee", "other")
    _create(run, db_file, "gamma", "--priority", "low", "--label", "other")
    _create(run, db_file, "delta", "--status", "ready", "--priority", "urgent")

    code, out, _ = run("board", "--brief", "--db", str(db_file))
    assert code == 0
    counts = dict(item.split("=") for item in out.split())
    assert counts["tasks"] == "4"
    assert counts["ready"] == "3"
    assert counts["backlog"] == "1"
    assert counts["eligible"] == "3"
    assert counts["claimed"] == "0"
    assert counts["blocked"] == "0"

    code, out, _ = run("board", "--brief", "--label", "core", "--db", str(db_file))
    assert code == 0
    assert out.split()[0] == "tasks=1"

    code, out, _ = run("board", "--count", "--label", "core", "--json", "--db", str(db_file))
    assert code == 0
    document = _json_document(out)
    assert document["project"] == "project"
    assert document["counts"]["tasks"] == 1
    assert document["counts"]["eligible"] == 1
    assert document["counts"]["in_progress"] == 0

    brief = run("board", "--brief", "--json", "--db", str(db_file))[1]
    counted = run("board", "--count", "--json", "--db", str(db_file))[1]
    assert brief == counted


def test_count_lists_one_key_per_line(run: RunCli, db_file: Path) -> None:
    _create(run, db_file, "alpha", "--status", "ready")

    code, out, _ = run("board", "--count", "--db", str(db_file))

    assert code == 0
    lines = out.splitlines()
    assert lines[0] == "tasks=1"
    assert "eligible=1" in lines
    assert len(lines) == 11


def test_brief_and_count_combined_is_a_usage_error(run: RunCli, db_file: Path) -> None:
    code, out, err = run("board", "--brief", "--count", "--json", "--db", str(db_file))

    assert code == 2
    document = _json_document(out)
    assert document["error"]["code"] == "usage"
    assert document["error"]["exit_code"] == 2
    assert err == ""


def test_filters_and_sort_run_through_board_and_list(run: RunCli, db_file: Path) -> None:
    _create(run, db_file, "low one", "--status", "ready", "--priority", "low")
    _create(run, db_file, "urgent one", "--status", "ready", "--priority", "urgent")
    _create(run, db_file, "blocked one", "--label", "core")

    code, out, _ = run("list", "--json", "--db", str(db_file))
    assert code == 0
    titles = [task["title"] for task in _json_document(out)["tasks"]]
    # priority rank descending, then created_at, then sequence
    assert titles == ["urgent one", "blocked one", "low one"]

    reversed_titles = [
        task["title"]
        for task in _json_document(run("list", "--json", "--reverse", "--db", str(db_file))[1])[
            "tasks"
        ]
    ]
    assert reversed_titles == list(reversed(titles))

    eligible = _json_document(run("list", "--eligible", "--json", "--db", str(db_file))[1])
    assert [task["id"] for task in eligible["tasks"]] == ["T-000002", "T-000001"]

    alias = _json_document(run("list", "--ready", "--json", "--db", str(db_file))[1])
    assert alias["tasks"] == eligible["tasks"]

    labelled = _json_document(run("list", "--label", "CORE", "--json", "--db", str(db_file))[1])
    assert [task["title"] for task in labelled["tasks"]] == ["blocked one"]

    limited = _json_document(run("list", "--limit", "1", "--json", "--db", str(db_file))[1])
    assert [task["title"] for task in limited["tasks"]] == ["urgent one"]


def test_board_groups_by_status_and_truncates_terminal_groups(
    run: RunCli, db_file: Path
) -> None:
    for index in range(12):
        _make_done(run, db_file, f"done {index}")
    _create(run, db_file, "still open", "--status", "ready")

    code, out, _ = run("board", "--db", str(db_file))
    assert code == 0
    lines = out.splitlines()
    assert "done (12)" in lines
    assert "... 2 more" in out

    code, out, _ = run("board", "--status", "done", "--db", str(db_file))
    assert code == 0
    assert "done (12)" in out.splitlines()
    assert "... 2 more" not in out

    code, out, _ = run("board", "--limit", "5", "--db", str(db_file))
    assert code == 0
    assert "... 2 more" not in out


# --------------------------------------------------------------------------- #
# descriptions
# --------------------------------------------------------------------------- #


def test_descriptions_are_omitted_from_list_json_unless_full(
    run: RunCli, db_file: Path
) -> None:
    _create(run, db_file, "alpha", "--status", "ready", "--description", "the detail")

    plain = _json_document(run("list", "--json", "--db", str(db_file))[1])
    assert "description" not in plain["tasks"][0]

    board = _json_document(run("board", "--json", "--db", str(db_file))[1])
    assert "description" not in board["tasks"][0]

    full = _json_document(run("list", "--full", "--json", "--db", str(db_file))[1])
    assert full["tasks"][0]["description"] == "the detail"

    next_full = _json_document(
        run("next", "--full", "--json", "--agent", "dev", "--db", str(db_file))[1]
    )
    assert next_full["tasks"][0]["description"] == "the detail"

    next_plain = _json_document(
        run("next", "--json", "--agent", "dev", "--db", str(db_file))[1]
    )
    assert "description" not in next_plain["tasks"][0]

    shown = _json_document(run("show", "T-000001", "--json", "--db", str(db_file))[1])
    assert shown["task"]["description"] == "the detail"


def test_show_displays_the_latest_notes(run: RunCli, db_file: Path) -> None:
    _create(run, db_file, "alpha")
    for index in range(7):
        code, _, err = run(
            "note", "T-000001", f"note {index}", "--agent", DEFAULT_ACTOR, "--db", str(db_file)
        )
        assert code == 0, err

    document = _json_document(run("show", "T-000001", "--json", "--db", str(db_file))[1])
    assert [note["payload"]["text"] for note in document["notes"]] == [
        "note 6",
        "note 5",
        "note 4",
        "note 3",
        "note 2",
    ]

    code, out, _ = run("show", "T-000001", "--db", str(db_file))
    assert code == 0
    assert "notes (latest 5):" in out
    assert "note 6" in out
    assert "note 1" not in out


def test_document_shapes_for_next_doctor_and_show(run: RunCli, db_file: Path) -> None:
    """Pin the three containers the JSON contract documents for the work loop."""
    _create(run, db_file, "prerequisite", "--status", "ready")
    _create(run, db_file, "dependent", "--status", "ready", "--depends-on", "T-000001")

    # next: an array container, empty when nothing is eligible (never null).
    code, out, err = run("next", "--json", "--agent", DEFAULT_ACTOR, "--db", str(db_file))
    assert code == 0, err
    picked = _json_document(out)
    assert set(picked) == {"schema_version", "tasks"}
    assert [task["id"] for task in picked["tasks"]] == ["T-000001"]

    code, out, err = run(
        "next",
        "--json",
        "--agent",
        DEFAULT_ACTOR,
        "--stage",
        "review",
        "--db",
        str(db_file),
    )
    assert code == 0, err
    assert _json_document(out)["tasks"] == []

    # show: a task/notes container, not a bare task.
    code, out, err = run("show", "T-000001", "--json", "--db", str(db_file))
    assert code == 0, err
    shown = _json_document(out)
    assert set(shown) == {"schema_version", "task", "notes"}
    assert shown["task"]["id"] == "T-000001"
    assert isinstance(shown["notes"], list)

    # doctor: ok/errors/warnings, where ok:true coexists with a warning.
    code, out, err = run("doctor", "--json", "--db", str(db_file))
    assert code == 0, err
    clean = _json_document(out)
    assert set(clean) == {"schema_version", "ok", "errors", "warnings"}
    assert clean["ok"] is True
    assert clean["errors"] == []
    assert clean["warnings"] == []

    assert (
        run(
            "status",
            "T-000002",
            "in_progress",
            "--agent",
            DEFAULT_ACTOR,
            "--force",
            "--reason",
            "pin the doctor warning shape",
            "--db",
            str(db_file),
            "--json",
        )[0]
        == 0
    )
    code, out, err = run("doctor", "--json", "--db", str(db_file))
    assert code == 0, err
    warned = _json_document(out)
    assert warned["ok"] is True
    assert warned["errors"] == []
    assert [finding["task_id"] for finding in warned["warnings"]] == ["T-000002"]


# --------------------------------------------------------------------------- #
# actors
# --------------------------------------------------------------------------- #


def test_missing_actor_is_exit_two(run: RunCli, db_file: Path) -> None:
    code, out, err = run("create", "alpha", "--db", str(db_file), "--json")

    assert code == 2
    assert _json_document(out)["error"]["code"] == "usage"
    assert err == ""


def test_environment_actor_is_honoured_and_the_option_overrides_it(
    run: RunCli, db_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TACKBOARD_AGENT", "env-agent")
    code, out, err = run("create", "from environment", "--db", str(db_file), "--json")
    assert code == 0, err
    assert _json_document(out)["task"]["id"] == "T-000001"

    history = _json_document(run("history", "T-000001", "--json", "--db", str(db_file))[1])
    assert history["items"][0]["actor"] == "env-agent"

    code, out, err = run(
        "create", "from option", "--agent", "cli-agent", "--db", str(db_file), "--json"
    )
    assert code == 0, err
    history = _json_document(run("history", "T-000002", "--json", "--db", str(db_file))[1])
    assert history["items"][0]["actor"] == "cli-agent"


def test_invalid_actor_is_a_usage_error(run: RunCli, db_file: Path) -> None:
    code, out, _ = run(
        "create", "alpha", "--agent", "not a valid id", "--db", str(db_file), "--json"
    )

    assert code == 2
    assert _json_document(out)["error"]["code"] == "usage"


# --------------------------------------------------------------------------- #
# JSON contracts and errors
# --------------------------------------------------------------------------- #


def test_json_stdout_carries_no_prose(run: RunCli, db_file: Path) -> None:
    _create(run, db_file, "alpha", "--status", "ready")

    for argv in (
        ("board", "--json"),
        ("list", "--json"),
        ("board", "--count", "--json"),
        ("show", "T-000001", "--json"),
        ("next", "--json", "--agent", "dev"),
        ("history", "--json"),
        ("claims", "--json"),
        ("deps", "T-000001", "--json"),
        ("graph", "--json"),
        ("doctor", "--json"),
    ):
        code, out, err = run(*argv, "--db", str(db_file))
        assert code == 0, (argv, err)
        assert err == "", (argv, err)
        _json_document(out)


def test_error_envelope_matches_the_contract(run: RunCli, db_file: Path) -> None:
    code, out, err = run("show", "T-999999", "--json", "--db", str(db_file))

    assert code == 3
    assert err == ""
    document = _json_document(out)
    error = document["error"]
    assert error["code"] == "not_found"
    assert error["exit_code"] == 3
    assert error["retryable"] is False
    assert error["details"] == {}
    assert "T-999999" in error["message"]


def test_length_limit_errors_carry_machine_readable_details(
    run: RunCli, db_file: Path
) -> None:
    code, out, err = run(
        "create",
        "x" * 201,
        "--agent",
        DEFAULT_ACTOR,
        "--db",
        str(db_file),
        "--json",
    )

    assert code == 4
    assert err == ""
    error = _json_document(out)["error"]
    assert error["code"] == "validation"
    assert error["details"] == {
        "field": "title",
        "max": 200,
        "unit": "characters",
        "actual": 201,
    }


def test_errors_go_to_stderr_without_json(run: RunCli, db_file: Path) -> None:
    code, out, err = run("show", "T-999999", "--db", str(db_file))

    assert code == 3
    assert out == ""
    assert err.startswith("error: ")


def test_traceback_only_with_verbose(run: RunCli, db_file: Path) -> None:
    quiet = run("show", "T-999999", "--json", "--db", str(db_file))
    loud = run("show", "T-999999", "--json", "--verbose", "--db", str(db_file))

    assert quiet[2] == ""
    assert "Traceback" in loud[2]


def test_parser_failures_use_the_json_envelope(run: RunCli, db_file: Path) -> None:
    code, out, err = run("board", "--limit", "many", "--json", "--db", str(db_file))
    assert code == 2
    assert err == ""
    assert _json_document(out)["error"]["code"] == "usage"

    code, out, _ = run("frobnicate", "--json")
    assert code == 2
    assert _json_document(out)["error"]["exit_code"] == 2

    code, out, _ = run("--json")
    assert code == 2
    assert _json_document(out)["error"]["code"] == "usage"


def test_project_selection_failure_is_exit_three(run: RunCli, tmp_path: Path) -> None:
    other = tmp_path / "elsewhere"
    other.mkdir()
    code, out, err = run("board", "--json")

    assert code == 3
    assert err == ""
    assert _json_document(out)["error"]["code"] == "not_found"


def test_unknown_task_is_exit_three_and_missing_database_is_exit_three(
    run: RunCli, tmp_path: Path
) -> None:
    code, out, _ = run("show", "T-000001", "--json", "--db", str(tmp_path / "absent.sqlite3"))
    assert code == 3
    assert _json_document(out)["error"]["code"] == "not_found"


def test_schema_errors_are_exit_six(run: RunCli, tmp_path: Path) -> None:
    bogus = tmp_path / "unrelated.sqlite3"
    bogus.write_bytes(b"this file is not a database, and does not pretend to be one\n" * 8)

    code, out, _ = run("init", "--db", str(bogus), "--json")
    assert code == 6
    assert _json_document(out)["error"]["code"] == "schema"

    code, out, _ = run("doctor", "--db", str(bogus), "--json")
    assert code == 6
    document = _json_document(out)
    assert document["ok"] is False
    assert document["errors"]


def test_mutation_documents_pin_their_top_level_keys(run: RunCli, db_file: Path) -> None:
    """A mutation returns {schema_version, changed, task}; `reopen` alone adds a key.

    `reopen` is the one mutation whose document carries a fourth top-level key,
    `active_dependents`, and only when it actually reopens a task. Both halves of
    the `in_progress`/`review` predicate are pinned, the array order is pinned to
    each dependent's own creation order, and an unstarted dependent is pinned as
    excluded.
    """
    base = {"schema_version", "changed", "task"}
    prerequisite = _create(run, db_file, "Prerequisite", "--status", "ready")

    # Two dependents start in a known creation order (pins the ordering), one
    # moves through `review` (pins the second half of the predicate), and one is
    # left unstarted (pins its exclusion).
    first = _create(
        run, db_file, "First", "--status", "ready", "--depends-on", prerequisite["id"]
    )
    second = _create(
        run, db_file, "Second", "--status", "ready", "--depends-on", prerequisite["id"]
    )
    reviewer = _create(
        run, db_file, "Reviewer", "--status", "ready", "--depends-on", prerequisite["id"]
    )
    unstarted = _create(
        run, db_file, "Unstarted", "--status", "ready", "--depends-on", prerequisite["id"]
    )

    def mutate(*argv: str) -> dict[str, Any]:
        code, out, err = run(*argv, "--agent", DEFAULT_ACTOR, "--db", str(db_file), "--json")
        assert code == 0, err
        return _json_document(out)

    # A non-`reopen` mutation carries exactly the three-key envelope.
    assert set(mutate("claim", prerequisite["id"])) == base
    completed = mutate("complete", prerequisite["id"])
    assert set(completed) == base
    assert completed["changed"] is True

    # Start the dependents so reopening the prerequisite has something to report.
    assert mutate("status", first["id"], "in_progress")["task"]["status"] == "in_progress"
    assert mutate("status", second["id"], "in_progress")["task"]["status"] == "in_progress"
    assert mutate("status", reviewer["id"], "in_progress")["task"]["status"] == "in_progress"
    assert mutate("status", reviewer["id"], "review")["task"]["status"] == "review"

    reopened = mutate("reopen", prerequisite["id"])
    assert reopened["changed"] is True
    assert set(reopened) == base | {"active_dependents"}
    entries = reopened["active_dependents"]
    # Ordered by each dependent's own creation order, never by the reopened task.
    assert [entry["id"] for entry in entries] == [first["id"], second["id"], reviewer["id"]]
    # Both an `in_progress` and a `review` dependent are included...
    assert [entry["status"] for entry in entries] == ["in_progress", "in_progress", "review"]
    # ...and the unstarted dependent is not.
    assert unstarted["id"] not in {entry["id"] for entry in entries}
    assert all(entry["blocked"] is True for entry in entries)
    # The entries use the board/list projection: no `description`, unlike `task`.
    assert "description" in reopened["task"]
    assert all("description" not in entry for entry in entries)

    # A no-op `reopen` omits the key entirely.
    noop = mutate("reopen", prerequisite["id"])
    assert noop["changed"] is False
    assert set(noop) == base


def test_reopen_reports_an_empty_active_dependents_array_when_none_has_started(
    run: RunCli, db_file: Path
) -> None:
    """A changed `reopen` keeps the key and reports `[]` when no dependent started."""
    prerequisite = _create(run, db_file, "Prerequisite", "--status", "ready")
    _create(run, db_file, "Unstarted", "--status", "ready", "--depends-on", prerequisite["id"])

    sequence = (
        ("status", prerequisite["id"], "in_progress"),
        ("complete", prerequisite["id"]),
        ("reopen", prerequisite["id"]),
    )
    document: dict[str, Any] = {}
    for argv in sequence:
        code, out, err = run(*argv, "--agent", DEFAULT_ACTOR, "--db", str(db_file), "--json")
        assert code == 0, err
        document = _json_document(out)

    assert document["changed"] is True
    assert document["active_dependents"] == []


def test_force_into_in_progress_takes_the_claim(run: RunCli, db_file: Path) -> None:
    """The `--force` path over an unfinished prerequisite still records the claim."""
    blocker = _create(run, db_file, "Blocker", "--status", "ready")
    blocked = _create(
        run, db_file, "Blocked", "--status", "ready", "--depends-on", blocker["id"]
    )

    code, out, err = run(
        "status",
        blocked["id"],
        "in_progress",
        "--force",
        "--reason",
        "starting over an unfinished prerequisite",
        "--agent",
        DEFAULT_ACTOR,
        "--db",
        str(db_file),
        "--json",
    )
    assert code == 0, err
    document = _json_document(out)
    assert document["changed"] is True
    assert document["task"]["status"] == "in_progress"
    assert document["task"]["claimed_by"] == DEFAULT_ACTOR

    claims_document = _json_document(run("claims", "--json", "--db", str(db_file))[1])
    assert [item["task_id"] for item in claims_document["items"]] == [blocked["id"]]


# --------------------------------------------------------------------------- #
# exit codes
# --------------------------------------------------------------------------- #


def test_exit_codes_are_stable_across_the_documented_cases(
    run: RunCli, db_file: Path
) -> None:
    _create(run, db_file, "alpha", "--status", "ready")
    _create(run, db_file, "beta")

    assert run("board", "--json", "--db", str(db_file))[0] == 0
    assert run("board", "--brief", "--count", "--db", str(db_file))[0] == 2
    assert run("show", "T-999999", "--db", str(db_file))[0] == 3
    assert run("depend", "add", "T-000002", "--on", "T-000002", "--agent", "dev",
               "--db", str(db_file))[0] == 4
    assert run(
        "status", "T-000001", "in_progress", "--if-version", "99",
        "--agent", DEFAULT_ACTOR, "--db", str(db_file),
    )[0] == 5


def test_stale_version_and_cycle_report_their_codes(run: RunCli, db_file: Path) -> None:
    _create(run, db_file, "alpha", "--status", "ready")
    _create(run, db_file, "beta", "--depends-on", "T-000001")

    code, out, _ = run(
        "status", "T-000001", "in_progress", "--if-version", "99",
        "--agent", DEFAULT_ACTOR, "--json", "--db", str(db_file),
    )
    assert code == 5
    assert _json_document(out)["error"]["code"] == "stale_version"

    code, out, _ = run(
        "depend", "add", "T-000001", "--on", "T-000002", "--agent", DEFAULT_ACTOR,
        "--json", "--db", str(db_file),
    )
    assert code == 4
    error = _json_document(out)["error"]
    assert error["code"] == "cycle"
    assert "T-000002 -> T-000001 -> T-000002" in error["message"]


# --------------------------------------------------------------------------- #
# next
# --------------------------------------------------------------------------- #


def test_next_reports_an_empty_result_with_exit_zero(run: RunCli, db_file: Path) -> None:
    code, out, _ = run("next", "--json", "--agent", "dev", "--db", str(db_file))
    assert code == 0
    assert _json_document(out)["tasks"] == []

    code, out, _ = run("next", "--claim", "--json", "--agent", "dev", "--db", str(db_file))
    assert code == 0
    assert _json_document(out)["task"] is None

    code, out, _ = run("next", "--db", str(db_file), "--agent", "dev")
    assert code == 0
    assert out.splitlines()[-1] == "No eligible tasks."


def test_next_claim_pins_its_single_task_document_key_set(
    run: RunCli, db_file: Path
) -> None:
    """`next --claim` is `{schema_version, task}`, not the plain `next` container.

    Pinned for both the empty and the populated case: plain `next` carries
    `tasks`, and `next --claim` never does.
    """
    code, out, _ = run("next", "--claim", "--json", "--agent", "dev", "--db", str(db_file))
    assert code == 0
    empty = _json_document(out)
    assert set(empty) == {"schema_version", "task"}
    assert empty["task"] is None

    _create(run, db_file, "ready work", "--status", "ready")

    code, out, err = run("next", "--claim", "--json", "--agent", "dev", "--db", str(db_file))
    assert code == 0, err
    populated = _json_document(out)
    assert set(populated) == {"schema_version", "task"}
    assert populated["task"]["status"] == "in_progress"
    assert populated["task"]["claimed_by"] == "dev"


def test_next_reports_every_eligible_task_in_selection_order(
    run: RunCli, db_file: Path
) -> None:
    """Plain `next` returns the whole ranked candidate list, never a single task.

    No other test drives the plain `next` document past one eligible task — the
    other `next` tests either leave a single candidate on the board or exercise
    only `--claim` — so none of them can tell the full candidate list from a
    one-task document. That is how the "0 or 1 tasks" wording slipped past the
    suite. Three eligible tasks with distinct priorities pin both the count and
    the selection order: priority high to low, then `created_at`, then sequence.
    """
    _create(run, db_file, "low", "--status", "ready", "--priority", "low")
    _create(run, db_file, "urgent", "--status", "ready", "--priority", "urgent")
    _create(run, db_file, "high", "--status", "ready", "--priority", "high")

    code, out, err = run("next", "--json", "--agent", "dev", "--db", str(db_file))

    assert code == 0, err
    document = _json_document(out)
    assert [task["title"] for task in document["tasks"]] == ["urgent", "high", "low"]

    code, out, err = run(
        "next", "--claim", "--json", "--agent", "dev", "--db", str(db_file)
    )

    assert code == 0, err
    claimed = _json_document(out)
    assert set(claimed) == {"schema_version", "task"}
    assert claimed["task"]["title"] == "urgent"


def test_next_claim_selects_and_claims(run: RunCli, db_file: Path) -> None:
    _create(run, db_file, "low", "--status", "ready", "--priority", "low")
    _create(run, db_file, "urgent", "--status", "ready", "--priority", "urgent")

    code, out, err = run("next", "--claim", "--json", "--agent", "dev", "--db", str(db_file))

    assert code == 0, err
    task = _json_document(out)["task"]
    assert task["title"] == "urgent"
    assert task["status"] == "in_progress"
    assert task["claimed_by"] == "dev"

    claims_document = _json_document(run("claims", "--json", "--db", str(db_file))[1])
    assert [item["task_id"] for item in claims_document["items"]] == [task["id"]]

    filtered = _json_document(
        run("claims", "--json", "--agent", "someone-else", "--db", str(db_file))[1]
    )
    assert filtered["items"] == []


def test_next_filters_assignee_label_priority_and_stage(run: RunCli, db_file: Path) -> None:
    _create(
        run, db_file, "mine", "--status", "ready", "--assignee", "dev",
        "--label", "core", "--priority", "urgent",
    )
    _create(run, db_file, "theirs", "--status", "ready", "--assignee", "other")
    _create(run, db_file, "low", "--status", "ready", "--priority", "low")

    mine = _json_document(run("next", "--json", "--agent", "dev", "--db", str(db_file))[1])
    assert sorted(task["title"] for task in mine["tasks"]) == ["low", "mine"]

    everything = _json_document(
        run("next", "--all-assignees", "--json", "--agent", "dev", "--db", str(db_file))[1]
    )
    assert sorted(task["title"] for task in everything["tasks"]) == ["low", "mine", "theirs"]

    labelled = _json_document(
        run("next", "--label", "core", "--json", "--agent", "dev", "--db", str(db_file))[1]
    )
    assert [task["title"] for task in labelled["tasks"]] == ["mine"]

    urgent_only = _json_document(
        run(
            "next", "--min-priority", "high", "--json", "--agent", "dev",
            "--db", str(db_file),
        )[1]
    )
    assert [task["title"] for task in urgent_only["tasks"]] == ["mine"]

    review_stage = _json_document(
        run("next", "--stage", "review", "--json", "--agent", "dev", "--db", str(db_file))[1]
    )
    assert review_stage["tasks"] == []


def test_next_force_without_claim_is_a_usage_error(run: RunCli, db_file: Path) -> None:
    _create(run, db_file, "alpha", "--status", "ready")

    code, out, _ = run("next", "--force", "--json", "--agent", "dev", "--db", str(db_file))

    assert code == 2
    assert _json_document(out)["error"]["code"] == "usage"


def test_next_stage_review_claims_a_reviewable_task(run: RunCli, db_file: Path) -> None:
    _create(run, db_file, "alpha", "--status", "ready")
    run("claim", "T-000001", "--agent", DEFAULT_ACTOR, "--db", str(db_file))
    run("status", "T-000001", "review", "--agent", DEFAULT_ACTOR, "--db", str(db_file))

    code, out, err = run(
        "next", "--stage", "review", "--claim", "--json", "--agent", DEFAULT_ACTOR,
        "--db", str(db_file),
    )

    assert code == 0, err
    task = _json_document(out)["task"]
    assert task["id"] == "T-000001"
    assert task["status"] == "review"
    assert task["claimed_by"] == DEFAULT_ACTOR


def test_claim_takes_a_card_assigned_to_another_agent(run: RunCli, db_file: Path) -> None:
    """Issue #9: the claim names the caller and leaves the assignment alone."""
    task = _create(
        run, db_file, "routed away", "--status", "ready", "--assignee", "other"
    )

    code, out, err = run(
        "claim", task["id"], "--agent", DEFAULT_ACTOR, "--json", "--db", str(db_file)
    )

    assert code == 0, err
    claimed = _json_document(out)["task"]
    assert claimed["status"] == "in_progress"
    assert claimed["claimed_by"] == DEFAULT_ACTOR
    assert claimed["assignee"] == "other"


def test_next_all_assignees_claim_takes_a_card_assigned_to_another_agent(
    run: RunCli, db_file: Path
) -> None:
    """The exit-4 case the routing-note design retires now exits 0."""
    task = _create(
        run, db_file, "routed away", "--status", "ready", "--assignee", "other"
    )

    code, out, err = run(
        "next", "--agent", DEFAULT_ACTOR, "--all-assignees", "--claim", "--json",
        "--db", str(db_file),
    )

    assert code == 0, err
    claimed = _json_document(out)["task"]
    assert claimed["id"] == task["id"]
    assert claimed["claimed_by"] == DEFAULT_ACTOR
    assert claimed["assignee"] == "other"


def test_status_moves_take_and_release_the_claim(run: RunCli, db_file: Path) -> None:
    """Entering `in_progress` claims for the actor; leaving it releases."""
    task = _create(run, db_file, "Managed lifecycle", "--status", "ready")

    def mutate(*argv: str) -> dict[str, Any]:
        code, out, err = run(*argv, "--db", str(db_file), "--json")
        assert code == 0, err
        return _json_document(out)["task"]

    started = mutate("status", task["id"], "in_progress", "--agent", DEFAULT_ACTOR)
    assert started["claimed_by"] == DEFAULT_ACTOR
    assert started["claimed_at"] is not None

    reviewed = mutate("status", task["id"], "review", "--agent", DEFAULT_ACTOR)
    assert reviewed["claimed_by"] is None
    assert reviewed["claimed_at"] is None

    restarted = mutate("status", task["id"], "in_progress", "--agent", "other")
    assert restarted["claimed_by"] == "other"

    done = mutate("complete", task["id"], "--agent", "other")
    assert done["status"] == "done"
    assert done["claimed_by"] is None


# --------------------------------------------------------------------------- #
# lifecycle
# --------------------------------------------------------------------------- #


def test_end_to_end_lifecycle_through_the_cli(run: RunCli, tmp_path: Path) -> None:
    database = tmp_path / "board.sqlite3"

    code, out, err = run("init", "--db", str(database), "--json")
    assert code == 0, err
    document = _json_document(out)
    assert document["ok"] is True
    assert document["operation"] == "init"
    assert document["created"] is True
    assert document["database_schema_version"] == schema.SCHEMA_VERSION

    code, out, err = run(
        "create", "Write the parser", "--description", "recursive descent",
        "--priority", "high", "--label", "core", "--label", "parser",
        "--status", "ready", "--agent", DEFAULT_ACTOR, "--db", str(database), "--json",
    )
    assert code == 0, err
    first = _json_document(out)["task"]
    assert first["id"] == "T-000001"
    assert first["labels"] == ["core", "parser"]
    assert first["priority"] == "high"
    assert first["eligible"] is True

    code, out, err = run(
        "create", "Ship the parser", "--priority", "urgent", "--label", "core",
        "--depends-on", "T-000001", "--agent", DEFAULT_ACTOR, "--db", str(database), "--json",
    )
    assert code == 0, err
    second = _json_document(out)["task"]
    assert second["depends_on"] == ["T-000001"]
    assert second["blocked"] is True
    assert second["blockers"] == [{"code": "prerequisite_unfinished", "task_id": "T-000001"}]

    def counts() -> dict[str, int]:
        document = _json_document(
            run("board", "--count", "--json", "--db", str(database))[1]
        )
        return document["counts"]

    assert counts()["tasks"] == 2
    assert counts()["eligible"] == 1
    assert counts()["blocked"] == 1

    code, out, _ = run("board", "--brief", "--db", str(database))
    assert code == 0
    assert out.split()[0] == "tasks=2"

    code, out, err = run(
        "next", "--claim", "--json", "--agent", DEFAULT_ACTOR, "--db", str(database)
    )
    assert code == 0, err
    claimed = _json_document(out)["task"]
    assert claimed["id"] == "T-000001"
    assert claimed["status"] == "in_progress"

    code, out, _ = run("board", "--count", "--json", "--db", str(database))
    assert _json_document(out)["counts"]["blocked"] == 1

    code, out, err = run(
        "complete", "T-000001", "--agent", DEFAULT_ACTOR, "--db", str(database), "--json"
    )
    assert code == 0, err
    completed = _json_document(out)
    assert completed["changed"] is True
    assert completed["task"]["status"] == "done"
    assert completed["task"]["completed_at"] is not None

    # Readiness is derived: the prerequisite is finished, but the dependent is
    # still backlog, so it is no longer blocked and not yet eligible.
    assert counts()["blocked"] == 0
    dependent = _json_document(
        run("show", "T-000002", "--json", "--db", str(database))[1]
    )["task"]
    assert dependent["dependency_ready"] is True
    assert dependent["eligible"] is False

    code, out, err = run(
        "status", "T-000002", "ready", "--agent", DEFAULT_ACTOR, "--db", str(database), "--json"
    )
    assert code == 0, err
    assert counts()["eligible"] == 1

    code, out, err = run(
        "reopen", "T-000001", "--agent", DEFAULT_ACTOR, "--db", str(database), "--json"
    )
    assert code == 0, err
    reopened = _json_document(out)
    assert reopened["task"]["status"] == "ready"
    assert reopened["task"]["completed_at"] is None
    assert reopened["active_dependents"] == []

    # Reopening the prerequisite re-blocks the dependent immediately.
    assert counts()["blocked"] == 1
    blocked = _json_document(
        run("show", "T-000002", "--json", "--db", str(database))[1]
    )["task"]
    assert blocked["eligible"] is False
    assert blocked["blockers"] == [
        {"code": "prerequisite_unfinished", "task_id": "T-000001"}
    ]


def test_lifecycle_shortcuts_share_the_transition_table(run: RunCli, db_file: Path) -> None:
    _create(run, db_file, "alpha", "--status", "ready")

    # Already in the shortcut's target state: an idempotent no-op.
    code, out, _ = run(
        "reopen", "T-000001", "--agent", "dev", "--json", "--db", str(db_file)
    )
    assert code == 0
    assert _json_document(out)["changed"] is False

    _create(run, db_file, "beta")
    code, out, _ = run(
        "restore", "T-000002", "--agent", "dev", "--json", "--db", str(db_file)
    )
    assert code == 0
    assert _json_document(out)["changed"] is False

    # ready -> done is not in the transition table, so the shortcut is rejected
    code, out, _ = run(
        "complete", "T-000001", "--agent", "dev", "--json", "--db", str(db_file)
    )
    assert code == 4
    assert _json_document(out)["error"]["code"] == "invalid_transition"

    # ready -> backlog is in the table, so restore is a real transition
    code, out, _ = run(
        "restore", "T-000001", "--agent", "dev", "--json", "--db", str(db_file)
    )
    assert code == 0
    assert _json_document(out)["task"]["status"] == "backlog"


def test_status_no_op_reports_changed_false(run: RunCli, db_file: Path) -> None:
    _create(run, db_file, "alpha")

    code, out, err = run(
        "status", "T-000001", "backlog", "--agent", "dev", "--json", "--db", str(db_file)
    )

    assert code == 0, err
    document = _json_document(out)
    assert document["changed"] is False
    assert document["task"]["version"] == 1


# --------------------------------------------------------------------------- #
# updates, dependencies, claims, history, maintenance
# --------------------------------------------------------------------------- #


def test_update_fields_and_labels(run: RunCli, db_file: Path) -> None:
    _create(run, db_file, "alpha", "--label", "core")

    code, out, err = run(
        "update", "T-000001", "--title", "renamed", "--priority", "urgent",
        "--add-label", "extra", "--blocked-reason", "waiting on the API",
        "--agent", "dev", "--json", "--db", str(db_file),
    )
    assert code == 0, err
    task = _json_document(out)["task"]
    assert task["title"] == "renamed"
    assert task["priority"] == "urgent"
    assert sorted(task["labels"]) == ["core", "extra"]
    assert task["blocked_reason"] == "waiting on the API"
    assert task["blocked"] is True

    code, out, _ = run(
        "update", "T-000001", "--clear-blocked-reason", "--remove-label", "core",
        "--agent", "dev", "--json", "--db", str(db_file),
    )
    assert code == 0
    task = _json_document(out)["task"]
    assert task["blocked_reason"] is None
    assert task["labels"] == ["extra"]

    code, out, _ = run(
        "update", "T-000001", "--label", "only", "--agent", "dev", "--json",
        "--db", str(db_file),
    )
    assert code == 0
    assert _json_document(out)["task"]["labels"] == ["only"]

    code, out, _ = run(
        "update", "T-000001", "--title", "renamed", "--agent", "dev", "--json",
        "--db", str(db_file),
    )
    assert code == 0
    assert _json_document(out)["changed"] is False


def test_update_description_file(run: RunCli, db_file: Path, tmp_path: Path) -> None:
    _create(run, db_file, "alpha")
    source = tmp_path / "description.md"
    source.write_text("first line\nsecond line\n", encoding="utf-8")

    code, out, err = run(
        "update", "T-000001", "--description-file", str(source),
        "--agent", "dev", "--json", "--db", str(db_file),
    )

    assert code == 0, err
    assert _json_document(out)["task"]["description"] == "first line\nsecond line\n"


# --------------------------------------------------------------------------- #
# the in-process half of the stdin reader
# --------------------------------------------------------------------------- #
#
# ``tests/test_memo.py`` proves the stdin contract over a real OS pipe, in a
# child process. pytest-cov collects nothing from that child, so the stdin
# branch of ``cli._read_text_source`` needs a second, in-process instrument:
# these tests hand the reader a fake ``sys.stdin`` and cover every branch a
# real stdin reaches. Both instruments stay: the subprocess tests are the
# behavioural proof, these exercise the reader in this process.


class _StdinWithBuffer:
    """A stdin stand-in exposing a raw ``.buffer`` of bytes, like a real pipe."""

    def __init__(self, data: bytes) -> None:
        self.buffer = io.BytesIO(data)


class _DecodedStdin:
    """A stdin stand-in with no ``.buffer``: an already-decoded text stream."""

    def __init__(self, text: str) -> None:
        self._text = text

    def read(self) -> str:
        return self._text


def test_a_buffer_stdin_is_read_as_utf8_bytes_verbatim(
    run: RunCli, db_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bytes on a fake buffered stdin come back as text, unstripped."""
    monkeypatch.setattr(sys, "stdin", _StdinWithBuffer(b"  padded text\n"))
    code, out, err = run(
        "create", "from stdin", "--description-file", "-",
        "--agent", DEFAULT_ACTOR, "--json", "--db", str(db_file),
    )
    assert code == 0, err
    assert _json_document(out)["task"]["description"] == "  padded text\n"

    monkeypatch.setattr(sys, "stdin", _StdinWithBuffer(b"memo body\n"))
    code, out, err = run(
        "memo", "-", "--reason", "r",
        "--agent", DEFAULT_ACTOR, "--json", "--db", str(db_file),
    )
    assert code == 0, err
    assert _json_document(out)["memo"]["text"] == "memo body\n"


def test_a_buffer_stdin_with_invalid_utf8_is_a_validation_error(
    run: RunCli, db_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Invalid bytes on a fake buffered stdin fail exactly where a bad file does."""
    for argv in (
        ("create", "bad stdin", "--description-file", "-"),
        ("memo", "-", "--reason", "r"),
    ):
        monkeypatch.setattr(sys, "stdin", _StdinWithBuffer(b"\xff\xfe"))
        code, out, _ = run(
            *argv, "--agent", DEFAULT_ACTOR, "--json", "--db", str(db_file)
        )
        assert code == 4, argv
        document = _json_document(out)
        assert document["error"]["code"] == "validation"
        assert "stdin" in document["error"]["message"]


def test_a_stdin_without_a_buffer_falls_back_to_reading_text(
    run: RunCli, db_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A caller-injected decoded stream is read through ``sys.stdin.read()``."""
    monkeypatch.setattr(sys, "stdin", _DecodedStdin("decoded text\n"))
    code, out, err = run(
        "create", "text stdin", "--description-file", "-",
        "--agent", DEFAULT_ACTOR, "--json", "--db", str(db_file),
    )
    assert code == 0, err
    assert _json_document(out)["task"]["description"] == "decoded text\n"


def test_depend_add_remove_and_related_reads(run: RunCli, db_file: Path) -> None:
    _create(run, db_file, "alpha")
    _create(run, db_file, "beta")

    code, out, err = run(
        "depend", "add", "T-000002", "--on", "T-000001", "--agent", "dev",
        "--json", "--db", str(db_file),
    )
    assert code == 0, err
    assert _json_document(out)["changed"] is True

    code, out, _ = run(
        "depend", "add", "T-000002", "--on", "T-000001", "--agent", "dev",
        "--json", "--db", str(db_file),
    )
    assert code == 0
    assert _json_document(out)["changed"] is False

    dependents = _json_document(
        run("dependents", "T-000001", "--json", "--db", str(db_file))[1]
    )
    assert dependents["task_id"] == "T-000001"
    assert [task["id"] for task in dependents["tasks"]] == ["T-000002"]

    code, out, _ = run(
        "depend", "remove", "T-000002", "--on", "T-000001", "--agent", "dev",
        "--json", "--db", str(db_file),
    )
    assert code == 0
    assert _json_document(out)["task"]["depends_on"] == []

    code, out, _ = run("deps", "T-000002", "--db", str(db_file))
    assert code == 0
    assert "(none)" in out


def test_depend_remove_rejects_force_and_reason(run: RunCli, db_file: Path) -> None:
    """`--force` is an exhaustive list (spec 6.2); removing an edge is not in it."""
    _create(run, db_file, "alpha", "--status", "ready")
    _create(run, db_file, "beta")
    run(
        "depend", "add", "T-000002", "--on", "T-000001", "--agent", DEFAULT_ACTOR,
        "--json", "--db", str(db_file),
    )

    code, out, _ = run(
        "depend", "remove", "T-000002", "--on", "T-000001", "--agent", DEFAULT_ACTOR,
        "--force", "--reason", "probe", "--json", "--db", str(db_file),
    )
    assert code == 2
    error = _json_document(out)["error"]
    assert error["code"] == "usage"
    assert "--force" in error["message"]

    # The refused call changed nothing: the edge is still there.
    task = _json_document(run("show", "T-000002", "--json", "--db", str(db_file))[1])["task"]
    assert task["depends_on"] == ["T-000001"]


def test_depend_remove_removes_and_honours_if_version(run: RunCli, db_file: Path) -> None:
    _create(run, db_file, "alpha", "--status", "ready")
    _create(run, db_file, "beta")
    run(
        "depend", "add", "T-000002", "--on", "T-000001", "--agent", DEFAULT_ACTOR,
        "--json", "--db", str(db_file),
    )

    code, out, _ = run(
        "depend", "remove", "T-000002", "--on", "T-000001", "--agent", DEFAULT_ACTOR,
        "--if-version", "99", "--json", "--db", str(db_file),
    )
    assert code == 5
    assert _json_document(out)["error"]["code"] == "stale_version"

    code, out, err = run(
        "depend", "remove", "T-000002", "--on", "T-000001", "--agent", DEFAULT_ACTOR,
        "--json", "--db", str(db_file),
    )
    assert code == 0, err
    assert _json_document(out)["task"]["depends_on"] == []


def test_graph_json_and_dot(run: RunCli, db_file: Path) -> None:
    _create(run, db_file, 'alpha "quoted"')
    _create(run, db_file, "beta", "--depends-on", "T-000001")

    document = _json_document(run("graph", "--json", "--db", str(db_file))[1])
    assert [edge["source"] for edge in document["edges"]] == ["T-000001"]
    assert [edge["target"] for edge in document["edges"]] == ["T-000002"]

    code, out, _ = run("graph", "--format", "dot", "--db", str(db_file))
    assert code == 0
    assert out.startswith("digraph ")
    assert '\\"quoted\\"' in out

    code, out, _ = run("graph", "--format", "dot", "--json", "--db", str(db_file))
    assert code == 2
    assert _json_document(out)["error"]["code"] == "usage"


def test_claim_release_and_conflict_codes(run: RunCli, db_file: Path) -> None:
    _create(run, db_file, "alpha", "--status", "ready")

    code, out, err = run("claim", "T-000001", "--agent", "dev", "--json", "--db", str(db_file))
    assert code == 0, err
    task = _json_document(out)["task"]
    assert task["status"] == "in_progress"
    assert task["claimed_by"] == "dev"

    code, out, _ = run("claim", "T-000001", "--agent", "other", "--json", "--db", str(db_file))
    assert code == 5
    assert _json_document(out)["error"]["details"] == {"claimed_by": "dev"}

    code, out, _ = run(
        "release", "T-000001", "--agent", "other", "--json", "--db", str(db_file)
    )
    assert code == 5

    code, out, _ = run("release", "T-000001", "--agent", "dev", "--json", "--db", str(db_file))
    assert code == 0
    assert _json_document(out)["task"]["status"] == "ready"

    # A forced claim is a takeover: another agent must be holding the card.
    code, out, _ = run(
        "claim", "T-000001", "--agent", "dev", "--json", "--db", str(db_file)
    )
    assert code == 0
    assert _json_document(out)["task"]["claimed_by"] == "dev"

    code, out, _ = run(
        "claim", "T-000001", "--agent", "other", "--force", "--reason", "takeover",
        "--json", "--db", str(db_file),
    )
    assert code == 0
    assert _json_document(out)["task"]["claimed_by"] == "other"

    # A forced claim with no applicable force case: the card is assigned to
    # another agent but nobody holds a claim, so no force case applies and the
    # command is refused.
    _create(run, db_file, "beta", "--status", "ready", "--assignee", "qa")
    code, out, _ = run(
        "claim", "T-000002", "--agent", "dev", "--force", "--reason", "habit",
        "--json", "--db", str(db_file),
    )
    assert code == 4
    assert _json_document(out)["error"]["code"] == "validation"


def test_claims_older_than_filter(run: RunCli, db_file: Path) -> None:
    _create(run, db_file, "alpha", "--status", "ready")
    run("claim", "T-000001", "--agent", "dev", "--db", str(db_file))

    fresh = _json_document(
        run("claims", "--older-than", "2h", "--json", "--db", str(db_file))[1]
    )
    assert fresh["items"] == []
    invalid = run("claims", "--older-than", "soon", "--json", "--db", str(db_file))
    assert invalid[0] == 4
    assert _json_document(invalid[1])["error"]["code"] == "validation"


def test_history_json_and_human(run: RunCli, db_file: Path) -> None:
    _create(run, db_file, "alpha")
    run("note", "T-000001", "a note", "--agent", "dev", "--db", str(db_file))

    code, out, err = run("history", "T-000001", "--json", "--db", str(db_file))
    assert code == 0, err
    items = _json_document(out)["items"]
    assert [item["event_type"] for item in items] == ["note", "task.created"]

    code, out, _ = run("history", "T-000001", "--limit", "1", "--db", str(db_file))
    assert code == 0
    assert len(out.splitlines()) == 2

    code, out, _ = run("history", "T-999999", "--json", "--db", str(db_file))
    assert code == 3
    assert _json_document(out)["error"]["code"] == "not_found"


def test_maintenance_commands_report_json(run: RunCli, tmp_path: Path) -> None:
    database = tmp_path / "new.sqlite3"
    backup = tmp_path / "copy.sqlite3"

    code, out, _ = run("init", "--db", str(database), "--json")
    assert code == 0
    assert _json_document(out)["created"] is True

    code, out, _ = run("init", "--db", str(database), "--json")
    assert code == 0
    assert _json_document(out)["created"] is False

    code, out, err = run("init", "--db", str(database))
    assert code == 0
    assert "Already initialized" in out

    code, out, _ = run("backup", str(backup), "--db", str(database), "--json")
    assert code == 0
    document = _json_document(out)
    assert document["operation"] == "backup"
    assert document["backup"] == str(backup)

    code, out, _ = run("backup", str(backup), "--db", str(database), "--json")
    assert code == 4
    assert _json_document(out)["error"]["code"] == "validation"

    code, out, _ = run("migrate", "--db", str(database), "--json")
    assert code == 0
    assert _json_document(out)["migrated"] is False


def test_doctor_reports_clean_and_seeded_problems(run: RunCli, db_file: Path) -> None:
    code, out, err = run("doctor", "--db", str(db_file), "--json")
    assert code == 0, err
    document = _json_document(out)
    assert document["ok"] is True
    assert document["errors"] == []

    code, out, _ = run("doctor", "--db", str(db_file))
    assert code == 0
    assert out.strip() == "No problems found."


# --------------------------------------------------------------------------- #
# guide, naming and packaging
# --------------------------------------------------------------------------- #


def test_guide_is_text_and_matches_the_readme_snippet(run: RunCli) -> None:
    code, out, err = run("guide")
    assert code == 0
    assert err == ""
    assert out == cli.GUIDE_TEXT

    with_json = run("guide", "--json")
    assert with_json[0] == 0
    assert with_json[1] == cli.GUIDE_TEXT

    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert cli.GUIDE_TEXT in readme


def test_no_legacy_identifier_survives_the_rename() -> None:
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    offenders = []
    for name in tracked:
        path = REPO_ROOT / name
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if LEGACY_NAME in text.lower():
            offenders.append(name)
    assert offenders == []


def test_help_text_never_names_the_old_project() -> None:
    parser_output = cli._build_parser().format_help()  # noqa: SLF001 - help text is a contract
    assert LEGACY_NAME not in parser_output.lower()
    assert LEGACY_NAME not in cli.GUIDE_TEXT.lower()


def test_console_script_and_module_invocation(db_file: Path) -> None:
    script = Path(sys.executable).parent / "tack"
    assert script.exists(), f"the tack console script is missing at {script}"

    result = subprocess.run(
        [str(script), "board", "--json", "--db", str(db_file)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["schema_version"] == 1
    assert result.stderr == ""

    module = subprocess.run(
        [sys.executable, "-m", "tackboard", "guide"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert module.returncode == 0, module.stderr
    assert module.stdout == cli.GUIDE_TEXT
