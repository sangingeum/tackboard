"""Connection handling, transactions, and the busy/retry policy (spec 8.3)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tackboard import db
from tackboard.errors import LockedError


def test_connect_enables_foreign_keys_and_wal(db_path: Path) -> None:
    conn = db.connect(db_path)
    try:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] > 0
    finally:
        conn.close()


def test_read_only_connection_refuses_writes(db_path: Path) -> None:
    conn = db.connect(db_path, read_only=True)
    try:
        assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO metadata(key, value) VALUES('x', '1')")
    finally:
        conn.close()


def test_write_transaction_commits(conn: sqlite3.Connection) -> None:
    with db.write_transaction(conn):
        conn.execute("INSERT INTO metadata(key, value) VALUES('temp', '1')")
    assert conn.execute("SELECT COUNT(*) FROM metadata WHERE key = 'temp'").fetchone()[0] == 1


def test_write_transaction_rolls_back_on_error(conn: sqlite3.Connection) -> None:
    with pytest.raises(RuntimeError), db.write_transaction(conn):
        conn.execute("INSERT INTO metadata(key, value) VALUES('temp', '1')")
        raise RuntimeError("boom")
    assert conn.execute("SELECT COUNT(*) FROM metadata WHERE key = 'temp'").fetchone()[0] == 0
    assert not conn.in_transaction


def test_nested_write_transaction_joins_the_outer_one(conn: sqlite3.Connection) -> None:
    with db.write_transaction(conn), db.write_transaction(conn):
        conn.execute("INSERT INTO metadata(key, value) VALUES('nested', '1')")
    assert conn.execute("SELECT COUNT(*) FROM metadata WHERE key = 'nested'").fetchone()[0] == 1
    assert not conn.in_transaction


def test_read_transaction_leaves_no_open_transaction(conn: sqlite3.Connection) -> None:
    with db.read_transaction(conn):
        assert conn.in_transaction
    assert not conn.in_transaction


def test_lock_contention_reports_a_retryable_lock_error(db_path: Path) -> None:
    holder = db.connect(db_path)
    contender = db.connect(db_path, busy_timeout=0.05)
    try:
        holder.execute("BEGIN IMMEDIATE")
        with pytest.raises(LockedError) as caught, db.write_transaction(contender):
            pass
        assert caught.value.retryable is True
        assert caught.value.exit_code == 5
        holder.execute("ROLLBACK")
        # The lock is released again, so the same call now succeeds.
        with db.write_transaction(contender):
            contender.execute("INSERT INTO metadata(key, value) VALUES('after', '1')")
    finally:
        contender.close()
        holder.close()
