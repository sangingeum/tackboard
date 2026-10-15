"""Shared fixtures for the tackboard test suite."""

from __future__ import annotations

import sqlite3
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tackboard import claims, schema, service  # noqa: E402

DEFAULT_ACTOR = "dev"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """An initialized, empty project database."""
    path = tmp_path / "project.sqlite3"
    schema.init_database(path, label="project")
    return path


@pytest.fixture
def conn(db_path: Path) -> Iterator[sqlite3.Connection]:
    """An open connection to the fixture database."""
    connection = schema.open_database(db_path)
    yield connection
    connection.close()


@pytest.fixture
def make_task(conn: sqlite3.Connection) -> Callable[..., dict[str, Any]]:
    """Create a task in the fixture database and return its projection."""

    def _make_task(
        *, title: str = "Task", actor: str = DEFAULT_ACTOR, **kwargs: Any
    ) -> dict[str, Any]:
        kwargs.setdefault("status", "backlog")
        return service.create_task(conn, actor=actor, title=title, **kwargs)["task"]

    return _make_task


@pytest.fixture
def make_done(conn: sqlite3.Connection) -> Callable[..., dict[str, Any]]:
    """Create a task and drive it to ``done`` (claim, then complete)."""

    def _make_done(
        *, title: str = "Done task", actor: str = DEFAULT_ACTOR, **kwargs: Any
    ) -> dict[str, Any]:
        kwargs["status"] = "ready"
        task = service.create_task(conn, actor=actor, title=title, **kwargs)["task"]
        claims.claim(conn, task["id"], actor=actor)
        service.complete_task(conn, task["id"], actor=actor)
        return service.get_task(conn, task["id"])

    return _make_done
