"""Database safety: init, open, migrate, backup, doctor (spec 5.4, 12, 14)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tackboard import schema, service
from tackboard.errors import NotFoundError, SchemaError, ValidationError


def _raw(path: Path) -> sqlite3.Connection:
    """A connection with foreign keys off, for seeding corrupt states."""
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=OFF")
    return conn


def test_init_creates_an_identified_database(tmp_path: Path) -> None:
    path = tmp_path / "board.sqlite3"
    result = schema.init_database(path, label="proj")
    assert result["ok"] is True
    assert result["created"] is True
    assert result["operation"] == "init"
    assert result["database"] == str(path)

    raw = _raw(path)
    try:
        assert raw.execute("PRAGMA application_id").fetchone()[0] == schema.APPLICATION_ID
        assert raw.execute("PRAGMA user_version").fetchone()[0] == schema.SCHEMA_VERSION
        assert raw.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        metadata = {row["key"]: row["value"] for row in raw.execute("SELECT * FROM metadata")}
    finally:
        raw.close()
    assert metadata["project_label"] == "proj"
    assert metadata["next_sequence"] == "1"
    assert metadata["created_at"]


def test_init_creates_parent_directories(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "deeper" / "board.sqlite3"
    schema.init_database(path)
    assert path.exists()


def test_init_is_idempotent_and_never_erases_tasks(tmp_path: Path) -> None:
    path = tmp_path / "board.sqlite3"
    schema.init_database(path)
    conn = schema.open_database(path)
    task = service.create_task(conn, actor="dev", title="Keep me")["task"]
    conn.close()

    again = schema.init_database(path)
    assert again["created"] is False

    conn = schema.open_database(path)
    try:
        assert service.get_task(conn, task["id"])["title"] == "Keep me"
    finally:
        conn.close()


def test_init_refuses_an_unrelated_sqlite_file(tmp_path: Path) -> None:
    path = tmp_path / "other.sqlite3"
    raw = sqlite3.connect(path)
    raw.execute("CREATE TABLE t(x)")
    raw.execute("INSERT INTO t VALUES(1)")
    raw.commit()
    raw.close()

    with pytest.raises(SchemaError) as caught:
        schema.init_database(path)
    assert caught.value.exit_code == 6

    raw = sqlite3.connect(path)
    try:
        assert raw.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1
    finally:
        raw.close()


def test_init_refuses_a_non_sqlite_file(tmp_path: Path) -> None:
    path = tmp_path / "junk.sqlite3"
    path.write_text("this is not a database at all")
    with pytest.raises(SchemaError):
        schema.init_database(path)


def test_open_never_creates_a_missing_database(tmp_path: Path) -> None:
    path = tmp_path / "ghost.sqlite3"
    with pytest.raises(NotFoundError) as caught:
        schema.open_database(path)
    assert caught.value.exit_code == 3
    assert not path.exists()


def test_older_schema_fails_until_migrated(tmp_path: Path) -> None:
    path = tmp_path / "board.sqlite3"
    schema.init_database(path)
    raw = _raw(path)
    raw.execute("PRAGMA user_version=0")
    raw.close()

    with pytest.raises(SchemaError) as caught:
        schema.open_database(path)
    assert caught.value.exit_code == 6
    assert "tack migrate" in str(caught.value)


def test_newer_schema_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "board.sqlite3"
    schema.init_database(path)
    raw = _raw(path)
    raw.execute("PRAGMA user_version=99")
    raw.close()

    with pytest.raises(SchemaError) as caught:
        schema.open_database(path)
    assert "newer" in str(caught.value)


def test_init_refuses_an_older_schema_database(tmp_path: Path) -> None:
    """`init` is idempotent-or-refuse; only `migrate` changes a schema version."""
    path = tmp_path / "board.sqlite3"
    schema.init_database(path)
    conn = schema.open_database(path)
    task = service.create_task(conn, actor="dev", title="Kept")["task"]
    conn.close()

    raw = _raw(path)
    raw.execute("PRAGMA user_version=0")
    raw.close()
    before = path.read_bytes()

    with pytest.raises(SchemaError) as caught:
        schema.init_database(path)
    assert caught.value.exit_code == 6
    assert "tack migrate" in str(caught.value)

    # No in-place upgrade, no rewrite: the file is byte- and content-unchanged.
    assert path.read_bytes() == before
    raw = _raw(path)
    try:
        assert raw.execute("PRAGMA user_version").fetchone()[0] == 0
        assert raw.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1
        title = raw.execute(
            "SELECT title FROM tasks WHERE id = ?", (task["id"],)
        ).fetchone()[0]
        assert title == "Kept"
    finally:
        raw.close()


def test_migrate_preserves_data_and_writes_a_backup(tmp_path: Path) -> None:
    path = tmp_path / "board.sqlite3"
    schema.init_database(path)
    conn = schema.open_database(path)
    task = service.create_task(conn, actor="dev", title="Survives migration")["task"]
    conn.close()

    raw = _raw(path)
    raw.execute("PRAGMA user_version=0")
    raw.close()

    result = schema.migrate_database(path)
    assert result["migrated"] is True
    assert result["from_version"] == 0
    assert result["to_version"] == schema.SCHEMA_VERSION

    backup = Path(result["backup"])
    assert backup.exists()
    assert backup.name.startswith(
        f"{path.name}.pre-migrate-v0-to-v{schema.SCHEMA_VERSION}-"
    )

    conn = schema.open_database(path)
    try:
        assert service.get_task(conn, task["id"])["title"] == "Survives migration"
    finally:
        conn.close()


def test_migrate_is_a_no_op_at_the_current_version(tmp_path: Path) -> None:
    path = tmp_path / "board.sqlite3"
    schema.init_database(path)
    result = schema.migrate_database(path)
    assert result["migrated"] is False


def test_migrate_aborts_when_the_backup_already_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "board.sqlite3"
    schema.init_database(path)
    raw = _raw(path)
    raw.execute("PRAGMA user_version=0")
    raw.close()

    monkeypatch.setattr(schema, "utcstamp_iso", lambda: "fixed")
    existing = path.with_name(
        f"{path.name}.pre-migrate-v0-to-v{schema.SCHEMA_VERSION}-fixed.sqlite3"
    )
    existing.write_bytes(b"occupied")

    with pytest.raises(SchemaError) as caught:
        schema.migrate_database(path)
    assert caught.value.exit_code == 6

    raw = _raw(path)
    try:
        assert raw.execute("PRAGMA user_version").fetchone()[0] == 0
    finally:
        raw.close()


def test_backup_produces_a_valid_database(tmp_path: Path, db_path: Path) -> None:
    conn = schema.open_database(db_path)
    task = service.create_task(conn, actor="dev", title="Backed up")["task"]
    conn.close()

    destination = tmp_path / "snapshot.sqlite3"
    result = schema.backup_database(db_path, destination)
    assert result["ok"] is True
    assert result["operation"] == "backup"

    conn = schema.open_database(destination)
    try:
        assert service.get_task(conn, task["id"])["title"] == "Backed up"
    finally:
        conn.close()


def test_backup_refuses_to_overwrite(tmp_path: Path, db_path: Path) -> None:
    destination = tmp_path / "snapshot.sqlite3"
    destination.write_text("")
    with pytest.raises(ValidationError):
        schema.backup_database(db_path, destination)


def test_backup_requires_an_existing_destination_directory(
    tmp_path: Path, db_path: Path
) -> None:
    destination = tmp_path / "missing" / "snapshot.sqlite3"
    with pytest.raises(NotFoundError):
        schema.backup_database(db_path, destination)
    assert not destination.parent.exists()


def test_two_databases_stay_isolated(tmp_path: Path) -> None:
    alpha_path = tmp_path / "alpha.sqlite3"
    beta_path = tmp_path / "beta.sqlite3"
    schema.init_database(alpha_path, label="alpha")
    schema.init_database(beta_path, label="beta")

    alpha = schema.open_database(alpha_path)
    beta = schema.open_database(beta_path)
    try:
        alpha_task = service.create_task(alpha, actor="dev", title="Alpha work")["task"]
        beta_task = service.create_task(beta, actor="dev", title="Beta work")["task"]

        # Each database has its own ID namespace and its own tasks.
        assert alpha_task["id"] == beta_task["id"] == "T-000001"
        assert [task["title"] for task in service.list_tasks(alpha)] == ["Alpha work"]
        assert [task["title"] for task in service.list_tasks(beta)] == ["Beta work"]
        assert service.get_task(beta, alpha_task["id"])["title"] == "Beta work"
        assert schema.database_label(alpha) == "alpha"
        assert schema.database_label(beta) == "beta"
    finally:
        alpha.close()
        beta.close()


def test_doctor_is_happy_with_a_healthy_database(db_path: Path) -> None:
    report = schema.doctor(db_path)
    assert report["ok"] is True
    assert report["errors"] == []
    assert report["warnings"] == []


def test_doctor_warns_when_the_board_lives_on_a_network_filesystem(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mount_point = str(db_path.parent)
    monkeypatch.setattr(
        schema,
        "_read_mount_table",
        lambda: f"overlay / overlay rw 0 0\nserver:/share {mount_point} nfs4 rw 0 0\n",
    )
    report = schema.doctor(db_path)
    warnings = [entry for entry in report["warnings"] if entry["code"] == "network_filesystem"]
    assert len(warnings) == 1
    assert warnings[0]["fstype"] == "nfs4"
    assert warnings[0]["mount_point"] == mount_point
    assert "WAL is unsafe" in warnings[0]["message"]
    assert report["ok"] is True  # a warning never changes the exit code


def test_doctor_stays_silent_on_a_local_filesystem(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mount_point = str(db_path.parent)
    monkeypatch.setattr(
        schema,
        "_read_mount_table",
        lambda: f"overlay / overlay rw 0 0\n/dev/sda1 {mount_point} ext4 rw 0 0\n",
    )
    report = schema.doctor(db_path)
    assert not any(entry["code"] == "network_filesystem" for entry in report["warnings"])


def test_doctor_stays_silent_when_the_mount_table_is_unreadable(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(schema, "_read_mount_table", lambda: None)
    report = schema.doctor(db_path)
    assert not any(entry["code"] == "network_filesystem" for entry in report["warnings"])


def test_doctor_reports_a_missing_database(tmp_path: Path) -> None:
    report = schema.doctor(tmp_path / "ghost.sqlite3")
    assert report["ok"] is False
    assert report["errors"][0]["code"] == "missing_database"


def test_doctor_detects_a_dangling_dependency(db_path: Path) -> None:
    conn = schema.open_database(db_path)
    task = service.create_task(conn, actor="dev", title="Orphaned edge")["task"]
    conn.close()

    raw = _raw(db_path)
    raw.execute(
        "INSERT INTO dependencies(prerequisite_id, task_id) VALUES('T-999999', ?)",
        (task["id"],),
    )
    raw.close()

    report = schema.doctor(db_path)
    codes = {entry["code"] for entry in report["errors"]}
    assert "dangling_dependency" in codes
    assert report["ok"] is False


def test_doctor_detects_orphan_and_missing_claims(db_path: Path) -> None:
    conn = schema.open_database(db_path)
    claimed_but_idle = service.create_task(conn, actor="dev", title="Idle claim")["task"]
    starting_without_claim = service.create_task(
        conn, actor="dev", title="Unclaimed work"
    )["task"]
    conn.close()

    raw = _raw(db_path)
    raw.execute(
        "INSERT INTO claims(task_id, agent_id, claimed_at) VALUES(?, ?, ?)",
        (claimed_but_idle["id"], "dev", "2026-01-01T00:00:00Z"),
    )
    raw.execute(
        "UPDATE tasks SET status = 'in_progress' WHERE id = ?",
        (starting_without_claim["id"],),
    )
    raw.close()

    report = schema.doctor(db_path)
    warning_codes = {entry["code"] for entry in report["warnings"]}
    assert "claim_on_inactive_task" in warning_codes
    assert "in_progress_without_claim" in warning_codes
    assert report["ok"] is True  # warnings do not fail the board


def test_doctor_warns_about_a_stale_claim_but_not_a_fresh_one(db_path: Path) -> None:
    conn = schema.open_database(db_path)
    aged = service.create_task(conn, actor="dev", title="Abandoned")["task"]
    fresh = service.create_task(conn, actor="dev", title="In hand")["task"]
    conn.close()

    raw = _raw(db_path)
    for task in (aged, fresh):
        raw.execute(
            "UPDATE tasks SET status = 'in_progress' WHERE id = ?", (task["id"],)
        )
    raw.execute(
        "INSERT INTO claims(task_id, agent_id, claimed_at) VALUES(?, ?, ?)",
        (aged["id"], "dev", "2020-01-01T00:00:00Z"),
    )
    raw.execute(
        "INSERT INTO claims(task_id, agent_id, claimed_at) VALUES(?, ?, ?)",
        (fresh["id"], "qa", "2099-01-01T00:00:00Z"),
    )
    raw.close()

    report = schema.doctor(db_path)
    stale = [entry for entry in report["warnings"] if entry["code"] == "stale_claim"]
    assert [entry["task_id"] for entry in stale] == [aged["id"]]
    assert stale[0]["agent_id"] == "dev"
    assert stale[0]["age_seconds"] > 7200
    assert aged["id"] in stale[0]["message"]
    assert report["ok"] is True  # a stale claim is a warning, never an error

    # The threshold is a parameter: a large enough one hides the same claim.
    relaxed = schema.doctor(db_path, stale_after_seconds=10**9)
    assert not any(
        entry["code"] == "stale_claim" for entry in relaxed["warnings"]
    )


def test_doctor_detects_a_dependency_cycle(db_path: Path) -> None:
    conn = schema.open_database(db_path)
    first = service.create_task(conn, actor="dev", title="First")["task"]
    second = service.create_task(conn, actor="dev", title="Second")["task"]
    conn.close()

    raw = _raw(db_path)
    raw.execute(
        "INSERT INTO dependencies(prerequisite_id, task_id) VALUES(?, ?)",
        (first["id"], second["id"]),
    )
    raw.execute(
        "INSERT INTO dependencies(prerequisite_id, task_id) VALUES(?, ?)",
        (second["id"], first["id"]),
    )
    raw.close()

    report = schema.doctor(db_path)
    assert any(entry["code"] == "dependency_cycle" for entry in report["errors"])


def test_doctor_detects_metadata_damage(db_path: Path) -> None:
    raw = _raw(db_path)
    raw.execute("DELETE FROM metadata WHERE key = 'next_sequence'")
    raw.close()

    report = schema.doctor(db_path)
    assert any(entry["code"] == "metadata" for entry in report["errors"])
