"""SQLite connection handling, transactions, and the busy/retry policy.

Spec: section 8.3 (SQLite concurrency), section 13 (Python requirements).
Connections use ``isolation_level=None`` so that ``BEGIN IMMEDIATE`` is
actually issued; Python's implicit transaction handling never is.
"""

from __future__ import annotations

import contextlib
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .errors import LockedError

DEFAULT_BUSY_TIMEOUT = 5.0
BUSY_RETRIES = 5
BUSY_BACKOFF_SECONDS = 0.05

_LOCKED_MESSAGE = "Database is locked after retries; retry the command."


def _is_busy(exc: sqlite3.Error) -> bool:
    text = str(exc).lower()
    return "locked" in text or "busy" in text


def connect(
    path: Path | str,
    *,
    read_only: bool = False,
    busy_timeout: float | None = None,
) -> sqlite3.Connection:
    """Open a connection with foreign keys, a busy timeout, and WAL.

    ``read_only=True`` uses ``PRAGMA query_only=ON`` and never opens the
    database for writing (spec 11.3 explains why not ``mode=ro``).
    """
    timeout = DEFAULT_BUSY_TIMEOUT if busy_timeout is None else busy_timeout
    conn = sqlite3.connect(str(path), isolation_level=None, timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(f"PRAGMA busy_timeout={int(timeout * 1000)}")
    if read_only:
        conn.execute("PRAGMA query_only=ON")
    else:
        conn.execute("PRAGMA journal_mode=WAL")
    return conn


@contextmanager
def write_transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run the block in a ``BEGIN IMMEDIATE`` transaction.

    A nested call inside an already-open transaction joins the outer one, which
    is what makes "select-then-mutate" atomic (spec 10.4). ``SQLITE_BUSY`` is
    retried a bounded number of times, then reported as a retryable lock error.
    """
    if conn.in_transaction:
        yield conn
        return

    attempts = 0
    while True:
        try:
            conn.execute("BEGIN IMMEDIATE")
            break
        except sqlite3.OperationalError as exc:
            if not _is_busy(exc) or attempts >= BUSY_RETRIES:
                raise LockedError(_LOCKED_MESSAGE) from exc
            attempts += 1
            time.sleep(BUSY_BACKOFF_SECONDS * attempts)

    try:
        yield conn
    except BaseException:
        with contextlib.suppress(sqlite3.Error):
            conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


@contextmanager
def read_transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run the block in a deferred read transaction (a consistent snapshot)."""
    if conn.in_transaction:
        yield conn
        return
    conn.execute("BEGIN")
    try:
        yield conn
    except BaseException:
        with contextlib.suppress(sqlite3.Error):
            conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


__all__ = [
    "BUSY_RETRIES",
    "DEFAULT_BUSY_TIMEOUT",
    "connect",
    "read_transaction",
    "write_transaction",
]
