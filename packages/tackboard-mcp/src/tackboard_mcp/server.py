"""The MCP binding: one tool per board operation, over the CLI.

This module is the only place the MCP framework is imported: the server class
inside :func:`build_server` and the error type inside :func:`_fail`, so
importing :mod:`tackboard_mcp` or its tool layer never requires the framework,
and the core ``tackboard`` package never depends on it.

Every tool returns the CLI's JSON document unchanged. A failed call raises, and
the framework turns that into a tool error whose text is the CLI's own message.
The two exceptions are ``board_info`` and ``board_attach``: no single CLI call
answers them, so they return adapter-owned documents (both carry a ``document``
key).
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, NoReturn

from .tools import TackCli, TackCommandError

if TYPE_CHECKING:  # pragma: no cover - typing only, the framework is optional
    from mcp.server.mcpserver import MCPServer

#: Where the session's board came from. ``none`` only appears in ``board_info``
#: output, when no board is resolvable at all.
Source = Literal["env", "discovery", "none", "attach"]

ADMIN_FLAG = "--allow-board-admin"
ADMIN_ENV = "TACKBOARD_MCP_ALLOW_ADMIN"

INSTRUCTIONS = """\
This server exposes one project's tackboard through the `tack` CLI.

Call board_info first: it reports which board this process serves, its path and
whether the file exists. If no board is attached, call board_create to make one
at an absolute path, or board_attach to switch to an existing board. Both are
administrative tools and are available only when the server was started with
--allow-board-admin (or TACKBOARD_MCP_ALLOW_ADMIN=1). board_attach changes the
board for every later call in this process; it is not per-client and not
per-call.

Read the memo with memo_read, then the board with board_brief and list; take
work with next_claim or claim. A claim is a promise to every other agent —
complete the task when the work is done, and leave a note with evidence (commit
ids, paths, urls) at every handoff. `show` returns the full task including
notes. Reviewers submit with status(task_id, "review"), then reject with a
mandatory reason to send work back; the claim returns to the author. `release`
without a reason gives up a claim you hold; with a reason it is the audited
force path, so use it only to recover a dead agent's claim.
"""


@dataclass
class BoardSession:
    """The one board this process serves, plus the start-up policy.

    ``source`` records how the board was chosen: ``env`` (``TACKBOARD_DB`` at
    start-up), ``discovery`` (no explicit db; the CLI resolves
    ``.tackboard.toml`` from the server's working directory), ``attach`` (set by
    ``board_attach``), or ``none`` (nothing resolvable). ``allow_admin`` is read
    once at start-up and never changes.
    """

    cli: TackCli
    source: Source
    allow_admin: bool = False


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


def _fail(message: str) -> NoReturn:
    """Raise the framework's anticipated tool error, imported lazily."""
    from mcp.server.mcpserver.exceptions import ToolError

    raise ToolError(message)


def _document_text(document: dict[str, Any]) -> str:
    return json.dumps(document, sort_keys=True)


def _error_document(code: str, message: str, details: dict[str, Any]) -> dict[str, Any]:
    """An adapter-owned error document, shaped like the CLI's own."""
    return {
        "schema_version": 1,
        "error": {
            "code": code,
            "message": message,
            "exit_code": 2,
            "retryable": False,
            "details": details,
        },
    }


def _raise_cli_error(exc: TackCommandError) -> NoReturn:
    """Surface the CLI's own error document, with a hint when no board exists."""
    document = exc.document
    if document is None:
        _fail(str(exc))
    error = document.get("error")
    if not isinstance(error, dict):
        _fail(_document_text(document))
    message = str(error.get("message") or str(exc))
    text = f"{message}\n{_document_text(document)}"
    if error.get("code") == "not_found":
        text += (
            "\nNo board is attached. Call board_info to inspect the session; "
            "if the board file exists, call board_attach with its path, "
            "otherwise call board_create and then board_attach."
        )
    _fail(text)


def _call_cli(operation: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """Run one CLI operation, surfacing its error document to the client.

    Every tool body goes through here: a ``TackCommandError`` becomes the
    framework's ``ToolError`` carrying the CLI's own message and document, so
    the client never sees the generic ``Error executing tool <name>``.
    """
    try:
        return operation()
    except TackCommandError as exc:
        _raise_cli_error(exc)


def _require_admin(session: BoardSession) -> None:
    if session.allow_admin:
        return
    document = _error_document(
        "board_admin_disabled",
        "Board administration is disabled. Start the server with "
        f"{ADMIN_FLAG} or set {ADMIN_ENV}=1.",
        {"flag": ADMIN_FLAG},
    )
    _fail(_document_text(document))


def _require_absolute(path: str, tool: str) -> Path:
    target = Path(path)
    if not target.is_absolute():
        document = _error_document(
            "board_path_invalid",
            f"{tool} requires an absolute path; got {path!r}.",
            {"path": path},
        )
        _fail(_document_text(document))
    return target


def _display_path(raw: object) -> str | None:
    if not isinstance(raw, str) or not raw:
        return None
    return str(Path(raw).expanduser().resolve())


def _info_document(
    *,
    attached: bool,
    configured: bool,
    path: str | None,
    source: Source,
    project: str | None,
    warnings: list[str],
) -> dict[str, Any]:
    display = _display_path(path)
    return {
        "document": "board_info",
        "schema_version": 1,
        "attached": attached,
        "configured": configured,
        "path": display,
        "exists": Path(display).is_file() if display is not None else None,
        "source": source,
        "project": project,
        "warnings": warnings,
    }


def board_info(session: BoardSession) -> dict[str, Any]:
    """Report the board this process serves, without changing it.

    A missing or unconfigured board is a warning, not an error: the point of
    this tool is to tell the client what is wrong. A board that exists but
    cannot be read (corrupt, foreign, permissions) is raised with the CLI's
    document, because ``attached`` cannot be determined.
    """
    configured = session.cli.db is not None
    try:
        document = session.cli.board_brief()
    except TackCommandError as exc:
        return _board_info_failure(session, exc, configured)

    raw_path = str(session.cli.db) if session.cli.db is not None else document.get("database")
    warnings: list[str] = []
    if session.source == "discovery":
        warnings.append(
            "board discovered from the server's working directory; "
            "set TACKBOARD_DB to pin it"
        )
    return _info_document(
        attached=True,
        configured=configured,
        path=raw_path if isinstance(raw_path, str) else None,
        source=session.source,
        project=document.get("project"),
        warnings=warnings,
    )


def _board_info_failure(
    session: BoardSession, exc: TackCommandError, configured: bool
) -> dict[str, Any]:
    document = exc.document
    if document is None:
        _fail(str(exc))
    error = document.get("error")
    if not isinstance(error, dict):
        _fail(_document_text(document))
    code = error.get("code")
    message = str(error.get("message") or "")

    def report(source: Source, path: str | None, warning: str) -> dict[str, Any]:
        return _info_document(
            attached=False,
            configured=configured,
            path=path,
            source=source,
            project=None,
            warnings=[warning],
        )

    if code == "not_found" and "No project database configured" in message:
        return report("none", None, message)
    if code == "validation" and "Cannot parse" in message:
        return report("none", None, f"malformed .tackboard.toml: {message}")
    if code == "not_found" and "No database configured in" in message:
        return report(
            "none", None, f"no database configured in .tackboard.toml: {message}"
        )
    if code == "not_found" and "not found" in message:
        if configured and session.cli.db is not None:
            return report(
                session.source,
                str(session.cli.db),
                f"configured board file does not exist: {message}",
            )
        # Discovery mode: .tackboard.toml names a file that is not there. The
        # CLI has no structured path to report, so the warning carries it.
        return report("discovery", None, message)
    _fail(f"{message}\n{_document_text(document)}")


def board_create(session: BoardSession, path: str) -> dict[str, Any]:
    """Create a board at an absolute path; the active board is unchanged.

    Delegates to ``tack init --db PATH --json`` and returns that document
    unchanged. Missing parent directories are created by the CLI. The new
    board is not attached: call ``board_attach`` with the same path to use it.
    """
    _require_admin(session)
    target = _require_absolute(path, "board_create")
    candidate = replace(session.cli, db=target)
    try:
        return candidate.init()
    except TackCommandError as exc:
        _raise_cli_error(exc)


def board_attach(session: BoardSession, path: str) -> dict[str, Any]:
    """Validate a board and make it the one this process serves.

    Both validation calls (``doctor``, then ``board --brief``) run against the
    candidate before the swap, so a failure leaves the old board active.
    """
    _require_admin(session)
    target = _require_absolute(path, "board_attach")
    if not target.is_file():
        document = _error_document(
            "board_path_invalid",
            f"board_attach requires an existing board file; {path!r} is not a file.",
            {"path": path},
        )
        _fail(_document_text(document))

    resolved = target.expanduser().resolve()
    candidate = replace(session.cli, db=target)
    try:
        doctor = candidate.doctor()
        brief = candidate.board_brief()
    except TackCommandError as exc:
        failure = exc.document
        if failure is not None and "ok" in failure:
            # A doctor document is not an error envelope; raise it unchanged.
            _fail(_document_text(failure))
        _raise_cli_error(exc)
    if not doctor.get("ok"):
        _fail(_document_text(doctor))

    already_active = (
        session.cli.db is not None
        and Path(session.cli.db).expanduser().resolve() == resolved
    )
    session.cli = candidate
    if not already_active:
        session.source = "attach"
    return {
        "document": "board_attach",
        "schema_version": 1,
        "attached": True,
        "board": {"path": str(resolved), "project": brief.get("project")},
        "warnings": doctor.get("warnings", []),
    }


def build_server(session: BoardSession) -> MCPServer:
    """Build the MCP server for ``session``'s board.

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
        return _call_cli(lambda: session.cli.next_claim(stage))

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
        return _call_cli(lambda: session.cli.show(task_id))

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
        return _call_cli(lambda: session.cli.note(task_id, text, tuple(refs or ())))

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
        return _call_cli(lambda: session.cli.complete(task_id))

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
        return _call_cli(lambda: session.cli.release(task_id, reason))

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
        return _call_cli(
            lambda: session.cli.create(
                title,
                description=description,
                priority=priority,
                assignee=assignee,
                labels=tuple(labels or ()),
                depends_on=tuple(depends_on or ()),
                status=status,
            )
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
        return _call_cli(session.cli.board_brief)

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
        return _call_cli(lambda: session.cli.memo_read(rev))

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
        return _call_cli(
            lambda: session.cli.list_tasks(
                status=status,
                assignee=assignee,
                label=label,
                priority=priority,
                blocked=blocked,
                eligible=eligible,
                limit=limit,
                full=full,
            )
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
        return _call_cli(lambda: session.cli.claim(task_id, force=force, reason=reason))

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
        return _call_cli(
            lambda: session.cli.status(
                task_id, status, force=force, reason=reason, if_version=if_version
            )
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
        return _call_cli(lambda: session.cli.reject(task_id, reason, if_version))

    @server.tool(
        name="board_info",
        annotations=ToolAnnotations(
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    def board_info_tool() -> dict[str, Any]:
        """Report which board this server serves, its path and whether it exists."""
        return board_info(session)

    @server.tool(
        name="board_create",
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=False,
        ),
    )
    def board_create_tool(path: str) -> dict[str, Any]:
        """Create a board at an absolute path; call board_attach to use it."""
        return board_create(session, path)

    @server.tool(
        name="board_attach",
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    def board_attach_tool(path: str) -> dict[str, Any]:
        """Switch this process to an existing board; every later call uses it."""
        return board_attach(session, path)

    return server


def _env_flag(value: str | None) -> bool:
    """Strict start-up parse: only ``1``, ``true`` and ``yes`` enable."""
    return value is not None and value.strip().lower() in {"1", "true", "yes"}


def main(argv: Sequence[str] | None = None) -> int:
    """Start the server over stdio, configured from the environment."""
    parser = argparse.ArgumentParser(
        prog="tackboard-mcp",
        description="Expose one tackboard project to an MCP client over stdio.",
    )
    parser.add_argument(
        ADMIN_FLAG,
        action="store_true",
        help=(
            "enable board_create and board_attach; also enabled by "
            f"{ADMIN_ENV}=1"
        ),
    )
    args = parser.parse_args(argv)
    cli = TackCli.from_environment()
    session = BoardSession(
        cli=cli,
        source="env" if cli.db is not None else "discovery",
        allow_admin=args.allow_board_admin or _env_flag(os.environ.get(ADMIN_ENV)),
    )
    build_server(session).run()
    return 0


if __name__ == "__main__":  # pragma: no cover - entry point
    raise SystemExit(main())
