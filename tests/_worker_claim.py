"""Subprocess worker: try to claim one task in a shared database.

Run as ``python _worker_claim.py <db> <task-id> <agent>``. Prints one JSON
object on stdout and exits with the domain exit code, so the parent test can
assert exactly one winner in a real multi-process race (spec section 16).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tackboard import claims, schema  # noqa: E402
from tackboard.errors import TackError  # noqa: E402


def main() -> int:
    db_path, task_id, agent = sys.argv[1], sys.argv[2], sys.argv[3]
    conn = schema.open_database(Path(db_path))
    try:
        result = claims.claim(conn, task_id, actor=agent)
        claim = claims.active_claim(conn, task_id)
        payload = {
            "agent": agent,
            "claimed": True,
            "changed": result["changed"],
            "claimed_by": None if claim is None else claim["agent_id"],
        }
        sys.stdout.write(json.dumps(payload))
        return 0
    except TackError as exc:
        sys.stdout.write(
            json.dumps(
                {
                    "agent": agent,
                    "claimed": False,
                    "code": exc.code,
                    "exit_code": exc.exit_code,
                    "details": exc.details,
                }
            )
        )
        return exc.exit_code
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
