"""Database identity, DDL, migrations, backup, and diagnostics.

Spec: section 5.4 (initialization and safety), section 12 (schema),
section 14 (``doctor``, ``backup``). Maintenance operations are not task
mutations and never write task events.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import db
from .config import DEFAULT_STALE_CLAIM_SECONDS
from .errors import NotFoundError, SchemaError, TackError, ValidationError
from .models import (
    MAX_MEMO_BYTES,
    MAX_NOTE_REFS,
    MAX_REF_PATH_LENGTH,
    MAX_REF_URL_LENGTH,
    NOTE_REF_KINDS,
    Claim,
    utcnow_iso,
    utcstamp_iso,
)

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


#: Filesystem types whose WAL locking is unsafe for a live SQLite database.
#: Deliberately narrow: a false warning is worse than none, so only well-known
#: network/shared filesystems are listed. Detection that cannot read the mount
#: table stays silent.
NETWORK_FILESYSTEM_TYPES: frozenset[str] = frozenset(
    {
        "nfs",
        "nfs4",
        "cifs",
        "smbfs",
        "smb3",
        "sshfs",
        "fuse.sshfs",
        "fuse.rclone",
        "fuse.smbnetfs",
        "glusterfs",
        "ceph",
        "cephfs",
        "fuse.cephfs",
        "afs",
        "9p",
        "davfs",
        "lustre",
    }
)


def _read_mount_table() -> str | None:
    """Return the kernel mount table, or ``None`` when it cannot be read."""
    try:
        return Path("/proc/self/mounts").read_text(encoding="utf-8")
    except OSError:
        return None


def _mount_for_path(
    path: Path, mounts: str | None = None
) -> tuple[str, str] | None:
    """Return ``(fstype, mount_point)`` for the filesystem holding ``path``.

    ``None`` means detection is impossible or the path is not mounted: callers
    stay silent rather than guess. The longest matching mount point wins.
    """
    table = _read_mount_table() if mounts is None else mounts
    if table is None:
        return None
    target = str(path.resolve())
    best: tuple[int, str, str] | None = None
    for line in table.splitlines():
        fields = line.split()
        if len(fields) < 3:
            continue
        mount_point = fields[1].replace("\\040", " ")
        matches = target == mount_point or target.startswith(
            mount_point.rstrip("/") + "/"
        )
        if matches and (best is None or len(mount_point) > best[0]):
            best = (len(mount_point), fields[2], mount_point)
    if best is None:
        return None
    return best[1], best[2]


def _doctor_network_filesystem(path: Path) -> list[dict[str, Any]]:
    """Warn when the board file sits on a network filesystem unsafe for WAL."""
    mount = _mount_for_path(path)
    if mount is None or mount[0] not in NETWORK_FILESYSTEM_TYPES:
        return []
    fstype, mount_point = mount
    return [
        {
            "code": "network_filesystem",
            "message": (
                f"Database {path.name} is on a {fstype} mount at {mount_point}; "
                "SQLite WAL is unsafe on network/shared filesystems. Keep the "
                "board on a local disk, or accept the corruption risk knowingly."
            ),
            "fstype": fstype,
            "mount_point": mount_point,
        }
    ]


def doctor(
    path: Path, *, stale_after_seconds: int | None = None
) -> dict[str, Any]:
    """Run the spec section 14 integrity checks.

    ``stale_after_seconds`` is the stale-claim threshold used for the
    ``stale_claim`` warning; callers resolve it from configuration and pass it
    in, so this module stays configuration-free. ``None`` means the documented
    default (``2h``). A stale claim is a warning, never an error.
    """
    path = Path(path)
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    threshold = (
        stale_after_seconds
        if stale_after_seconds is not None
        else DEFAULT_STALE_CLAIM_SECONDS
    )

    if not path.exists():
        errors.append(
            {"code": "missing_database", "message": f"Database {path} does not exist."}
        )
        return {"schema_version": 1, "ok": False, "errors": errors, "warnings": warnings}

    warnings.extend(_doctor_network_filesystem(path))

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
            warnings.extend(_doctor_note_refs(conn))
            warnings.extend(_doctor_memo_events(conn))
            warnings.extend(_doctor_plan_events(conn))
        warnings.extend(_doctor_claim_states(conn, threshold))
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
    A revision table that is missing or unusable is one condition, reported with
    the same code and message the memo command path raises.
    """
    from .memo import MEMO_TABLE_INVALID_MESSAGE

    try:
        rows = conn.execute(
            "SELECT rev, text, author FROM memo_revisions ORDER BY rev"
        ).fetchall()
    except sqlite3.DatabaseError:
        return [
            {
                "code": "memo_table_invalid",
                "message": MEMO_TABLE_INVALID_MESSAGE,
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


_NOTE_REF_COMMIT_RE = re.compile(r"\A[0-9a-fA-F]{7,40}\Z")
_NOTE_REF_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def _note_ref_finding(
    *,
    code: str,
    message: str,
    task_id: str | None,
    event_id: int,
    kind: str | None = None,
    value: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    finding: dict[str, Any] = {
        "code": code,
        "message": message,
        "task_id": task_id,
        "event_id": event_id,
    }
    if kind is not None:
        finding["kind"] = kind
    if value is not None:
        finding["value"] = value
    if limit is not None:
        finding["limit"] = limit
    return finding


def _doctor_note_refs(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Warn when a stored note's refs break the documented kind limits.

    The CLI validates refs before writing, so a finding here means the event
    payload was written outside the CLI (corruption or tampering). Each finding
    names the task and the event so the row can be located.
    """
    findings: list[dict[str, Any]] = []
    rows = conn.execute(
        "SELECT id, task_id, payload_json FROM events WHERE event_type = 'note' "
        "ORDER BY id"
    ).fetchall()
    task_ids = {str(row["id"]) for row in conn.execute("SELECT id FROM tasks")}
    for row in rows:
        event_id = int(row["id"])
        task_id = row["task_id"]
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, ValueError):
            findings.append(
                _note_ref_finding(
                    code="note_payload_invalid",
                    message=f"note event {event_id} does not carry a JSON payload.",
                    task_id=task_id,
                    event_id=event_id,
                )
            )
            continue
        refs = payload.get("refs") if isinstance(payload, dict) else None
        if refs is None:
            continue
        if not isinstance(refs, list):
            findings.append(
                _note_ref_finding(
                    code="note_ref_invalid",
                    message=f"note event {event_id} carries refs that are not a list.",
                    task_id=task_id,
                    event_id=event_id,
                )
            )
            continue
        if len(refs) > MAX_NOTE_REFS:
            findings.append(
                _note_ref_finding(
                    code="note_refs_exceeded",
                    message=(
                        f"note event {event_id} has {len(refs)} refs, over the "
                        f"{MAX_NOTE_REFS}-ref cap."
                    ),
                    task_id=task_id,
                    event_id=event_id,
                    limit=MAX_NOTE_REFS,
                )
            )
        for ref in refs:
            if not isinstance(ref, dict):
                findings.append(
                    _note_ref_finding(
                        code="note_ref_invalid",
                        message=f"note event {event_id} has a ref that is not an object.",
                        task_id=task_id,
                        event_id=event_id,
                    )
                )
                continue
            kind = ref.get("kind")
            value = ref.get("value")
            if not isinstance(kind, str) or kind not in NOTE_REF_KINDS:
                findings.append(
                    _note_ref_finding(
                        code="note_ref_invalid",
                        message=(
                            f"note event {event_id} has ref kind {kind!r}, which is "
                            f"not one of {', '.join(NOTE_REF_KINDS)}."
                        ),
                        task_id=task_id,
                        event_id=event_id,
                        kind=str(kind),
                    )
                )
                continue
            if not isinstance(value, str) or not value.strip():
                findings.append(
                    _note_ref_finding(
                        code="note_ref_invalid",
                        message=f"note event {event_id} has an empty {kind} ref value.",
                        task_id=task_id,
                        event_id=event_id,
                        kind=kind,
                    )
                )
                continue
            limit = {
                "url": MAX_REF_URL_LENGTH,
                "path": MAX_REF_PATH_LENGTH,
                "commit": 40,
            }.get(kind)
            invalid = _NOTE_REF_CONTROL_RE.search(value) is not None
            if kind == "commit" and not _NOTE_REF_COMMIT_RE.match(value):
                invalid = True
            if limit is not None and len(value) > limit:
                invalid = True
            if invalid:
                findings.append(
                    _note_ref_finding(
                        code="note_ref_invalid",
                        message=(
                            f"note event {event_id} has a {kind} ref that breaks "
                            "the documented value limits."
                        ),
                        task_id=task_id,
                        event_id=event_id,
                        kind=kind,
                        value=value[:120],
                        limit=limit,
                    )
                )
                continue
            if kind == "task" and value not in task_ids:
                findings.append(
                    _note_ref_finding(
                        code="note_ref_missing_task",
                        message=(
                            f"note event {event_id} refers to {value}, which is "
                            "not a task on this board."
                        ),
                        task_id=task_id,
                        event_id=event_id,
                        kind=kind,
                        value=value,
                    )
                )
    return findings


def _doctor_memo_events(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Warn when a ``memo.*`` event's revision pointer does not resolve.

    Every memo write appends a revision row and one event naming it, so the
    event log and the revision table must agree: each event's ``rev`` exists,
    and the newest event names the current revision. A missing revision table
    is already reported by ``_doctor_memo``; this check then stays silent.
    """
    try:
        revisions = {
            int(row["rev"]) for row in conn.execute("SELECT rev FROM memo_revisions")
        }
    except sqlite3.DatabaseError:
        return []
    current = max(revisions) if revisions else 0

    findings: list[dict[str, Any]] = []
    latest_rev: int | None = None
    rows = conn.execute(
        "SELECT id, payload_json FROM events WHERE event_type LIKE 'memo.%' "
        "ORDER BY id"
    ).fetchall()
    for row in rows:
        event_id = int(row["id"])
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, ValueError):
            payload = None
        rev = payload.get("rev") if isinstance(payload, dict) else None
        if not isinstance(rev, int):
            findings.append(
                {
                    "code": "memo_event_invalid",
                    "message": f"memo event {event_id} does not name a revision.",
                    "event_id": event_id,
                }
            )
            continue
        latest_rev = rev
        if rev not in revisions:
            findings.append(
                {
                    "code": "memo_event_missing_revision",
                    "message": (
                        f"memo event {event_id} names revision {rev}, which is "
                        "not in memo_revisions."
                    ),
                    "event_id": event_id,
                    "rev": rev,
                }
            )
    if latest_rev is not None and latest_rev != current:
        findings.append(
            {
                "code": "memo_event_rev_mismatch",
                "message": (
                    f"the newest memo event names revision {latest_rev}, but the "
                    f"current revision is {current}."
                ),
                "expected": current,
                "found": latest_rev,
            }
        )
    return findings


def _doctor_plan_events(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Warn when a ``plan.applied`` event names tasks that do not exist.

    Plan application is atomic and creates every task it names, so a missing
    id means the task row was removed outside the CLI (cancel is the only
    removal path, and it keeps the row).
    """
    findings: list[dict[str, Any]] = []
    task_ids = {str(row["id"]) for row in conn.execute("SELECT id FROM tasks")}
    rows = conn.execute(
        "SELECT id, payload_json FROM events WHERE event_type = 'plan.applied' "
        "ORDER BY id"
    ).fetchall()
    for row in rows:
        event_id = int(row["id"])
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, ValueError):
            payload = None
        ids = payload.get("ids") if isinstance(payload, dict) else None
        if not isinstance(ids, list):
            findings.append(
                {
                    "code": "plan_event_invalid",
                    "message": f"plan.applied event {event_id} does not name task ids.",
                    "event_id": event_id,
                }
            )
            continue
        for task_id in ids:
            if not isinstance(task_id, str) or task_id not in task_ids:
                findings.append(
                    {
                        "code": "plan_event_missing_task",
                        "message": (
                            f"plan.applied event {event_id} names {task_id!r}, "
                            "which is not a task on this board."
                        ),
                        "event_id": event_id,
                        "task_id": str(task_id),
                    }
                )
    return findings


def _doctor_claim_states(
    conn: sqlite3.Connection, stale_after_seconds: int
) -> list[dict[str, Any]]:
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

    rows = conn.execute(
        """
        SELECT c.task_id AS task_id, c.agent_id AS agent_id, c.claimed_at AS claimed_at
        FROM claims c JOIN tasks t ON t.id = c.task_id
        ORDER BY c.claimed_at, c.task_id
        """
    ).fetchall()
    from .service import derived_flags  # local: service imports this module

    for row in rows:
        claim = Claim(
            task_id=str(row["task_id"]),
            agent_id=str(row["agent_id"]),
            claimed_at=str(row["claimed_at"]),
        )
        # One source for the stale rule: the same derived flag the projection
        # and `claims --stale` read, against the same configured threshold.
        flags = derived_flags(
            (),
            None,
            "in_progress",
            claim,
            stale_after_seconds=stale_after_seconds,
        )
        age = flags["claim_age_seconds"]
        if not flags["claim_stale"] or age is None:
            continue
        findings.append(
            {
                "code": "stale_claim",
                "message": (
                    f"{row['task_id']} is claimed by {row['agent_id']}; the claim "
                    f"is {age}s old (stale after {stale_after_seconds}s)."
                ),
                "task_id": row["task_id"],
                "agent_id": row["agent_id"],
                "age_seconds": age,
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
