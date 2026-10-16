"""tackboard — a project-scoped task board for coordinating multiple AI agents.

One SQLite database per project, a stable CLI as the primary interface, and a
read-only web view for the human operator. The public surface is the ``tack``
command; see ``README.md`` for the CLI contract and ``DESIGN.md`` for the
architecture.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _distribution_version

try:
    # The release number has exactly one hand-written copy: ``pyproject.toml``,
    # whose value the build stamps into the installed distribution metadata.
    # Reading it back here means ``tack --version`` cannot drift from the
    # packaged version the way it did at 0.1.1, where this module still said
    # 0.1.0 after the metadata had moved to 0.1.1.
    __version__ = _distribution_version("tackboard")
except PackageNotFoundError:
    # No installed distribution metadata — a source tree run without an
    # install (for example ``python src/tackboard/...`` outside ``uv run``).
    # The sentinel is deliberately not a plausible release number, so a real
    # version never masquerades as this fallback.
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
