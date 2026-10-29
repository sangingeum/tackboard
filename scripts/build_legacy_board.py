#!/usr/bin/env python3
"""Build a small board at a released schema version, for the migration matrix.

Version 2 is the current layout with a memo revision. Version 1 is the same
board with the memo table and its triggers removed and ``user_version`` set
back to 1, so ``tack migrate`` genuinely runs the 1 -> 2 migration (creating
the memo table) rather than a no-op. Boards stay tiny: a few tasks, one edge
and one typed note.

Usage:
    python scripts/build_legacy_board.py --version 1 --db /tmp/legacy-v1.sqlite3
    python scripts/build_legacy_board.py --version 2 --db /tmp/legacy-v2.sqlite3
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from tackboard import dependencies, memo, schema, service  # noqa: E402


def _build(path: Path, version: int, tasks: int) -> dict[str, object]:
    for suffix in ("", "-wal", "-shm"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)
    schema.init_database(path, label=f"legacy-v{version}")
    conn = schema.open_database(path)
    try:
        created: list[str] = []
        for index in range(tasks):
            task = service.create_task(
                conn,
                title=f"legacy task {index + 1}",
                actor="builder",
                status="ready",
            )["task"]
            created.append(str(task["id"]))
        if len(created) > 1:
            dependencies.add_dependency(conn, created[1], created[0], actor="builder")
        service.add_note(
            conn,
            created[0],
            "legacy note",
            actor="builder",
            refs=["commit:abc1234", f"task:{created[0]}"],
        )
        if version == 2:
            memo.replace_memo(conn, text="legacy board memo", actor="builder", reason="fixture")
        if version == 1:
            # Removing the memo surface is what makes the v1 -> v2 migration
            # do real work; the append-only triggers must go first.
            conn.execute("DROP TRIGGER IF EXISTS memo_revisions_no_update")
            conn.execute("DROP TRIGGER IF EXISTS memo_revisions_no_delete")
            conn.execute("DROP TABLE IF EXISTS memo_revisions")
            conn.execute("PRAGMA user_version=1")
    finally:
        conn.close()
    return {"database": str(path), "version": version, "tasks": tasks}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", type=int, choices=(1, 2), required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--tasks", type=int, default=3)
    args = parser.parse_args(argv)
    print(json.dumps(_build(args.db, args.version, args.tasks)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
