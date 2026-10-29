"""Doctor checks for the note-ref, memo-event and plan-event surfaces."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from tackboard import schema, service
from tackboard.models import MAX_NOTE_REFS, MAX_REF_URL_LENGTH

NOTE_CODES = {
    "note_refs_exceeded",
    "note_ref_invalid",
    "note_ref_missing_task",
    "note_payload_invalid",
}
MEMO_CODES = {
    "memo_event_invalid",
    "memo_event_missing_revision",
    "memo_event_rev_mismatch",
}
PLAN_CODES = {"plan_event_invalid", "plan_event_missing_task"}


def _raw(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _insert_event(
    conn: sqlite3.Connection,
    *,
    event_type: str,
    payload: dict[str, object],
    task_id: str | None = None,
    actor: str = "dev",
) -> int:
    cursor = conn.execute(
        "INSERT INTO events(timestamp, actor, event_type, task_id, payload_json) "
        "VALUES('2026-01-01T00:00:00Z', ?, ?, ?, ?)",
        (actor, event_type, task_id, json.dumps(payload)),
    )
    return int(cursor.lastrowid or 0)


def _codes(report: dict[str, object]) -> set[str]:
    warnings = report["warnings"]
    assert isinstance(warnings, list)
    return {entry["code"] for entry in warnings}


def test_doctor_is_quiet_about_valid_note_refs(db_path: Path) -> None:
    conn = schema.open_database(db_path)
    first = service.create_task(conn, actor="dev", title="First")["task"]
    second = service.create_task(conn, actor="dev", title="Second")["task"]
    service.add_note(
        conn,
        first["id"],
        "Handoff",
        actor="dev",
        refs=[
            "commit:abc1234",
            "url:https://example.invalid/x",
            "path:src/tackboard/service.py",
            f"task:{second['id']}",
        ],
    )
    conn.close()

    report = schema.doctor(db_path)
    assert not (_codes(report) & NOTE_CODES)
    assert report["ok"] is True


def test_doctor_warns_about_a_stored_note_that_breaks_the_ref_rules(
    db_path: Path,
) -> None:
    conn = schema.open_database(db_path)
    task = service.create_task(conn, actor="dev", title="Corrupted")["task"]
    conn.close()

    refs = [{"kind": "path", "value": f"src/file-{index}.py"} for index in range(21)]
    refs.extend(
        [
            {"kind": "url", "value": "u" * (MAX_REF_URL_LENGTH + 1)},
            {"kind": "bogus", "value": "x"},
            {"kind": "task", "value": "T-999999"},
            {"kind": "commit", "value": "not-a-sha"},
        ]
    )
    raw = _raw(db_path)
    event_id = _insert_event(
        raw,
        event_type="note",
        task_id=task["id"],
        payload={"text": "written outside the CLI", "refs": refs},
    )
    raw.close()

    report = schema.doctor(db_path)
    findings = [
        entry
        for entry in report["warnings"]
        if entry["code"] in NOTE_CODES
    ]
    assert findings, report["warnings"]
    assert all(entry["event_id"] == event_id for entry in findings)
    assert all(entry["task_id"] == task["id"] for entry in findings)
    codes = {entry["code"] for entry in findings}
    assert {"note_refs_exceeded", "note_ref_invalid", "note_ref_missing_task"} <= codes

    exceeded = [entry for entry in findings if entry["code"] == "note_refs_exceeded"]
    assert exceeded[0]["limit"] == MAX_NOTE_REFS
    missing = [entry for entry in findings if entry["code"] == "note_ref_missing_task"]
    assert missing[0]["value"] == "T-999999"
    assert report["ok"] is True  # corruption is still a warning, not an error


def test_doctor_warns_about_a_note_event_without_a_json_payload(db_path: Path) -> None:
    conn = schema.open_database(db_path)
    task = service.create_task(conn, actor="dev", title="Broken payload")["task"]
    conn.close()

    # The events table refuses UPDATE, so the malformed row is seeded directly.
    raw = _raw(db_path)
    raw.execute(
        "INSERT INTO events(timestamp, actor, event_type, task_id, payload_json) "
        "VALUES('2026-01-01T00:00:00Z', 'dev', 'note', ?, 'not json')",
        (task["id"],),
    )
    raw.close()

    report = schema.doctor(db_path)
    codes = _codes(report)
    assert "note_payload_invalid" in codes


def test_doctor_warns_when_a_memo_event_names_a_missing_revision(
    db_path: Path,
) -> None:
    raw = _raw(db_path)
    event_id = _insert_event(
        raw, event_type="memo.replaced", payload={"rev": 4, "bytes": 1}
    )
    raw.close()

    report = schema.doctor(db_path)
    findings = [e for e in report["warnings"] if e["code"] in MEMO_CODES]
    codes = {entry["code"] for entry in findings}
    assert "memo_event_missing_revision" in codes
    missing = [
        entry for entry in findings if entry["code"] == "memo_event_missing_revision"
    ]
    assert missing[0]["event_id"] == event_id
    assert missing[0]["rev"] == 4
    assert report["ok"] is True


def test_doctor_warns_when_the_newest_memo_event_lags_the_current_revision(
    db_path: Path,
) -> None:
    from tackboard import memo

    conn = schema.open_database(db_path)
    memo.replace_memo(conn, text="first", actor="dev", reason="start")
    conn.close()

    # A revision written outside the memo path leaves the newest event naming
    # revision 1 while the table's current revision is higher.
    raw = _raw(db_path)
    raw.execute(
        "INSERT INTO memo_revisions(rev, text, author, created_at, reason) "
        "VALUES(2, 'hidden', 'dev', '2026-01-01T00:00:00Z', NULL)"
    )
    raw.close()

    report = schema.doctor(db_path)
    findings = [
        entry for entry in report["warnings"] if entry["code"] == "memo_event_rev_mismatch"
    ]
    assert findings, report["warnings"]
    assert findings[0]["expected"] == 2
    assert findings[0]["found"] == 1


def test_doctor_warns_when_a_plan_event_names_a_missing_task(db_path: Path) -> None:
    conn = schema.open_database(db_path)
    task = service.create_task(conn, actor="dev", title="Kept")["task"]
    conn.close()

    raw = _raw(db_path)
    event_id = _insert_event(
        raw,
        event_type="plan.applied",
        payload={"file": "plan.json", "tasks": 2, "ids": [task["id"], "T-999999"]},
    )
    raw.close()

    report = schema.doctor(db_path)
    findings = [
        entry for entry in report["warnings"] if entry["code"] in PLAN_CODES
    ]
    assert [entry["code"] for entry in findings] == ["plan_event_missing_task"]
    assert findings[0]["event_id"] == event_id
    assert findings[0]["task_id"] == "T-999999"
    assert report["ok"] is True
