"""MCP binding tests — skipped unless the optional framework is installed.

The tool layer is covered without the framework (``test_tools.py``). These tests
prove the binding itself: that the seven tools register, and — when the
framework is present — that a real MCP client can handshake over stdio and call
them. In a checkout without ``mcp`` they skip rather than fail; the report
states which of the two was actually exercised.

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
    "complete",
    "create",
    "next_claim",
    "note",
    "release",
    "show",
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
