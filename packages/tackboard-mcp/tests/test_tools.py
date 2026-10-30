"""Tests for the adapter's tool layer.

Two halves. The first drives every tool through a fake transport and asserts the
argv it builds — the mapping from tool arguments to CLI options, and the fact
that the CLI's document comes back untouched. The second runs a real scratch
board through the actual CLI subprocess, which proves the plumbing end to end.

What is deliberately NOT tested here: the MCP handshake. That needs the
framework and a client session, and the report says so plainly.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from tackboard_mcp.tools import TackCli, TackCommandError

Document = dict[str, Any]


class FakeTransport:
    """Records each argv and replies with a canned document."""

    def __init__(self, document: Document | None = None, returncode: int = 0) -> None:
        self.document = document or {"schema_version": 1, "ok": True}
        self.returncode = returncode
        self.calls: list[tuple[list[str], str | None]] = []
        self.actor_seen: list[str | None] = []

    def __call__(self, argv: Sequence[str], stdin: str | None) -> tuple[int, str, str]:
        self.calls.append((list(argv), stdin))
        self.actor_seen.append(os.environ.get("TACKBOARD_AGENT"))
        return self.returncode, json.dumps(self.document), ""

    @property
    def last_argv(self) -> list[str]:
        return self.calls[-1][0]


@pytest.fixture
def transport() -> FakeTransport:
    return FakeTransport()


@pytest.fixture
def cli(transport: FakeTransport, tmp_path: Path) -> TackCli:
    return TackCli(
        command=("tack",),
        db=tmp_path / "board.sqlite3",
        actor="worker",
        runner=transport,
    )


def test_the_tool_returns_the_cli_document_unchanged(
    cli: TackCli, transport: FakeTransport
) -> None:
    transport.document = {"schema_version": 1, "task": {"id": "T-000001"}}
    assert cli.next_claim() == transport.document


def test_every_call_carries_json_db_and_actor(cli: TackCli, transport: FakeTransport) -> None:
    cli.show("T-000001")
    argv = transport.last_argv
    assert argv[:3] == ["tack", "show", "T-000001"]
    assert argv[-3:] == ["--json", "--db", str(cli.db)]
    assert transport.actor_seen == ["worker"]


def test_the_actor_is_restored_after_the_call(cli: TackCli) -> None:
    before = os.environ.get("TACKBOARD_AGENT")
    cli.board_brief()
    assert os.environ.get("TACKBOARD_AGENT") == before


def test_next_claim_and_review_stage(cli: TackCli, transport: FakeTransport) -> None:
    cli.next_claim()
    assert transport.last_argv[:3] == ["tack", "next", "--claim"]
    cli.next_claim("review")
    assert transport.last_argv[:5] == ["tack", "next", "--claim", "--stage", "review"]


def test_note_passes_every_ref(cli: TackCli, transport: FakeTransport) -> None:
    cli.note("T-000002", "done", ["commit:1a2b3c4", "path:src/parser.py"])
    assert transport.last_argv[:4] == ["tack", "note", "T-000002", "done"]
    assert transport.last_argv[4:8] == [
        "--ref",
        "commit:1a2b3c4",
        "--ref",
        "path:src/parser.py",
    ]


def test_create_maps_every_option(cli: TackCli, transport: FakeTransport) -> None:
    cli.create(
        "Wire the CLI",
        description="details",
        priority="high",
        assignee="worker",
        labels=["core"],
        depends_on=["T-000001"],
        status="ready",
    )
    argv = transport.last_argv
    assert argv[:3] == ["tack", "create", "Wire the CLI"]
    for flag, value in (
        ("--description", "details"),
        ("--priority", "high"),
        ("--assignee", "worker"),
        ("--label", "core"),
        ("--depends-on", "T-000001"),
        ("--status", "ready"),
    ):
        assert argv[argv.index(flag) + 1] == value


def test_release_forces_only_when_a_reason_is_given(
    cli: TackCli, transport: FakeTransport
) -> None:
    cli.release("T-000003")
    assert "--force" not in transport.last_argv
    cli.release("T-000003", "dead agent")
    argv = transport.last_argv
    assert "--force" in argv and argv[argv.index("--reason") + 1] == "dead agent"


def test_board_brief_asks_for_counts_only(cli: TackCli, transport: FakeTransport) -> None:
    cli.board_brief()
    assert transport.last_argv[:4] == ["tack", "board", "--brief", "--json"]


def test_a_failure_carries_the_cli_error_document(
    cli: TackCli, transport: FakeTransport
) -> None:
    error = {
        "schema_version": 1,
        "error": {"code": "claim_conflict", "exit_code": 5, "message": "held"},
    }
    transport.document = error
    transport.returncode = 5
    with pytest.raises(TackCommandError) as raised:
        cli.next_claim()
    assert raised.value.returncode == 5
    assert raised.value.document == error


def test_from_environment_reads_the_documented_variables(tmp_path: Path) -> None:
    transport = FakeTransport()
    cli = TackCli.from_environment(
        {
            "TACK_CMD": f"{sys.executable} -m tackboard",
            "TACKBOARD_DB": str(tmp_path / "b.sqlite3"),
            "TACKBOARD_AGENT": "reader",
        },
        runner=transport,
    )
    assert cli.command == (sys.executable, "-m", "tackboard")
    assert cli.actor == "reader"
    assert cli.db == tmp_path / "b.sqlite3"


def test_the_default_command_prefers_the_installed_module() -> None:
    # The adapter depends on ``tackboard``, so in a normal install the core is
    # importable and the module form avoids relying on ``tack`` being on PATH.
    assert TackCli.from_environment({}).command == (sys.executable, "-m", "tackboard")


def test_the_default_command_falls_back_to_the_console_script(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An adapter installed into a different environment than the CLI cannot
    # import ``tackboard``; the historical ``tack`` lookup is the fallback.
    monkeypatch.setattr("tackboard_mcp.tools.find_spec", lambda name: None)
    assert TackCli.from_environment({}).command == ("tack",)


def test_memo_read_and_revision(cli: TackCli, transport: FakeTransport) -> None:
    cli.memo_read()
    assert transport.last_argv[:2] == ["tack", "memo"]
    cli.memo_read(3)
    assert transport.last_argv[:4] == ["tack", "memo", "--rev", "3"]


def test_list_tasks_maps_every_filter(cli: TackCli, transport: FakeTransport) -> None:
    cli.list_tasks(
        status="ready",
        assignee="worker",
        label="core",
        priority="high",
        blocked=True,
        eligible=True,
        limit=5,
        full=True,
    )
    argv = transport.last_argv
    assert argv[:2] == ["tack", "list"]
    for flag, value in (
        ("--status", "ready"),
        ("--assignee", "worker"),
        ("--label", "core"),
        ("--priority", "high"),
        ("--limit", "5"),
    ):
        assert argv[argv.index(flag) + 1] == value
    assert "--blocked" in argv and "--eligible" in argv and "--full" in argv


def test_claim_plain_and_forced(cli: TackCli, transport: FakeTransport) -> None:
    cli.claim("T-000001")
    assert transport.last_argv[:3] == ["tack", "claim", "T-000001"]
    assert "--force" not in transport.last_argv

    cli.claim("T-000001", force=True, reason="dead agent")
    argv = transport.last_argv
    assert "--force" in argv and argv[argv.index("--reason") + 1] == "dead agent"

    with pytest.raises(ValueError, match="non-empty reason"):
        cli.claim("T-000001", force=True)
    with pytest.raises(ValueError, match="requires force"):
        cli.claim("T-000001", reason="unused")


def test_reject_maps_reason_and_version(cli: TackCli, transport: FakeTransport) -> None:
    cli.reject("T-000001", "needs tests", 4)
    argv = transport.last_argv
    assert argv[:5] == ["tack", "reject", "T-000001", "--reason", "needs tests"]
    assert argv[argv.index("--if-version") + 1] == "4"


def test_status_requires_a_force_reason(cli: TackCli, transport: FakeTransport) -> None:
    cli.status("T-000001", "review")
    assert transport.last_argv[:4] == ["tack", "status", "T-000001", "review"]
    with pytest.raises(ValueError, match="non-empty reason"):
        cli.status("T-000001", "done", force=True)
    with pytest.raises(ValueError, match="requires force"):
        cli.status("T-000001", "done", reason="unused")


# --------------------------------------------------------------------------- #
# the real CLI, on a scratch board
# --------------------------------------------------------------------------- #


@pytest.fixture
def live_cli(tmp_path: Path) -> TackCli:
    """A tool layer wired to the real CLI on a throwaway board."""
    return TackCli(
        command=(sys.executable, "-m", "tackboard"),
        db=tmp_path / "scratch.sqlite3",
        actor="adapter-test",
    )


def test_a_whole_work_unit_through_the_real_cli(live_cli: TackCli) -> None:
    live_cli.run("init")
    created = live_cli.create("Adapter round trip", status="ready", labels=["core"])
    task_id = created["task"]["id"]

    brief = live_cli.board_brief()
    assert brief["counts"]["tasks"] == 1
    assert brief["counts"]["eligible"] == 1

    claimed = live_cli.next_claim()
    assert claimed["task"]["id"] == task_id
    assert claimed["task"]["claimed_by"] == "adapter-test"

    noted = live_cli.note(task_id, "worked", ["path:src/example.py"])
    assert noted["event"]["payload"]["refs"][0] == {
        "kind": "path",
        "value": "src/example.py",
    }

    finished = live_cli.complete(task_id)
    assert finished["changed"] is True
    assert finished["task"]["status"] == "done"
    assert live_cli.show(task_id)["task"]["status"] == "done"


def test_a_second_agent_gets_the_conflict_document(live_cli: TackCli, tmp_path: Path) -> None:
    live_cli.run("init")
    task_id = live_cli.create("Contended", status="ready")["task"]["id"]
    live_cli.next_claim()

    other = TackCli(
        command=(sys.executable, "-m", "tackboard"),
        db=live_cli.db,
        actor="second-agent",
    )
    # `next --claim` would honestly answer with an empty task; the conflict is
    # what an explicit claim on a held task returns.
    with pytest.raises(TackCommandError) as raised:
        other.run("claim", task_id)
    assert raised.value.returncode == 5
    assert raised.value.document is not None
    assert raised.value.document["error"]["code"] == "claim_conflict"


def test_the_cli_itself_reports_a_missing_board(live_cli: TackCli) -> None:
    with pytest.raises(TackCommandError) as raised:
        live_cli.board_brief()
    assert raised.value.returncode == 3


def test_the_review_loop_with_reject_and_handback(tmp_path: Path) -> None:
    """An MCP-only agent can read the memo, work, submit, be rejected and resume."""
    author = TackCli(
        command=(sys.executable, "-m", "tackboard"),
        db=tmp_path / "loop.sqlite3",
        actor="author",
    )
    author.run("init")
    assert author.memo_read()["rev"] == 0

    task_id = author.create("Review loop", status="ready")["task"]["id"]
    assert author.claim(task_id)["task"]["claimed_by"] == "author"
    author.note(task_id, "ready for review")
    assert author.status(task_id, "review")["task"]["status"] == "review"

    reviewer = TackCli(
        command=(sys.executable, "-m", "tackboard"),
        db=author.db,
        actor="reviewer",
    )
    taken = reviewer.next_claim(stage="review")
    assert taken["task"]["id"] == task_id
    rejected = reviewer.reject(task_id, "needs tests")
    assert rejected["task"]["status"] == "in_progress"
    assert rejected["task"]["claimed_by"] == "author"

    author.note(task_id, "tests added")
    assert author.complete(task_id)["task"]["status"] == "done"
    assert [t["id"] for t in author.list_tasks(status="done")["tasks"]] == [task_id]


def test_the_adapter_adds_no_core_dependency() -> None:
    """The core package stays dependency-free; the adapter is a separate package."""
    root = Path(__file__).resolve().parents[3]
    core = (root / "pyproject.toml").read_text(encoding="utf-8")
    assert "dependencies = []" in core
    adapter = (root / "packages" / "tackboard-mcp" / "pyproject.toml").read_text(
        encoding="utf-8"
    )
    assert 'dependencies = ["tackboard' in adapter
    assert '"mcp' in adapter


def test_running_the_module_without_the_framework_gives_a_clear_error(
    tmp_path: Path,
) -> None:
    """Importing the tool layer never needs MCP; the server module names the gap."""
    result = subprocess.run(
        [sys.executable, "-c", "import tackboard_mcp, tackboard_mcp.tools"],
        capture_output=True,
        text=True,
        check=False,
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
    )
    assert result.returncode == 0, result.stderr
