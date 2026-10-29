"""Optional MCP adapter for the ``tack`` board CLI.

The tool layer (:mod:`tackboard_mcp.tools`) has no MCP dependency and can be
used directly; :mod:`tackboard_mcp.server` is the thin MCP binding and imports
the framework only when it is asked to build a server.
"""

from __future__ import annotations

from .tools import Document, TackCli, TackCommandError

__version__ = "0.1.0"

__all__ = ["Document", "TackCli", "TackCommandError", "__version__"]
