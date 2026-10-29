"""The MCP binding: one tool per board operation, over the CLI.

This module is the only place the MCP framework is imported, and only inside
:func:`build_server` — so importing :mod:`tackboard_mcp` or its tool layer never
requires the framework, and the core ``tackboard`` package never depends on it.

Every tool returns the CLI's JSON document unchanged. A failed call raises, and
the framework turns that into a tool error whose text is the CLI's own message.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .tools import TackCli

if TYPE_CHECKING:  # pragma: no cover - typing only, the framework is optional
    from mcp.server.mcpserver import MCPServer

INSTRUCTIONS = """\
This server exposes one project's tackboard through the `tack` CLI.

Read the board before acting: board_brief for counts, then next_claim to take
work atomically. A claim is a promise to every other agent — complete the task
when the work is done, and leave a note with evidence (commit ids, paths, urls)
at every handoff. `show` returns the full task including notes. `release`
without a reason gives up a claim you hold; with a reason it is the audited
force path, so use it only to recover a dead agent's claim.
"""


def _server_class() -> type[MCPServer]:
    """The framework's server class, under either release line's name.

    MCP 2.x renamed ``mcp.server.fastmcp.FastMCP`` to
    ``mcp.server.mcpserver.MCPServer``; the decorator surface this adapter uses
    is the same, so it supports both rather than pinning a client's toolchain.
    """
    try:
        from mcp.server.mcpserver import MCPServer
    except ImportError:  # pragma: no cover - 1.x
        from mcp.server.fastmcp import FastMCP as MCPServer
    return MCPServer


def build_server(cli: TackCli) -> MCPServer:
    """Build the MCP server for ``cli``'s board.

    Imported lazily: the MCP framework lives only in this package's metadata,
    so a checkout without it can still use the tool layer and its tests.
    """
    server = _server_class()("tackboard", instructions=INSTRUCTIONS)

    @server.tool()
    def next_claim(stage: str | None = None) -> dict[str, Any]:
        """Claim and return the next eligible task. `stage` may be "review"."""
        return cli.next_claim(stage)

    @server.tool()
    def show(task_id: str) -> dict[str, Any]:
        """Read one task in full, including its notes."""
        return cli.show(task_id)

    @server.tool()
    def note(task_id: str, text: str, refs: list[str] | None = None) -> dict[str, Any]:
        """Append a handoff note; refs are KIND:VALUE strings (commit/url/path/task)."""
        return cli.note(task_id, text, tuple(refs or ()))

    @server.tool()
    def complete(task_id: str) -> dict[str, Any]:
        """Finish a task, unblocking its dependents."""
        return cli.complete(task_id)

    @server.tool()
    def release(task_id: str, reason: str | None = None) -> dict[str, Any]:
        """Release a claim; a reason makes it the audited force path."""
        return cli.release(task_id, reason)

    @server.tool()
    def create(
        title: str,
        description: str | None = None,
        priority: str | None = None,
        assignee: str | None = None,
        labels: list[str] | None = None,
        depends_on: list[str] | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        """Create a task, optionally with labels, assignment and prerequisite ids."""
        return cli.create(
            title,
            description=description,
            priority=priority,
            assignee=assignee,
            labels=tuple(labels or ()),
            depends_on=tuple(depends_on or ()),
            status=status,
        )

    @server.tool()
    def board_brief() -> dict[str, Any]:
        """The board's counts only: the cheapest look at what is on the board."""
        return cli.board_brief()

    return server


def main() -> int:
    """Start the server over stdio, configured from the environment."""
    build_server(TackCli.from_environment()).run()
    return 0


if __name__ == "__main__":  # pragma: no cover - entry point
    raise SystemExit(main())
