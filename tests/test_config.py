"""Project/database resolution (spec section 5) — test area 16."""

from __future__ import annotations

from pathlib import Path

import pytest

from tackboard.config import (
    CONFIG_FILENAME,
    CONFIG_TABLE,
    ENV_AGENT,
    ENV_DB,
    find_config_file,
    load_config,
    resolve_actor,
    resolve_database,
    validate_agent_id,
)
from tackboard.errors import NotFoundError, UsageError, ValidationError

CONFIG_BODY = f"[{CONFIG_TABLE}]\ndb = \"from-config.sqlite3\"\n"


def test_explicit_database_wins(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILENAME).write_text(CONFIG_BODY)
    resolved = resolve_database(
        "explicit.sqlite3", cwd=tmp_path, env={ENV_DB: "from-env.sqlite3"}
    )
    assert resolved == (tmp_path / "explicit.sqlite3").resolve()


def test_environment_database_beats_config(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILENAME).write_text(CONFIG_BODY)
    resolved = resolve_database(None, cwd=tmp_path, env={ENV_DB: "from-env.sqlite3"})
    assert resolved == (tmp_path / "from-env.sqlite3").resolve()


def test_config_is_used_when_nothing_else_is_set(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILENAME).write_text(CONFIG_BODY)
    resolved = resolve_database(None, cwd=tmp_path, env={})
    assert resolved == (tmp_path / "from-config.sqlite3").resolve()


def test_config_is_discovered_in_a_parent_directory(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILENAME).write_text(CONFIG_BODY)
    deep = tmp_path / "src" / "package"
    deep.mkdir(parents=True)
    assert find_config_file(deep) == tmp_path / CONFIG_FILENAME
    assert resolve_database(None, cwd=deep, env={}) == (
        tmp_path / "from-config.sqlite3"
    ).resolve()


def test_config_paths_resolve_against_the_config_directory(tmp_path: Path) -> None:
    board = tmp_path / "board"
    board.mkdir()
    (board / CONFIG_FILENAME).write_text(f"[{CONFIG_TABLE}]\ndb = \"data/board.sqlite3\"\n")
    deep = board / "src" / "package"
    deep.mkdir(parents=True)
    assert resolve_database(None, cwd=deep, env={}) == (
        board / "data" / "board.sqlite3"
    ).resolve()


def test_missing_configuration_fails_without_creating_anything(tmp_path: Path) -> None:
    with pytest.raises(NotFoundError) as caught:
        resolve_database(None, cwd=tmp_path, env={})
    assert caught.value.exit_code == 3
    assert ENV_DB in str(caught.value)
    assert list(tmp_path.iterdir()) == []


def test_config_without_db_key_is_an_actionable_error(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILENAME).write_text("[other]\nx = 1\n")
    with pytest.raises(NotFoundError) as caught:
        resolve_database(None, cwd=tmp_path, env={})
    assert "No database configured" in str(caught.value)


def test_malformed_config_is_a_validation_error(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILENAME).write_text("this is not toml =")
    with pytest.raises(ValidationError):
        resolve_database(None, cwd=tmp_path, env={})


def test_find_config_file_returns_none_when_absent(tmp_path: Path) -> None:
    assert find_config_file(tmp_path) is None


def test_load_config_parses_the_tackboard_table(tmp_path: Path) -> None:
    path = tmp_path / CONFIG_FILENAME
    path.write_text(CONFIG_BODY)
    assert load_config(path)[CONFIG_TABLE]["db"] == "from-config.sqlite3"


def test_actor_prefers_the_explicit_option(tmp_path: Path) -> None:
    assert resolve_actor("explicit-agent", env={ENV_AGENT: "env-agent"}) == "explicit-agent"


def test_actor_falls_back_to_the_environment() -> None:
    assert resolve_actor(None, env={ENV_AGENT: "env-agent"}) == "env-agent"


def test_missing_actor_is_a_usage_error() -> None:
    with pytest.raises(UsageError) as caught:
        resolve_actor(None, env={})
    assert caught.value.exit_code == 2
    assert ENV_AGENT in str(caught.value)


@pytest.mark.parametrize("bad", ["has space", "semi;colon", "", "a" * 65, "emoji🙂"])
def test_invalid_agent_identifiers_are_rejected(bad: str) -> None:
    with pytest.raises(UsageError):
        validate_agent_id(bad)


def test_agent_identifier_boundaries_are_accepted() -> None:
    assert validate_agent_id("a") == "a"
    assert validate_agent_id("a" * 64) == "a" * 64
    assert validate_agent_id("qa.bot:7_1-2") == "qa.bot:7_1-2"
