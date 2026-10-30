"""Compatibility wrapper for ``scripts/build_docs.py``.

The guide sources now live in ``docs/guide/``; this module remains so that
existing invocations keep working. New code should call
``scripts/build_docs.py`` directly.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_docs

#: Backwards-compatible alias for the old entry point.
build_text = build_docs.render


def main(argv: list[str] | None = None) -> int:
    return build_docs.main(argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
