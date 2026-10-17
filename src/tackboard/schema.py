"""Database identity, DDL, migrations, backup, and diagnostics.

Spec: section 5.4 (initialization and safety), section 12 (schema),
section 14 (``doctor``, ``backup``). Maintenance operations are not task
mutations and never write task events.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import db
from .errors import NotFoundError, SchemaError, TackError, ValidationError
from .models import MAX_MEMO_BYTES, utcnow_iso, utcstamp_iso

#: A fixed magic number identifying a tackboard database (spec 5.4).
APPLICATION_ID = 0x5441434B  # "TACK"

#: The schema version this build supports (``PRAGMA user_version``).
#:
#: Independent of the release version: ``user_version`` moves to 2 for the
#: board-level memo's revision table, while the JSON document contract stays
#: ``schema_version: 1``.
SCHEMA_VERSION = 2

METADATA_REQUIRED_KEYS = ("project_label", "created_at", "next_sequence")

#: The board-level memo's revision table and its append-only triggers
#: (migration 1 -> 2). A memo revision is immutable once written — ``--restore``
#: and ``--clear`` add a revision rather than editing or deleting one — so the
#: table carries the same append-only enforcement as ``events``. It has no
#: foreign keys: a memo is board-scoped, not attached to any task.
_MEMO_DDL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS memo_revisions (
        rev INTEGER PRIMARY KEY,
        text TEXT NOT NULL,
        author TEXT NOT NULL,
        created_at TEXT NOT NULL,
        reason TEXT NULL
    )
    """,
    """
    CREATE TRIGGER IF NOT EXISTS memo_revisions_no_update
    BEFORE UPDATE ON memo_revisions
    BEGIN
        SELECT RAISE(ABORT, 'memo_revisions is append-only');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS memo_revisions_no_delete
    BEFORE DELETE ON memo_revisions
    BEGIN
        SELECT RAISE(ABORT, 'memo_revisions is append-only');
    END
    """,
)

_DDL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS metadata (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY,
        sequence INTEGER UNIQUE NOT NULL,
        title TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL CHECK (
            status IN ('backlog', 'ready', 'in_progress', 'review', 'done', 'cancelled')
        ),
        priority TEXT NOT NULL CHECK (
            priority IN ('low', 'normal', 'high', 'urgent')
        ),
        assignee TEXT NULL,
        blocked_reason TEXT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        completed_at TEXT NULL,
        version INTEGER NOT NULL DEFAULT 1
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)",
    "CREATE INDEX IF NOT EXISTS idx_tasks_priority ON tasks(priority)",
    "CREATE INDEX IF NOT EXISTS idx_tasks_assignee ON tasks(assignee)",
    "CREATE INDEX IF NOT EXISTS idx_tasks_updated_at ON tasks(updated_at)",
    """
    CREATE TABLE IF NOT EXISTS task_labels (
        task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
        label TEXT NOT NULL,
        label_key TEXT NOT NULL,
        PRIMARY KEY (task_id, label_key)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_task_labels_key ON task_labels(label_key)",
    """
    CREATE TABLE IF NOT EXISTS dependencies (
        prerequisite_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE RESTRICT,
        task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE RESTRICT,
        PRIMARY KEY (prerequisite_id, task_id),
        CHECK (prerequisite_id <> task_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_dependencies_task ON dependencies(task_id)",
    """
    CREATE TABLE IF NOT EXISTS claims (
        task_id TEXT PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
        agent_id TEXT NOT NULL,
        claimed_at TEXT NOT NULL,
        lease_expires_at TEXT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        actor TEXT NOT NULL,
        event_type TEXT NOT NULL,
        task_id TEXT NULL,
        payload_json TEXT NOT NULL DEFAULT '{}'
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_events_task ON events(task_id, id)",
    "CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp)",
    # The events table is append-only (spec 9).
    """
    CREATE TRIGGER IF NOT EXISTS events_no_update
    BEFORE UPDATE ON events
    BEGIN
        SELECT RAISE(ABORT, 'events is append-only');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS events_no_delete
    BEFORE DELETE ON events
    BEGIN
        SELECT RAISE(ABORT, 'events is append-only');
    END
    """,
    *_MEMO_DDL,
)


def _read_application_id(conn: sqlite3.Connection) -> int:
    row = conn.execute("PRAGMA application_id").fetchone()
    return int(row[0])


def _read_user_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("PRAGMA user_version").fetchone()
    return int(row[0])


def _apply_schema(conn: sqlite3.Connection, *, label: str) -> None:
    """Create the schema and metadata rows; idempotent (migration 0 -> 1)."""
    for statement in _DDL:
        conn.execute(statement)
    conn.execute(f"PRAGMA application_id={APPLICATION_ID}")
    conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    row = conn.execute("SELECT COALESCE(MAX(sequence), 0) AS max_seq FROM tasks").fetchone()
    default_next = int(row["max_seq"]) + 1
    defaults = {
        "project_label": label,
        "created_at": utcnow_iso(),
        "next_sequence": str(default_next),
    }
    for key, value in defaults.items():
        conn.execute(
            "INSERT INTO metadata(key, value) VALUES(?, ?) ON CONFLICT(key) DO NOTHING",
            (key, value),
        )


def _require_known_application_id(conn: sqlite3.Connection, path: Path) -> None:
    try:
        application_id = _read_application_id(conn)
    except sqlite3.DatabaseError as exc:
        raise SchemaError(
            f"Cannot use {path.name}: the file is not a SQLite database."
        ) from exc
    if application_id != APPLICATION_ID:
        raise SchemaError(
            f"{path.name} is not a tackboard database "
            f"(application_id {application_id} != {APPLICATION_ID})."
        )


def _require_supported_version(conn: sqlite3.Connection, path: Path) -> int:
    version = _read_user_version(conn)
    if version < SCHEMA_VERSION:
        raise SchemaError(
            f"{path.name} uses schema version {version}, older than the supported "
            f"version {SCHEMA_VERSION}. Run 'tack migrate'."
        )
    if version > SCHEMA_VERSION:
        raise SchemaError(
            f"{path.name} uses schema version {version}, newer than this build "
            f"supports ({SCHEMA_VERSION}). Upgrade tackboard before opening it."
        )
    return version


def init_database(path: Path, *, label: str | None = None) -> dict[str, Any]:
    """Create and initialize a database, or accept an already-initialized one."""
    path = Path(path)
    project_label = label or path.stem
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists() and path.stat().st_size > 0:
        conn = sqlite3.connect(str(path), isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            _require_known_application_id(conn, path)
            _require_supported_version(conn, path)
            with db.write_transaction(conn):
                _apply_schema(conn, label=project_label)
        finally:
            conn.close()
        return {
            "schema_version": 1,
            "ok": True,
            "database": str(path),
            "operation": "init",
            "created": False,
            "project_label": project_label,
            "database_schema_version": SCHEMA_VERSION,
        }

    conn = db.connect(path)
    try:
        with db.write_transaction(conn):
            _apply_schema(conn, label=project_label)
    finally:
        conn.close()
    return {
        "schema_version": 1,
        "ok": True,
        "database": str(path),
        "operation": "init",
        "created": True,
        "project_label": project_label,
        "database_schema_version": SCHEMA_VERSION,
    }


def open_database(path: Path, *, read_only: bool = False) -> sqlite3.Connection:
    """Open a database for the supported schema version.

    A missing file is a ``NotFoundError``: no command except ``init`` creates a
    database (spec 5.2).
    """
    path = Path(path)
    if not path.exists():
        raise NotFoundError(
            f"Database {path} not found. Run 'tack init --db {path}' to create it."
        )
    conn = db.connect(path, read_only=read_only)
    try:
        _require_known_application_id(conn, path)
        _require_supported_version(conn, path)
    except TackError:
        conn.close()
        raise
    return conn


def backup_database(source: Path, dest: Path) -> dict[str, Any]:
    """Write a consistent snapshot of ``source`` to ``dest`` (spec 14).

    Uses SQLite's online backup API — never ``cp`` a live database.
    """
    source = Path(source)
    dest = Path(dest)
    if not source.exists():
        raise NotFoundError(f"Database {source} not found.")
    if dest.exists():
        raise ValidationError(f"Refusing to overwrite existing file {dest}.")
    if not dest.parent.exists():
        raise NotFoundError(
            f"Destination directory {dest.parent} does not exist. "
            "Only 'tack init' creates parent directories."
        )

    src_conn = db.connect(source, read_only=True)
    try:
        application_id = _read_application_id(src_conn)
        target = sqlite3.connect(str(dest))
        try:
            src_conn.backup(target)
        finally:
            target.close()
    finally:
        src_conn.close()

    conn = sqlite3.connect(str(dest), isolation_level=None)
    try:
        if _read_application_id(conn) != APPLICATION_ID:
            raise SchemaError(f"Backup of {source} is not a tackboard database.")
        check = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if check != "ok":
            raise SchemaError(f"Backup of {source} failed integrity check: {check}.")
        version = _read_user_version(conn)
    finally:
        conn.close()
    return {
        "schema_version": 1,
        "ok": True,
        "database": str(source),
        "operation": "backup",
        "backup": str(dest),
        "source_application_id": application_id,
        "database_schema_version": version,
    }


def _migrate_to_v2(conn: sqlite3.Connection) -> None:
    """Migration 1 -> 2: add the board-level memo's revision table."""
    for statement in _MEMO_DDL:
        conn.execute(statement)


#: Migration steps keyed by the version they upgrade *from*.
MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {
    0: lambda conn: _apply_schema(conn, label="project"),
    1: _migrate_to_v2,
}


def migrate_database(path: Path) -> dict[str, Any]:
    """Back up, then migrate an older database in one transaction (spec 5.4)."""
    path = Path(path)
    if not path.exists():
        raise NotFoundError(f"Database {path} not found.")

    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        _require_known_application_id(conn, path)
        version = _read_user_version(conn)
        if version > SCHEMA_VERSION:
            raise SchemaError(
                f"{path.name} uses schema version {version}, newer than this build "
                f"supports ({SCHEMA_VERSION})."
            )
        if version == SCHEMA_VERSION:
            return {
                "schema_version": 1,
                "ok": True,
                "database": str(path),
                "operation": "migrate",
                "migrated": False,
                "from_version": version,
                "to_version": version,
                "database_schema_version": version,
            }
    finally:
        conn.close()

    stamp = utcstamp_iso()
    backup_path = path.with_name(
        f"{path.name}.pre-migrate-v{version}-to-v{SCHEMA_VERSION}-{stamp}.sqlite3"
    )
    if backup_path.exists():
        raise SchemaError(
            f"Migration backup {backup_path} already exists; aborting without "
            "changing the database."
        )
    backup_database(path, backup_path)

    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        with db.write_transaction(conn):
            for step in range(version, SCHEMA_VERSION):
                MIGRATIONS[step](conn)
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    finally:
        conn.close()

    return {
        "schema_version": 1,
        "ok": True,
        "database": str(path),
        "operation": "migrate",
        "migrated": True,
        "from_version": version,
        "to_version": SCHEMA_VERSION,
        "backup": str(backup_path),
        "database_schema_version": SCHEMA_VERSION,
    }


def database_label(conn: sqlite3.Connection) -> str:
    """Return the project label recorded in ``metadata`` (fallback: file stem)."""
    row = conn.execute("SELECT value FROM metadata WHERE key = 'project_label'").fetchone()
    if row is not None and row["value"]:
        return str(row["value"])
    return Path(database_path(conn)).stem


def database_path(conn: sqlite3.Connection) -> str:
    """Return the on-disk path of the connection's main database."""
    for row in conn.execute("PRAGMA database_list"):
        if row["name"] == "main":
            return str(row["file"])
    return ""


def database_name(conn: sqlite3.Connection) -> str:
    """Return the file name of the connection's main database."""
    path = database_path(conn)
    return Path(path).name if path else "<unknown>"


def doctor(path: Path) -> dict[str, Any]:
    """Run the spec section 14 integrity checks."""
    path = Path(path)
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    if not path.exists():
        errors.append(
            {"code": "missing_database", "message": f"Database {path} does not exist."}
        )
        return {"schema_version": 1, "ok": False, "errors": errors, "warnings": warnings}

    try:
        conn = sqlite3.connect(str(path), isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
    except sqlite3.DatabaseError as exc:
        errors.append({"code": "unreadable", "message": f"Cannot open {path.name}: {exc}"})
        return {"schema_version": 1, "ok": False, "errors": errors, "warnings": warnings}

    try:
        try:
            application_id = _read_application_id(conn)
        except sqlite3.DatabaseError as exc:
            errors.append(
                {"code": "unreadable", "message": f"Cannot read {path.name}: {exc}"}
            )
            return {"schema_version": 1, "ok": False, "errors": errors, "warnings": warnings}

        if application_id != APPLICATION_ID:
            errors.append(
                {
                    "code": "application_id",
                    "message": (
                        f"application_id {application_id} is not a tackboard "
                        f"database id ({APPLICATION_ID})."
                    ),
                }
            )
            return {"schema_version": 1, "ok": False, "errors": errors, "warnings": warnings}

        version = _read_user_version(conn)
        if version != SCHEMA_VERSION:
            errors.append(
                {
                    "code": "schema_version",
                    "message": (
                        f"schema version {version} is not the supported version "
                        f"{SCHEMA_VERSION}."
                    ),
                    "found": version,
                    "supported": SCHEMA_VERSION,
                }
            )

        errors.extend(_doctor_foreign_keys(conn))
        errors.extend(_doctor_metadata(conn))
        errors.extend(_doctor_cycles(conn))
        if version == SCHEMA_VERSION:
            errors.extend(_doctor_memo(conn))
        warnings.extend(_doctor_claim_states(conn))
    finally:
        conn.close()

    return {
        "schema_version": 1,
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
    }


def _doctor_foreign_keys(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    codes = {
        "dependencies": "dangling_dependency",
        "claims": "orphan_claim",
        "task_labels": "orphan_label",
    }
    findings: list[dict[str, Any]] = []
    for row in conn.execute("PRAGMA foreign_key_check"):
        table = str(row[0])
        findings.append(
            {
                "code": codes.get(table, "foreign_key_violation"),
                "message": (
                    f"Row {row[1]} in {table} references a missing {row[2]} row "
                    f"(constraint {row[3]})."
                ),
                "table": table,
                "rowid": row[1],
            }
        )
    return findings


def _doctor_metadata(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    try:
        stored = {
            str(row["key"]): str(row["value"])
            for row in conn.execute("SELECT key, value FROM metadata")
        }
    except sqlite3.DatabaseError as exc:
        return [{"code": "metadata", "message": f"Cannot read metadata: {exc}"}]

    for key in METADATA_REQUIRED_KEYS:
        if key not in stored:
            findings.append(
                {"code": "metadata", "message": f"metadata key {key!r} is missing."}
            )

    raw = stored.get("next_sequence")
    if raw is not None:
        try:
            next_sequence = int(raw)
        except ValueError:
            findings.append(
                {
                    "code": "metadata",
                    "message": f"metadata next_sequence {raw!r} is not an integer.",
                }
            )
        else:
            row = conn.execute(
                "SELECT COALESCE(MAX(sequence), 0) AS max_seq FROM tasks"
            ).fetchone()
            highest = int(row["max_seq"])
            if next_sequence <= highest:
                findings.append(
                    {
                        "code": "metadata",
                        "message": (
                            f"metadata next_sequence {next_sequence} would reuse "
                            f"existing task sequences (highest is {highest})."
                        ),
                    }
                )
    return findings


def _doctor_cycles(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    from .dependencies import find_cycle

    cycle = find_cycle(conn)
    if cycle is None:
        return []
    return [
        {
            "code": "dependency_cycle",
            "message": "Dependency cycle: " + " -> ".join(cycle) + ".",
            "path": cycle,
        }
    ]


def _doctor_memo(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Validate the board-level memo's revision table (schema version 2 only).

    A revision row is immutable and is never deleted, so the revisions must be
    a gapless, monotonic run ``1..N``; anything else is tampering or corruption.
    """
    try:
        rows = conn.execute(
            "SELECT rev, text, author FROM memo_revisions ORDER BY rev"
        ).fetchall()
    except sqlite3.DatabaseError:
        return [
            {
                "code": "memo_table_missing",
                "message": "schema version 2 requires the memo_revisions table.",
            }
        ]

    findings: list[dict[str, Any]] = []
    expected = 1
    for row in rows:
        rev = int(row["rev"])
        if rev != expected:
            findings.append(
                {
                    "code": "memo_revision_gap",
                    "message": (
                        "memo revisions must run 1..N without a gap: expected "
                        f"{expected}, found {rev}."
                    ),
                    "expected": expected,
                    "found": rev,
                }
            )
        expected = rev + 1

        size = len(str(row["text"]).encode("utf-8"))
        if size > MAX_MEMO_BYTES:
            findings.append(
                {
                    "code": "memo_revision_oversize",
                    "message": (
                        f"memo revision {rev} is {size} bytes, over the "
                        f"{MAX_MEMO_BYTES}-byte cap."
                    ),
                    "rev": rev,
                    "bytes": size,
                    "max": MAX_MEMO_BYTES,
                }
            )
        if not str(row["author"]).strip():
            findings.append(
                {
                    "code": "memo_revision_author",
                    "message": f"memo revision {rev} has an empty author.",
                    "rev": rev,
                }
            )
    return findings


def _doctor_claim_states(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []

    rows = conn.execute(
        """
        SELECT c.task_id AS task_id, c.agent_id AS agent_id, t.status AS status
        FROM claims c JOIN tasks t ON t.id = c.task_id
        WHERE t.status NOT IN ('in_progress', 'review')
        """
    ).fetchall()
    for row in rows:
        findings.append(
            {
                "code": "claim_on_inactive_task",
                "message": (
                    f"{row['task_id']} is claimed by {row['agent_id']} but its "
                    f"status is {row['status']}."
                ),
                "task_id": row["task_id"],
            }
        )

    rows = conn.execute(
        """
        SELECT t.id AS task_id
        FROM tasks t
        WHERE t.status = 'in_progress'
          AND NOT EXISTS (SELECT 1 FROM claims c WHERE c.task_id = t.id)
        """
    ).fetchall()
    for row in rows:
        findings.append(
            {
                "code": "in_progress_without_claim",
                "message": f"{row['task_id']} is in_progress without an active claim.",
                "task_id": row["task_id"],
            }
        )

    rows = conn.execute(
        """
        SELECT t.id AS task_id, t.status AS status, d.prerequisite_id AS prerequisite_id
        FROM tasks t
        JOIN dependencies d ON d.task_id = t.id
        JOIN tasks p ON p.id = d.prerequisite_id
        WHERE t.status IN ('in_progress', 'review') AND p.status <> 'done'
        """
    ).fetchall()
    for row in rows:
        findings.append(
            {
                "code": "inconsistent_blocked_state",
                "message": (
                    f"{row['task_id']} is {row['status']} while prerequisite "
                    f"{row['prerequisite_id']} is unfinished."
                ),
                "task_id": row["task_id"],
                "prerequisite_id": row["prerequisite_id"],
            }
        )
    return findings


__all__ = [
    "APPLICATION_ID",
    "MIGRATIONS",
    "SCHEMA_VERSION",
    "backup_database",
    "database_label",
    "database_name",
    "database_path",
    "doctor",
    "init_database",
    "migrate_database",
    "open_database",
]
