"""The board-level memo: one standing document per project, with revisions.

A **memo** is the project's standing note — the rules of engagement, the
read-me-first premise, the thing an agent reads before it starts. It is not a
task (it can never be claimed, completed or blocked) and it is not the session
handoff narrative; it carries its own revisions so a reader can see what the
document looked like before.

Storage and safety model (see ``DESIGN.md``):

- ``memo_revisions`` is append-only, keyed by a gapless ``rev`` counter;
  ``rev`` 0 means "no memo yet" and the first write creates rev 1. A revision
  is immutable, so its stamp is ``created_at`` — never ``updated_at``.
- Every accepted write appends exactly one small board-level event inside the
  same ``BEGIN IMMEDIATE`` transaction. The event payload points at the
  revision (``rev``); it never carries the memo body.
- **Append** reads the current text inside its own write transaction, so two
  concurrent appends both survive and an append can never erase anyone's
  content. An append needs no guard. **Replace**, **restore** and **clear**
  rewrite the whole document, so they are guarded: on a non-empty memo they
  require ``--if-rev N`` or ``--force --reason``.

Nothing here contains SQL outside this module's own table, and nothing here
knows that a command line or a browser exists: the functions raise typed
exceptions and return plain dictionaries.
"""

from __future__ import annotations

import contextlib
import difflib
import sqlite3
from collections.abc import Iterator
from typing import Any

from . import history
from .config import validate_agent_id
from .db import read_transaction, write_transaction
from .errors import (
    InternalError,
    MemoTableInvalidError,
    NotFoundError,
    StaleRevError,
    UsageError,
    ValidationError,
)
from .models import MAX_MEMO_BYTES, MAX_REASON_LENGTH, utcnow_iso

#: The canonical board-level event types a memo write appends.
MEMO_REPLACED = "memo.replaced"
MEMO_APPENDED = "memo.appended"
MEMO_RESTORED = "memo.restored"
MEMO_CLEARED = "memo.cleared"

#: "No memo yet": a freshly created (or freshly migrated) board has no
#: revision rows and its current revision is 0.
NO_MEMO_REV = 0

#: A memo longer than this is collapsed by default in the web view rather than
#: truncated silently.
COLLAPSE_BYTES = 4 * 1024

_REASON_REQUIRED = (
    "A whole-document memo write needs --reason with the explanation to record."
)

_EMPTY_DOCUMENT: dict[str, Any] = {
    "rev": NO_MEMO_REV,
    "text": "",
    "author": None,
    "created_at": None,
    "bytes": 0,
}


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #


def _check_memo_text(text: str) -> str:
    """Enforce the memo cap, byte-measured, mirroring ``_check_description``."""
    if not isinstance(text, str):
        raise ValidationError("Memo text must be a string.")
    size = len(text.encode("utf-8"))
    if size > MAX_MEMO_BYTES:
        raise ValidationError(
            f"Memo is too long: maximum is {MAX_MEMO_BYTES} bytes.",
            details={
                "field": "memo",
                "max": MAX_MEMO_BYTES,
                "unit": "bytes",
                "actual": size,
            },
        )
    return text


def _check_reason(reason: str | None, *, required: bool) -> str | None:
    """The memo's ``--reason``: the recorded explanation for a write.

    The same concept and the same 1024-character cap as a task's ``--reason``;
    the memo simply requires it for the whole-document writes (replace,
    restore, clear) and treats it as optional on an append.
    """
    if reason is None or not reason.strip():
        if required:
            raise ValidationError(_REASON_REQUIRED)
        return None
    trimmed = reason.strip()
    if len(trimmed) > MAX_REASON_LENGTH:
        raise ValidationError(
            f"--reason is too long: maximum is {MAX_REASON_LENGTH} characters.",
            details={
                "field": "reason",
                "max": MAX_REASON_LENGTH,
                "unit": "characters",
                "actual": len(trimmed),
            },
        )
    return trimmed


def _clean_actor(actor: str | None) -> str:
    if actor is None:
        raise InternalError("A memo write requires an actor.")
    return validate_agent_id(actor)


def _document(row: sqlite3.Row) -> dict[str, Any]:
    """The read document for one stored revision."""
    text = str(row["text"])
    return {
        "rev": int(row["rev"]),
        "text": text,
        "author": str(row["author"]),
        "created_at": str(row["created_at"]),
        "bytes": len(text.encode("utf-8")),
    }


# --------------------------------------------------------------------------- #
# reads
# --------------------------------------------------------------------------- #


#: The one message for the "memo revision table is not usable" condition: it
#: names the observed state and the version-2 expectation, so it is true both
#: when the table is absent and when it exists with the wrong shape.
MEMO_TABLE_INVALID_MESSAGE = (
    "This board's memo revision table is not usable; schema version 2 expects "
    "a memo_revisions table with rev, text, author, created_at and reason."
)


@contextlib.contextmanager
def _memo_revision_queries(
    conn: sqlite3.Connection,
) -> Iterator[sqlite3.Connection]:
    """Map a database error raised while touching ``memo_revisions`` to one error.

    Every ``memo_revisions`` query on the memo path runs inside this guard, so a
    board whose ``user_version`` claims 2 but whose revision table is absent or
    malformed fails as one typed schema error (exit 6) — the same condition
    ``doctor`` reports as ``memo_table_invalid`` — instead of leaking a raw
    ``sqlite3.OperationalError``. The guard wraps the queries only, never
    transaction control, so a lock or busy outcome keeps its own ``LockedError``
    (exit 5, retryable).
    """
    try:
        yield conn
    except sqlite3.DatabaseError as exc:
        raise MemoTableInvalidError(MEMO_TABLE_INVALID_MESSAGE) from exc


def current_rev(conn: sqlite3.Connection) -> int:
    """The board's current memo revision (0 when there is no memo)."""
    with _memo_revision_queries(conn):
        row = conn.execute(
            "SELECT COALESCE(MAX(rev), 0) AS rev FROM memo_revisions"
        ).fetchone()
    return int(row["rev"])


def _current_text(conn: sqlite3.Connection) -> str:
    with _memo_revision_queries(conn):
        row = conn.execute(
            "SELECT text FROM memo_revisions ORDER BY rev DESC LIMIT 1"
        ).fetchone()
    return "" if row is None else str(row["text"])


def read_memo(conn: sqlite3.Connection, rev: int | None = None) -> dict[str, Any]:
    """The memo document: the head revision, or the revision named by ``rev``.

    ``rev`` 0 is always the empty document (no memo); a revision that does not
    exist is exit 3.
    """
    with read_transaction(conn):
        if rev is None:
            with _memo_revision_queries(conn):
                row = conn.execute(
                    "SELECT * FROM memo_revisions ORDER BY rev DESC LIMIT 1"
                ).fetchone()
            return dict(_EMPTY_DOCUMENT) if row is None else _document(row)
        if rev == NO_MEMO_REV:
            return dict(_EMPTY_DOCUMENT)
        with _memo_revision_queries(conn):
            row = conn.execute(
                "SELECT * FROM memo_revisions WHERE rev = ?", (rev,)
            ).fetchone()
        if row is None:
            current = current_rev(conn)
            raise NotFoundError(
                f"Memo revision {rev} not found; the current revision is "
                f"{current}.",
                details={"rev": rev, "current": current},
            )
        return _document(row)


def memo_history(
    conn: sqlite3.Connection, *, limit: int | None = None
) -> dict[str, Any]:
    """The memo's revisions, newest first, with the head revision named."""
    if limit is not None and limit < 0:
        raise ValidationError("--limit must not be negative.")
    sql = "SELECT * FROM memo_revisions ORDER BY rev DESC"
    params: list[Any] = []
    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))
    with read_transaction(conn):
        with _memo_revision_queries(conn):
            rows = conn.execute(sql, params).fetchall()
        current = current_rev(conn)
    return {
        "current_rev": current,
        "items": [
            {
                "rev": int(row["rev"]),
                "author": str(row["author"]),
                "created_at": str(row["created_at"]),
                "bytes": len(str(row["text"]).encode("utf-8")),
                "reason": row["reason"],
            }
            for row in rows
        ],
    }


def _text_at(conn: sqlite3.Connection, rev: int) -> str:
    """The text of one revision; rev 0 is the empty document."""
    if rev == NO_MEMO_REV:
        return ""
    with _memo_revision_queries(conn):
        row = conn.execute(
            "SELECT text FROM memo_revisions WHERE rev = ?", (rev,)
        ).fetchone()
    if row is None:
        raise NotFoundError(
            f"Memo revision {rev} not found.",
            details={"rev": rev, "current": current_rev(conn)},
        )
    return str(row["text"])


def _unified_diff(
    a_text: str, b_text: str, a_rev: int, b_rev: int
) -> tuple[str, int, int]:
    """One line-based unified diff with 3 context lines.

    The same computation yields the exact text a human sees and the machine
    line counts, so the human rendering is never a second source of truth.
    """
    lines = list(
        difflib.unified_diff(
            a_text.splitlines(),
            b_text.splitlines(),
            fromfile=f"rev {a_rev}",
            tofile=f"rev {b_rev}",
            lineterm="",
        )
    )
    added = sum(1 for line in lines if line[:1] == "+" and line[:3] != "+++")
    removed = sum(1 for line in lines if line[:1] == "-" and line[:3] != "---")
    return "\n".join(lines), added, removed


def diff_memos(
    conn: sqlite3.Connection, a: int, b: int | None = None
) -> dict[str, Any]:
    """Diff revision ``a`` to revision ``b`` (``b`` None means the head).

    Revision 0 is the empty document, so ``--diff 0 1`` is "everything added".
    A revision outside ``0..current`` is exit 3.
    """
    with read_transaction(conn):
        current = current_rev(conn)
        for rev in (a, b):
            if rev is not None and (rev < 0 or rev > current):
                raise NotFoundError(
                    f"Memo revision {rev} not found; revisions run 0..{current}.",
                    details={"rev": rev, "current": current},
                )
        to_rev = current if b is None else b
        a_text = _text_at(conn, a)
        b_text = _text_at(conn, to_rev)
    unified, added, removed = _unified_diff(a_text, b_text, a, to_rev)
    return {
        "from": a,
        "to": to_rev,
        "added": added,
        "removed": removed,
        "unified": unified,
    }


# --------------------------------------------------------------------------- #
# writes
# --------------------------------------------------------------------------- #


def _guard(
    conn: sqlite3.Connection, *, if_rev: int | None, force: bool, what: str
) -> int:
    """Apply the whole-document guard; return the current revision.

    A whole-document write on a non-empty memo needs ``--if-rev N`` or
    ``--force --reason``. The first creation (rev 0) is exempt: there is
    nothing to clobber.
    """
    current = current_rev(conn)
    if current != NO_MEMO_REV and if_rev is None and not force:
        raise UsageError(
            f"A memo {what} needs --if-rev N or --force --reason: the memo "
            f"already has revision {current}."
        )
    if if_rev is not None and if_rev != current:
        raise StaleRevError(
            f"Stale memo revision: expected {if_rev}, current {current}.",
            details={"expected": if_rev, "current": current},
        )
    return current


def _transaction(
    conn: sqlite3.Connection, *, dry_run: bool
) -> contextlib.AbstractContextManager[sqlite3.Connection]:
    """A write transaction, or nothing at all for ``--dry-run``.

    A dry run never opens a write transaction and never appends an event.
    """
    if dry_run:
        return contextlib.nullcontext(conn)
    return write_transaction(conn)


def _predicted(
    conn: sqlite3.Connection, text: str, actor: str | None
) -> dict[str, Any]:
    """The revision a write *would* create, for ``--dry-run``."""
    return {
        "rev": current_rev(conn) + 1,
        "text": text,
        "author": actor,
        "created_at": utcnow_iso(),
        "bytes": len(text.encode("utf-8")),
    }


def _insert_revision(
    conn: sqlite3.Connection,
    *,
    actor: str,
    text: str,
    reason: str | None,
    event_type: str,
    forced: bool,
) -> dict[str, Any]:
    """Append one revision and its audit event in the caller's transaction."""
    previous_rev = current_rev(conn)
    new_rev = previous_rev + 1
    created_at = utcnow_iso()
    size = len(text.encode("utf-8"))
    with _memo_revision_queries(conn):
        conn.execute(
            "INSERT INTO memo_revisions(rev, text, author, created_at, reason) "
            "VALUES(?, ?, ?, ?, ?)",
            (new_rev, text, actor, created_at, reason),
        )
    payload: dict[str, Any] = {"rev": new_rev, "bytes": size}
    if reason is not None:
        payload["reason"] = reason
    if forced:
        payload["forced"] = True
        payload["previous_rev"] = previous_rev
    history.record_event(
        conn,
        event_type=event_type,
        actor=actor,
        task_id=None,
        payload=payload,
    )
    return {
        "changed": True,
        "memo": {
            "rev": new_rev,
            "text": text,
            "author": actor,
            "created_at": created_at,
            "bytes": size,
        },
    }


def _whole_document_write(
    conn: sqlite3.Connection,
    *,
    actor: str | None,
    text: str,
    reason: str | None,
    if_rev: int | None,
    force: bool,
    dry_run: bool,
    event_type: str,
    what: str,
) -> dict[str, Any]:
    """Rewrite the memo as ``text`` (the path ``--restore`` and ``--clear`` use)."""
    body = _check_memo_text(text)
    with _transaction(conn, dry_run=dry_run):
        _guard(conn, if_rev=if_rev, force=force, what=what)
        clean_reason = _check_reason(reason, required=True)
        if dry_run:
            return {
                "changed": body != _current_text(conn),
                "dry_run": True,
                "memo": _predicted(conn, body, actor),
            }
        clean_actor = _clean_actor(actor)
        if body == _current_text(conn):
            return {"changed": False, "memo": read_memo(conn)}
        return _insert_revision(
            conn,
            actor=clean_actor,
            text=body,
            reason=clean_reason,
            event_type=event_type,
            forced=force,
        )


def replace_memo(
    conn: sqlite3.Connection,
    *,
    actor: str | None,
    text: str,
    reason: str | None = None,
    if_rev: int | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Replace the whole memo with ``text`` (guarded; ``--reason`` required)."""
    if not isinstance(text, str):
        raise ValidationError("Memo text must be a string.")
    if not text.strip():
        raise UsageError(
            "Memo text must not be empty; use 'tack memo --clear --reason ...' "
            "to clear the memo."
        )
    return _whole_document_write(
        conn,
        actor=actor,
        text=text,
        reason=reason,
        if_rev=if_rev,
        force=force,
        dry_run=dry_run,
        event_type=MEMO_REPLACED,
        what="replacement",
    )


def append_memo(
    conn: sqlite3.Connection,
    *,
    actor: str | None,
    text: str,
    reason: str | None = None,
    if_rev: int | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Append ``text`` to the memo.

    The current text is read **inside** the write transaction, so concurrent
    appends serialise and never lose each other's content — which is why an
    append takes no guard. If the current memo is non-empty and does not end
    with a newline, one ``\\n`` is inserted before the appended text; nothing
    else about either part is transformed.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValidationError("Appended memo text must not be empty.")
    if force:
        raise UsageError(
            "--force does not apply to 'tack memo --append': an append is "
            "never guarded, so there is nothing to force."
        )
    with _transaction(conn, dry_run=dry_run):
        current = current_rev(conn)
        if if_rev is not None and if_rev != current:
            raise StaleRevError(
                f"Stale memo revision: expected {if_rev}, current {current}.",
                details={"expected": if_rev, "current": current},
            )
        existing = _current_text(conn)
        if existing and not existing.endswith("\n"):
            joined = f"{existing}\n{text}"
        else:
            joined = f"{existing}{text}"
        body = _check_memo_text(joined)
        clean_reason = _check_reason(reason, required=False)
        if dry_run:
            return {
                "changed": body != existing,
                "dry_run": True,
                "memo": _predicted(conn, body, actor),
            }
        clean_actor = _clean_actor(actor)
        if body == existing:
            return {"changed": False, "memo": read_memo(conn)}
        return _insert_revision(
            conn,
            actor=clean_actor,
            text=body,
            reason=clean_reason,
            event_type=MEMO_APPENDED,
            forced=False,
        )


def restore_memo(
    conn: sqlite3.Connection,
    *,
    actor: str | None,
    rev: int,
    reason: str | None = None,
    if_rev: int | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Restore an earlier revision's text as a **new** revision.

    Restoring the head's own text is an idempotent no-op. ``--restore 0`` is not
    a thing — clearing is ``--clear``.
    """
    with read_transaction(conn):
        current = current_rev(conn)
        if rev < 1 or rev > current:
            raise NotFoundError(
                f"Memo revision {rev} not found; revisions run 1..{current}.",
                details={"rev": rev, "current": current},
            )
        # Revisions are immutable, so reading the source text before the write
        # transaction cannot race with anything.
        source_text = _text_at(conn, rev)
    return _whole_document_write(
        conn,
        actor=actor,
        text=source_text,
        reason=reason,
        if_rev=if_rev,
        force=force,
        dry_run=dry_run,
        event_type=MEMO_RESTORED,
        what="restore",
    )


def clear_memo(
    conn: sqlite3.Connection,
    *,
    actor: str | None,
    reason: str | None = None,
    if_rev: int | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Clear the memo by writing an empty revision (history stays linear)."""
    return _whole_document_write(
        conn,
        actor=actor,
        text="",
        reason=reason,
        if_rev=if_rev,
        force=force,
        dry_run=dry_run,
        event_type=MEMO_CLEARED,
        what="clear",
    )


__all__ = [
    "COLLAPSE_BYTES",
    "MEMO_APPENDED",
    "MEMO_CLEARED",
    "MEMO_REPLACED",
    "MEMO_RESTORED",
    "NO_MEMO_REV",
    "append_memo",
    "clear_memo",
    "current_rev",
    "diff_memos",
    "memo_history",
    "read_memo",
    "replace_memo",
    "restore_memo",
]
