"""Subprocess worker: take the next claimable task in a shared database.

Run as ``python _worker_next.py <db> <agent>``. Prints the claimed task ID as
JSON so racing workers can be checked for duplicates (spec section 16).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tackboard import claims, schema  # noqa: E402


def main() -> int:
    db_path, agent = sys.argv[1], sys.argv[2]
    conn = schema.open_database(Path(db_path))
    try:
        result = claims.next_task(conn, actor=agent, claim=True)
        task = result["task"]
        sys.stdout.write(
            json.dumps({"agent": agent, "task_id": None if task is None else task["id"]})
        )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
