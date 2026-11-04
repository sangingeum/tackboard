"""Board lifecycle tests: ``board_info``, ``board_create`` and ``board_attach``.

These drive the adapter's lifecycle functions directly against the real CLI on
scratch boards. They need the MCP framework only for its ``ToolError`` type, so
they skip when it is absent, like the binding tests.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("mcp", reason="the optional MCP framework is not installed")

from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402
from tackboard_mcp import server  # noqa: E402
from tackboard_mcp.server import (  # noqa: E402
    BoardSession,
    board_attach,
    board_create,
    board_info,
)
from tackboard_mcp.tools import TackCli  # noqa: E402


def _cli(db: Path | None) -> TackCli:
    return TackCli(command=(sys.executable, "-m", "tackboard"), db=db, actor="lifecycle")


def _board(tmp_path: Path, name: str = "board.sqlite3") -> Path:
    path = tmp_path / name
    _cli(path).run("init")
    return path


def _session(db: Path | None, *, source: str = "env", allow_admin: bool = True) -> BoardSession:
    return BoardSession(cli=_cli(db), source=source, allow_admin=allow_admin)  # type: ignore[arg-type]


def _error_code(exc: ToolError) -> str:
    text = str(exc)
    for code in (
        "board_admin_disabled",
        "board_path_invalid",
        "not_found",
        "validation",
        "internal",
        "unreadable",
        "schema_version",
    ):
        if f'"code": "{code}"' in text:
            return code
    return ""


# --------------------------------------------------------------------------- #
# board_info
# --------------------------------------------------------------------------- #


def test_board_info_reports_a_configured_existing_board(tmp_path: Path) -> None:
    db = _board(tmp_path)
    info = board_info(_session(db))
    assert info["document"] == "board_info"
    assert info["schema_version"] == 1
    assert info["attached"] is True
    assert info["configured"] is True
    assert info["path"] == str(db.resolve())
    assert info["exists"] is True
    assert info["source"] == "env"
    assert info["project"] == "board"
    assert info["warnings"] == []


def test_board_info_warns_when_the_configured_file_is_missing(tmp_path: Path) -> None:
    missing = tmp_path / "gone.sqlite3"
    info = board_info(_session(missing))
    assert info["attached"] is False
    assert info["configured"] is True
    assert info["path"] == str(missing.resolve())
    assert info["exists"] is False
    assert info["source"] == "env"
    assert info["project"] is None
    assert any("does not exist" in warning for warning in info["warnings"])


def test_board_info_reports_a_discovered_board(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = _board(tmp_path)
    (tmp_path / ".tackboard.toml").write_text(
        '[tackboard]\ndb = "board.sqlite3"\n', encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    info = board_info(_session(None, source="discovery"))
    assert info["attached"] is True
    assert info["configured"] is False
    assert info["path"] == str(db.resolve())
    assert info["exists"] is True
    assert info["source"] == "discovery"
    assert info["project"] == "board"
    assert any("working directory" in warning for warning in info["warnings"])


def test_board_info_reports_no_board_at_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    info = board_info(_session(None, source="discovery"))
    assert info["attached"] is False
    assert info["configured"] is False
    assert info["path"] is None
    assert info["exists"] is None
    assert info["source"] == "none"
    assert info["project"] is None
    assert info["warnings"]


def test_board_info_reports_the_attached_board(tmp_path: Path) -> None:
    first = _board(tmp_path, "first.sqlite3")
    second = _board(tmp_path, "second.sqlite3")
    session = _session(first)
    board_attach(session, str(second))
    info = board_info(session)
    assert info["attached"] is True
    assert info["source"] == "attach"
    assert info["path"] == str(second.resolve())
    assert info["project"] == "second"


def test_board_info_warns_about_a_malformed_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".tackboard.toml").write_text("not = [valid\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    info = board_info(_session(None, source="discovery"))
    assert info["attached"] is False
    assert info["source"] == "none"
    assert any(
        warning.startswith("malformed .tackboard.toml:") for warning in info["warnings"]
    )


def test_board_info_warns_about_a_config_without_a_db_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".tackboard.toml").write_text("[tackboard]\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    info = board_info(_session(None, source="discovery"))
    assert info["attached"] is False
    assert info["source"] == "none"
    assert any(
        warning.startswith("no database configured in .tackboard.toml:")
        for warning in info["warnings"]
    )


def test_board_info_raises_for_a_corrupt_board(tmp_path: Path) -> None:
    foreign = tmp_path / "foreign.sqlite3"
    foreign.write_bytes(b"this is not a database")
    with pytest.raises(ToolError) as raised:
        board_info(_session(foreign))
    assert _error_code(raised.value) == "internal"


def test_board_info_warns_when_discovery_names_a_missing_board(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".tackboard.toml").write_text(
        '[tackboard]\ndb = "gone.sqlite3"\n', encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    info = board_info(_session(None, source="discovery"))
    assert info["attached"] is False
    assert info["source"] == "discovery"
    assert info["path"] is None
    assert info["exists"] is None
    assert any("not found" in warning for warning in info["warnings"])


# --------------------------------------------------------------------------- #
# board_create
# --------------------------------------------------------------------------- #


def test_board_create_is_disabled_by_default(tmp_path: Path) -> None:
    session = _session(_board(tmp_path), allow_admin=False)
    with pytest.raises(ToolError) as raised:
        board_create(session, str(tmp_path / "new.sqlite3"))
    assert _error_code(raised.value) == "board_admin_disabled"
    assert "--allow-board-admin" in str(raised.value)


def test_board_create_refuses_a_relative_path(tmp_path: Path) -> None:
    session = _session(_board(tmp_path))
    with pytest.raises(ToolError) as raised:
        board_create(session, "relative.sqlite3")
    assert _error_code(raised.value) == "board_path_invalid"
    assert '"path": "relative.sqlite3"' in str(raised.value)


def test_board_create_creates_a_board_without_attaching(tmp_path: Path) -> None:
    active = _board(tmp_path, "active.sqlite3")
    session = _session(active)
    target = tmp_path / "nested" / "new.sqlite3"
    document = board_create(session, str(target))
    assert document["created"] is True
    assert document["database"] == str(target)
    assert target.is_file()
    assert session.cli.db == active


def test_board_create_on_an_existing_board_reports_not_created(tmp_path: Path) -> None:
    session = _session(_board(tmp_path, "active.sqlite3"))
    target = _board(tmp_path, "existing.sqlite3")
    document = board_create(session, str(target))
    assert document["created"] is False


def test_board_create_raises_the_cli_document_for_a_non_board(tmp_path: Path) -> None:
    session = _session(_board(tmp_path, "active.sqlite3"))
    foreign = tmp_path / "foreign.sqlite3"
    foreign.write_bytes(b"this is not a database")
    with pytest.raises(ToolError) as raised:
        board_create(session, str(foreign))
    assert "error" in str(raised.value)


def test_board_create_creates_missing_parents(tmp_path: Path) -> None:
    session = _session(_board(tmp_path, "active.sqlite3"))
    target = tmp_path / "deep" / "deeper" / "board.sqlite3"
    board_create(session, str(target))
    assert target.is_file()


def test_admin_can_be_enabled_by_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert server._env_flag("1") is True
    assert server._env_flag("true") is True
    assert server._env_flag("YES") is True
    assert server._env_flag("0") is False
    assert server._env_flag("on") is False
    assert server._env_flag(None) is False

    captured: list[BoardSession] = []

    class _FakeServer:
        def run(self) -> None:
            return None

    def _capture(session: BoardSession) -> Any:
        captured.append(session)
        return _FakeServer()

    monkeypatch.setattr(server, "build_server", _capture)
    monkeypatch.setenv("TACKBOARD_MCP_ALLOW_ADMIN", "yes")
    monkeypatch.delenv("TACKBOARD_DB", raising=False)
    assert server.main([]) == 0
    assert captured[-1].allow_admin is True

    monkeypatch.setenv("TACKBOARD_MCP_ALLOW_ADMIN", "0")
    assert server.main([]) == 0
    assert captured[-1].allow_admin is False

    monkeypatch.delenv("TACKBOARD_MCP_ALLOW_ADMIN", raising=False)
    assert server.main(["--allow-board-admin"]) == 0
    assert captured[-1].allow_admin is True


# --------------------------------------------------------------------------- #
# board_attach
# --------------------------------------------------------------------------- #


def test_board_attach_is_disabled_by_default(tmp_path: Path) -> None:
    session = _session(_board(tmp_path), allow_admin=False)
    with pytest.raises(ToolError) as raised:
        board_attach(session, str(tmp_path / "board.sqlite3"))
    assert _error_code(raised.value) == "board_admin_disabled"


def test_board_attach_refuses_a_missing_path(tmp_path: Path) -> None:
    session = _session(_board(tmp_path, "active.sqlite3"))
    with pytest.raises(ToolError) as raised:
        board_attach(session, str(tmp_path / "absent.sqlite3"))
    assert _error_code(raised.value) == "board_path_invalid"


def test_board_attach_refuses_a_relative_path(tmp_path: Path) -> None:
    session = _session(_board(tmp_path, "active.sqlite3"))
    with pytest.raises(ToolError) as raised:
        board_attach(session, "board.sqlite3")
    assert _error_code(raised.value) == "board_path_invalid"


def test_board_attach_raises_for_a_non_board_file(tmp_path: Path) -> None:
    session = _session(_board(tmp_path, "active.sqlite3"))
    foreign = tmp_path / "foreign.sqlite3"
    foreign.write_bytes(b"this is not a database")
    with pytest.raises(ToolError) as raised:
        board_attach(session, str(foreign))
    assert "unreadable" in str(raised.value)


def test_board_attach_raises_for_an_incompatible_schema(tmp_path: Path) -> None:
    session = _session(_board(tmp_path, "active.sqlite3"))
    target = _board(tmp_path, "future.sqlite3")
    connection = sqlite3.connect(target)
    connection.execute("PRAGMA user_version = 99")
    connection.commit()
    connection.close()
    with pytest.raises(ToolError) as raised:
        board_attach(session, str(target))
    assert "schema version 99" in str(raised.value)


def test_board_attach_switches_the_session(tmp_path: Path) -> None:
    first = _board(tmp_path, "first.sqlite3")
    second = _board(tmp_path, "second.sqlite3")
    _cli(first).create("on the first board", status="ready")
    _cli(second).create("on the second board", status="ready")
    session = _session(first)

    response = board_attach(session, str(second))
    assert response["document"] == "board_attach"
    assert response["schema_version"] == 1
    assert response["attached"] is True
    assert response["board"] == {"path": str(second.resolve()), "project": "second"}
    assert response["warnings"] == []
    assert "schema_version" not in response["board"]

    assert session.cli.db == second
    assert session.source == "attach"
    titles = [task["title"] for task in session.cli.list_tasks()["tasks"]]
    assert titles == ["on the second board"]
    assert [task["title"] for task in _cli(first).list_tasks()["tasks"]] == [
        "on the first board"
    ]


def test_board_attach_to_the_active_path_is_a_no_op(tmp_path: Path) -> None:
    db = _board(tmp_path)
    session = _session(db)
    response = board_attach(session, str(db))
    assert response["attached"] is True
    assert session.cli.db == db
    assert session.source == "env"


def test_a_failed_attach_leaves_the_old_board_active(tmp_path: Path) -> None:
    active = _board(tmp_path, "active.sqlite3")
    session = _session(active)
    foreign = tmp_path / "foreign.sqlite3"
    foreign.write_bytes(b"this is not a database")
    with pytest.raises(ToolError):
        board_attach(session, str(foreign))
    assert session.cli.db == active
    assert session.source == "env"
    assert board_info(session)["attached"] is True


def test_attach_in_discovery_mode_flips_the_source(tmp_path: Path) -> None:
    target = _board(tmp_path, "discovered.sqlite3")
    session = _session(None, source="discovery")
    response = board_attach(session, str(target))
    assert response["attached"] is True
    assert session.cli.db == target
    assert session.source == "attach"
    assert board_info(session)["source"] == "attach"
