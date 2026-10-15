"""``python -m tackboard`` — run the ``tack`` command-line interface."""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
