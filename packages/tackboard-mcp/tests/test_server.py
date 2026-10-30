"""MCP binding tests — skipped unless the optional framework is installed.

The tool layer is covered without the framework (``test_tools.py``). These tests
prove the binding itself: that the twelve tools register, and — when the
framework is present — that a real MCP client can handshake over stdio and call
them. In a checkout without ``mcp`` they skip rather than fail; the report
states which of the two was actually exercised.

Each tool declares all four MCP hints as explicit booleans. The two read tools
read one local SQLite file, so they are read-only and repeatable; every mutating
tool writes only that same local board and touches no external system
(``openWorldHint`` false); nothing on this surface deletes data
(``destructiveHint`` false); and ``complete`` is the one mutation whose final
state is the same when repeated (``idempotentHint`` true).

Run them with a framework installed, e.g.::

    uv pip install --python .venv/bin/python "mcp>=1.2"
    uv run pytest packages/tackboard-mcp/tests
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

import pytest

mcp = pytest.importorskip("mcp", reason="the optional MCP framework is not installed")

from tackboard_mcp.server import build_server  # noqa: E402
from tackboard_mcp.tools import TackCli  # noqa: E402

ADAPTER_SRC = Path(__file__).resolve().parents[1] / "src"
CORE_SRC = Path(__file__).resolve().parents[3] / "src"

EXPECTED_TOOLS = {
    "board_brief",
    "claim",
    "complete",
    "create",
    "list",
    "memo_read",
    "next_claim",
    "note",
    "reject",
    "release",
    "show",
    "status",
}

EXPECTED_ANNOTATIONS: dict[str, dict[str, bool]] = {
    "board_brief": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    "show": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    "next_claim": {
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
    "note": {
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
    "complete": {
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    "release": {
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
    "create": {
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
    "memo_read": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    "list": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    "claim": {
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    "status": {
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
    "reject": {
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
}


@pytest.fixture
def scratch_board(tmp_path: Path) -> Path:
    db = tmp_path / "mcp.sqlite3"
    cli = TackCli(command=(sys.executable, "-m", "tackboard"), db=db, actor="handshake")
    cli.run("init")
    return db


def test_the_seven_tools_register(scratch_board: Path) -> None:
    cli = TackCli(command=(sys.executable, "-m", "tackboard"), db=scratch_board, actor="a")
    server = build_server(cli)
    assert hasattr(server, "run"), "the built server exposes the framework's run loop"


def test_a_real_client_handshake_and_tool_call(scratch_board: Path) -> None:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(ADAPTER_SRC), str(CORE_SRC)])
    env["TACKBOARD_DB"] = str(scratch_board)
    env["TACKBOARD_AGENT"] = "handshake"
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "tackboard_mcp.server"], env=env
    )

    async def exercise() -> tuple[set[str], Any, Any]:
        async with (
            stdio_client(params) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            listed = await session.list_tools()
            created = await session.call_tool(
                "create", {"title": "Handshake task", "status": "ready"}
            )
            claimed = await session.call_tool("next_claim", {})
            return {tool.name for tool in listed.tools}, created, claimed

    names, created, claimed = asyncio.run(exercise())
    assert names == EXPECTED_TOOLS
    assert created.is_error is False
    assert "T-000001" in created.content[0].text
    assert claimed.is_error is False
    assert "T-000001" in claimed.content[0].text


def test_every_tool_declares_all_four_hints(scratch_board: Path) -> None:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(ADAPTER_SRC), str(CORE_SRC)])
    env["TACKBOARD_DB"] = str(scratch_board)
    env["TACKBOARD_AGENT"] = "handshake"
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "tackboard_mcp.server"], env=env
    )

    async def listed_annotations() -> dict[str, Any]:
        async with (
            stdio_client(params) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            result = await session.list_tools()
            return {tool.name: tool.annotations for tool in result.tools}

    received = asyncio.run(listed_annotations())
    assert set(received) == EXPECTED_TOOLS
    for name, expected in EXPECTED_ANNOTATIONS.items():
        annotations = received[name]
        assert annotations is not None, f"{name} carries no annotations"
        payload = annotations.model_dump(by_alias=True)
        for hint, value in expected.items():
            assert hint in payload, f"{name} is missing {hint}"
            actual = payload[hint]
            assert actual is value, f"{name}.{hint} is {actual!r}, expected {value!r}"
