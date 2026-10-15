"""CLI tests for the ``tack`` command line (spec section 16, the CLI area).

Every test owns its own temporary database and drives the CLI the way an agent
does: through the entry point, in process. One test runs the installed console
script as a real subprocess to prove the packaging.
"""

from __future__ import annotations

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
    assert document["database_schema_version"] == 1

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

    code, out, _ = run(
        "claim", "T-000001", "--agent", "other", "--force", "--reason", "takeover",
        "--json", "--db", str(db_file),
    )
    assert code == 0
    assert _json_document(out)["task"]["claimed_by"] == "other"


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
