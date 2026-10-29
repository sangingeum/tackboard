"""Every released schema version migrates and then reports a clean doctor."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from tackboard import schema

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_legacy_board.py"


@pytest.mark.parametrize("version", [1, 2])
def test_a_released_board_migrates_and_stays_clean(tmp_path: Path, version: int) -> None:
    path = tmp_path / f"legacy-v{version}.sqlite3"
    subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--version",
            str(version),
            "--db",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    schema.migrate_database(path)

    report = schema.doctor(path)
    assert report["ok"] is True, report["errors"]
    assert report["warnings"] == [], report["warnings"]

    conn = sqlite3.connect(str(path))
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == schema.SCHEMA_VERSION
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 3
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type = 'table' AND name = 'memo_revisions'"
            ).fetchone()[0]
            == 1
        )
    finally:
        conn.close()
