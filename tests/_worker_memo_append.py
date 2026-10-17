"""Subprocess worker: append text to the board memo.

Run as ``python _worker_memo_append.py <db> <agent> <text>``. Prints one JSON
object on stdout and exits with the domain exit code, so a parent test can race
real OS processes against one board and assert that both appends survive
(spec section 16: concurrency is tested with processes, not threads).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tackboard import memo, schema  # noqa: E402
from tackboard.errors import TackError  # noqa: E402


def main() -> int:
    db_path, agent, text = sys.argv[1], sys.argv[2], sys.argv[3]
    conn = schema.open_database(Path(db_path))
    try:
        result = memo.append_memo(conn, actor=agent, text=text)
        sys.stdout.write(
            json.dumps(
                {
                    "agent": agent,
                    "changed": result["changed"],
                    "rev": result["memo"]["rev"],
                }
            )
        )
        return 0
    except TackError as exc:
        sys.stdout.write(
            json.dumps(
                {
                    "agent": agent,
                    "changed": False,
                    "code": exc.code,
                    "exit_code": exc.exit_code,
                }
            )
        )
        return exc.exit_code
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
