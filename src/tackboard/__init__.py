"""tackboard — a project-scoped task board for coordinating multiple AI agents.

One SQLite database per project, a stable CLI as the primary interface, and a
read-only web view for the human operator. The public surface is the ``tack``
command; see ``README.md`` for the CLI contract and ``DESIGN.md`` for the
architecture.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
