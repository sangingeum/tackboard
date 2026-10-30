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

Read the memo with memo_read, then the board with board_brief and list; take
work with next_claim or claim. A claim is a promise to every other agent —
complete the task when the work is done, and leave a note with evidence (commit
ids, paths, urls) at every handoff. `show` returns the full task including
notes. Reviewers submit with status(task_id, "review"), then reject with a
mandatory reason to send work back; the claim returns to the author. `release`
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
        from mcp.server.mcpserver import MCPServer as _MCPServer
    except ImportError:  # pragma: no cover - 1.x
        from mcp.server.fastmcp import FastMCP as _MCPServer  # type: ignore[attr-defined,no-redef]
    return _MCPServer


def build_server(cli: TackCli) -> MCPServer:
    """Build the MCP server for ``cli``'s board.

    Imported lazily: the MCP framework lives only in this package's metadata,
    so a checkout without it can still use the tool layer and its tests.
    """
    from mcp.types import ToolAnnotations

    server = _server_class()("tackboard", instructions=INSTRUCTIONS)

    @server.tool(
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=False,
        )
    )
    def next_claim(stage: str | None = None) -> dict[str, Any]:
        """Claim and return the next eligible task. `stage` may be "review"."""
        return cli.next_claim(stage)

    @server.tool(
        annotations=ToolAnnotations(
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        )
    )
    def show(task_id: str) -> dict[str, Any]:
        """Read one task in full, including its notes."""
        return cli.show(task_id)

    @server.tool(
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=False,
        )
    )
    def note(task_id: str, text: str, refs: list[str] | None = None) -> dict[str, Any]:
        """Append a handoff note; refs are KIND:VALUE strings (commit/url/path/task)."""
        return cli.note(task_id, text, tuple(refs or ()))

    @server.tool(
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        )
    )
    def complete(task_id: str) -> dict[str, Any]:
        """Finish a task, unblocking its dependents."""
        return cli.complete(task_id)

    @server.tool(
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=False,
        )
    )
    def release(task_id: str, reason: str | None = None) -> dict[str, Any]:
        """Release a claim; a reason makes it the audited force path."""
        return cli.release(task_id, reason)

    @server.tool(
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=False,
        )
    )
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

    @server.tool(
        annotations=ToolAnnotations(
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        )
    )
    def board_brief() -> dict[str, Any]:
        """The board's counts only: the cheapest look at what is on the board."""
        return cli.board_brief()

    @server.tool(
        annotations=ToolAnnotations(
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        )
    )
    def memo_read(rev: int | None = None) -> dict[str, Any]:
        """Read the project memo before starting; `rev` selects one revision."""
        return cli.memo_read(rev)

    @server.tool(
        name="list",
        annotations=ToolAnnotations(
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    def list_tasks(
        status: str | None = None,
        assignee: str | None = None,
        label: str | None = None,
        priority: str | None = None,
        blocked: bool = False,
        eligible: bool = False,
        limit: int | None = None,
        full: bool = False,
    ) -> dict[str, Any]:
        """List tasks with the CLI's filters; descriptions only with `full`."""
        return cli.list_tasks(
            status=status,
            assignee=assignee,
            label=label,
            priority=priority,
            blocked=blocked,
            eligible=eligible,
            limit=limit,
            full=full,
        )

    @server.tool(
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        )
    )
    def claim(task_id: str, force: bool = False, reason: str | None = None) -> dict[str, Any]:
        """Claim one task; `force` requires an audited non-empty reason."""
        return cli.claim(task_id, force=force, reason=reason)

    @server.tool(
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=False,
        )
    )
    def status(
        task_id: str,
        status: str,
        force: bool = False,
        reason: str | None = None,
        if_version: int | None = None,
    ) -> dict[str, Any]:
        """Move a task to another status; `force` requires a reason."""
        return cli.status(
            task_id, status, force=force, reason=reason, if_version=if_version
        )

    @server.tool(
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=False,
        )
    )
    def reject(task_id: str, reason: str, if_version: int | None = None) -> dict[str, Any]:
        """Send a review task back to in_progress; the reason is mandatory."""
        return cli.reject(task_id, reason, if_version)

    return server


def main() -> int:
    """Start the server over stdio, configured from the environment."""
    build_server(TackCli.from_environment()).run()
    return 0


if __name__ == "__main__":  # pragma: no cover - entry point
    raise SystemExit(main())
