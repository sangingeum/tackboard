"""Web tests for the read-only board view (spec section 16, the Web area).

Every test drives the real server: it is started on port 0 against a temporary
database in a background thread, and the assertions are made against real HTTP
responses. Nothing here touches the network beyond loopback, and nothing here
needs JavaScript.
"""

from __future__ import annotations

import http.client
import json
import re
import sqlite3
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tackboard import (  # noqa: E402
    claims,
    cli,
    db,
    memo,
    models,
    schema,
    service,
    web,
)
from tackboard.errors import UsageError  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ACTOR = "dev"

RunCli = Callable[..., tuple[int, str, str]]

#: Generous bound for the 1,000-task graph page: proves the page stays bounded
#: without asserting a wall-clock number.
LARGE_GRAPH_TASKS = 1000
LARGE_GRAPH_BYTE_LIMIT = 4 * 1024 * 1024

#: Fields :func:`tackboard.views.graph_document` adds on top of the CLI nodes.
#: Claim data is now part of the shared projection, so it is not derived here.
DERIVED_NODE_FIELDS = frozenset(
    {"dependency_ready", "eligible", "reviewable", "blocked"}
)

#: Claim fields the shared projection carries on every node (nullable when absent).
CLAIM_NODE_FIELDS = frozenset({"claimed_by", "claimed_at"})


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never inherit project selection or an actor from the developer's shell."""
    monkeypatch.delenv("TACKBOARD_DB", raising=False)
    monkeypatch.delenv("TACKBOARD_AGENT", raising=False)


@pytest.fixture(autouse=True)
def quiet_request_log(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the server's per-request stderr logging out of captured output."""
    monkeypatch.setattr(web._BoardRequestHandler, "log_message", lambda *args: None)


@pytest.fixture
def db_file(tmp_path: Path) -> Path:
    """An initialized, empty project database."""
    path = tmp_path / "project.sqlite3"
    schema.init_database(path, label="project")
    return path


@pytest.fixture
def run(capsys: pytest.CaptureFixture[str]) -> RunCli:
    """Invoke the CLI in process, returning (exit code, stdout, stderr)."""

    def _run(*argv: str) -> tuple[int, str, str]:
        code = cli.main(list(argv))
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return _run


class Response:
    """A completed HTTP response."""

    __slots__ = ("body", "headers", "status")

    def __init__(self, status: int, headers: dict[str, str], body: bytes) -> None:
        self.status = status
        self.headers = headers
        self.body = body

    @property
    def text(self) -> str:
        return self.body.decode("utf-8")

    def json(self) -> Any:
        return json.loads(self.text)


class WebClient:
    """A tiny loopback HTTP client that can override the ``Host`` header."""

    def __init__(self, port: int) -> None:
        self.port = port

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        host: str | None = None,
        skip_host: bool = False,
        extra_headers: dict[str, str] | None = None,
    ) -> Response:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        headers = dict(extra_headers or {})
        if host is not None:
            headers["Host"] = host
        try:
            if skip_host:
                connection.putrequest(method, path, skip_host=True)
                for key, value in headers.items():
                    connection.putheader(key, value)
                connection.endheaders()
            else:
                connection.request(method, path, headers=headers)
            response = connection.getresponse()
            status = response.status
            body = response.read()
            out_headers = {key: value for key, value in response.getheaders()}
        finally:
            connection.close()
        return Response(status, out_headers, body)


@pytest.fixture
def start_server() -> Iterator[Callable[..., WebClient]]:
    """Start servers on free ports and always shut them down afterwards."""
    started: list[tuple[web.BoardServer, threading.Thread]] = []

    def _start(
        database: Path,
        *,
        allowed_hosts: frozenset[str] = web.LOOPBACK_HOSTNAMES,
        remote: bool = False,
    ) -> WebClient:
        config = web.ServerConfig(
            database_path=database, allowed_hosts=allowed_hosts, remote=remote
        )
        server = web.create_server(config, "127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        started.append((server, thread))
        return WebClient(server.port)

    yield _start

    for server, thread in started:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


@pytest.fixture
def server(db_file: Path, start_server: Callable[..., WebClient]) -> WebClient:
    return start_server(db_file)


def seed_board(conn: Any) -> dict[str, str]:
    """A small board covering done, claimed, eligible, blocked and backlog."""
    done = service.create_task(
        conn,
        actor=DEFAULT_ACTOR,
        title="Done prerequisite",
        status="ready",
        assignee=DEFAULT_ACTOR,
    )["task"]
    claims.claim(conn, done["id"], actor=DEFAULT_ACTOR)
    service.complete_task(conn, done["id"], actor=DEFAULT_ACTOR)
    claimed = service.create_task(
        conn,
        actor=DEFAULT_ACTOR,
        title="Claimed work",
        status="ready",
        depends_on=[done["id"]],
        assignee=DEFAULT_ACTOR,
    )["task"]
    claims.claim(conn, claimed["id"], actor=DEFAULT_ACTOR)
    service.add_note(conn, claimed["id"], "handoff note", actor=DEFAULT_ACTOR)
    eligible = service.create_task(
        conn,
        actor=DEFAULT_ACTOR,
        title="Eligible work",
        status="ready",
        depends_on=[done["id"]],
    )["task"]
    blocked = service.create_task(
        conn,
        actor=DEFAULT_ACTOR,
        title="Blocked work",
        status="ready",
        depends_on=[eligible["id"]],
    )["task"]
    backlog = service.create_task(
        conn, actor=DEFAULT_ACTOR, title="Backlog idea"
    )["task"]
    return {
        "done": done["id"],
        "claimed": claimed["id"],
        "eligible": eligible["id"],
        "blocked": blocked["id"],
        "backlog": backlog["id"],
    }


@pytest.fixture
def board(db_file: Path) -> dict[str, str]:
    conn = schema.open_database(db_file)
    try:
        return seed_board(conn)
    finally:
        conn.close()


def counts_line(page: str) -> str:
    match = re.search(r'<p class="counts-line">([^<]*)</p>', page)
    assert match is not None, "the board page carries no counts line"
    return match.group(1)


# --------------------------------------------------------------------------- #
# selected database, and JSON parity with the CLI
# --------------------------------------------------------------------------- #


def test_each_server_serves_only_its_own_database(
    tmp_path: Path, start_server: Callable[..., WebClient]
) -> None:
    first_path = tmp_path / "first.sqlite3"
    second_path = tmp_path / "second.sqlite3"
    schema.init_database(first_path, label="first")
    schema.init_database(second_path, label="second")
    for path, title in ((first_path, "Only in first"), (second_path, "Only in second")):
        conn = schema.open_database(path)
        try:
            service.create_task(conn, actor=DEFAULT_ACTOR, title=title)
        finally:
            conn.close()

    first = start_server(first_path)
    second = start_server(second_path)

    assert "Only in first" in first.request("/board").text
    assert "Only in second" not in first.request("/board").text
    assert "Only in second" in second.request("/board").text
    assert first.request("/api/board").json()["project"] == "first"
    assert second.request("/api/board").json()["project"] == "second"

    # Task IDs are database-local, so both boards hold a T-000001; the detail
    # each server returns must be its own row, never the other board's.
    first_task = first.request("/api/board").json()["tasks"][0]["id"]
    second_task = second.request("/api/board").json()["tasks"][0]["id"]
    assert first_task == second_task == "T-000001"
    assert first.request(f"/api/tasks/{first_task}").json()["task"]["title"] == (
        "Only in first"
    )
    assert second.request(f"/api/tasks/{second_task}").json()["task"]["title"] == (
        "Only in second"
    )
    assert first.request("/api/tasks/T-000099").status == 404


def test_json_endpoints_match_the_cli_schemas(
    run: RunCli, server: WebClient, db_file: Path, board: dict[str, str]
) -> None:
    cli_board = json.loads(run("board", "--db", str(db_file), "--json")[1])
    assert server.request("/api/board").json() == cli_board

    cli_graph = json.loads(run("graph", "--db", str(db_file), "--json")[1])
    served_graph = server.request("/api/graph").json()
    trimmed = [
        {
            key: value
            for key, value in node.items()
            if key not in DERIVED_NODE_FIELDS
        }
        for node in served_graph["nodes"]
    ]
    assert trimmed == cli_graph["nodes"]
    assert served_graph["edges"] == cli_graph["edges"]
    # The claim fields are part of the shared projection now: both documents
    # must carry them, and the trimmed comparison above already proves the
    # values agree for the same database.
    for node in served_graph["nodes"]:
        assert set(node) >= CLAIM_NODE_FIELDS
    for node in cli_graph["nodes"]:
        assert set(node) >= CLAIM_NODE_FIELDS

    cli_show = json.loads(run("show", board["claimed"], "--db", str(db_file), "--json")[1])
    detail = server.request(f"/api/tasks/{board['claimed']}").json()
    assert detail["task"] == cli_show["task"]
    assert detail["notes"] == cli_show["notes"]
    assert detail["claim"]["agent_id"] == DEFAULT_ACTOR
    assert [task["id"] for task in detail["prerequisites"]] == [board["done"]]
    assert [task["id"] for task in detail["dependents"]] == []

    blocked_detail = server.request(f"/api/tasks/{board['blocked']}").json()
    assert blocked_detail["task"]["blocked"] is True
    assert blocked_detail["task"]["blockers"] == [
        {"code": "prerequisite_unfinished", "task_id": board["eligible"]}
    ]


# --------------------------------------------------------------------------- #
# filters and counts
# --------------------------------------------------------------------------- #


def test_board_filters_and_counts_match_board_count(
    run: RunCli, server: WebClient, db_file: Path, board: dict[str, str]
) -> None:
    cli_counts = " ".join(run("board", "--count", "--db", str(db_file))[1].split())
    assert counts_line(server.request("/board").text) == cli_counts

    for query, filters in (
        ("?status=ready", ("--status", "ready")),
        ("?eligible=1", ("--eligible",)),
        ("?blocked=1", ("--blocked",)),
    ):
        expected = " ".join(
            run("board", "--count", *filters, "--db", str(db_file))[1].split()
        )
        assert counts_line(server.request(f"/board{query}").text) == expected

    eligible = server.request("/api/board?eligible=1").json()
    assert [task["id"] for task in eligible["tasks"]] == [board["eligible"]]

    blocked = server.request("/api/board?blocked=1").json()
    assert [task["id"] for task in blocked["tasks"]] == [board["blocked"]]

    ready = server.request("/api/board?status=ready").json()
    assert ready["counts"]["tasks"] == 2

    # The done and claimed tasks are routed to dev by an explicit assignment.
    labelled = server.request("/api/board?assignee=dev").json()
    assert labelled["counts"]["tasks"] == 2
    unassigned = server.request("/api/board?assignee=nobody").json()
    assert unassigned["counts"]["tasks"] == 0


def test_unknown_filter_values_are_rejected(server: WebClient) -> None:
    assert server.request("/api/board?status=bogus").status == 400
    assert server.request("/api/board?priority=bogus").status == 400


def test_terminal_columns_truncate_to_twenty_with_a_show_all_link(
    server: WebClient, db_file: Path
) -> None:
    conn = schema.open_database(db_file)
    try:
        for index in range(25):
            task = service.create_task(
                conn,
                actor=DEFAULT_ACTOR,
                title=f"Finished {index}",
                status="ready",
            )["task"]
            claims.claim(conn, task["id"], actor=DEFAULT_ACTOR)
            service.complete_task(conn, task["id"], actor=DEFAULT_ACTOR)
    finally:
        conn.close()

    page = server.request("/board").text
    assert len(re.findall(r'<li class="card"[^>]*data-status="done"', page)) == 20
    assert "show all (5 more)" in page
    assert 'href="/board?status=done"' in page

    expanded = server.request("/board?status=done").text
    assert len(re.findall(r'<li class="card"[^>]*data-status="done"', expanded)) == 25
    assert "show all" not in expanded


# --------------------------------------------------------------------------- #
# read-only enforcement, host validation and headers
# --------------------------------------------------------------------------- #


def test_only_get_and_head_are_accepted(
    server: WebClient, board: dict[str, str]
) -> None:
    for method in ("POST", "PUT", "DELETE", "PATCH"):
        assert server.request("/board", method=method).status == 405
        assert server.request("/api/board", method=method).status == 405
        assert (
            server.request(f"/api/tasks/{board['done']}", method=method).status == 405
        )

    response = server.request("/api/board", method="POST")
    assert response.headers.get("Allow") == "GET, HEAD"

    # Every other verb, including one the standard library has no handler for.
    for method in ("OPTIONS", "TRACE", "BREW"):
        assert server.request("/board", method=method).status == 405
        assert server.request("/api/tasks/T-000001", method=method).status == 405

    head = server.request("/board", method="HEAD")
    assert head.status == 200
    assert head.body == b""


def test_security_headers_are_present_on_html_responses(server: WebClient) -> None:
    for path in ("/", "/board", "/graph"):
        headers = server.request(path).headers
        assert headers.get("Content-Security-Policy") == "default-src 'self'"
        assert headers.get("X-Content-Type-Options") == "nosniff"
        assert "script-src" not in headers.get("Content-Security-Policy", "")


def test_a_foreign_host_header_is_rejected(
    server: WebClient, board: dict[str, str]
) -> None:
    assert server.request("/board", host="evil.example.com").status == 403
    assert server.request("/api/board", host="evil.example.com").status == 403
    assert server.request("/board", host=f"127.0.0.1:{server.port + 1}").status == 403
    assert server.request("/board", host="localhost").status == 200
    assert server.request("/board", host=f"localhost:{server.port}").status == 200
    assert server.request("/board", host=f"[::1]:{server.port}").status == 200


def test_a_refused_host_body_names_the_value_and_the_accepted_set(
    server: WebClient, board: dict[str, str]
) -> None:
    """Every refusal cause is named, and the accepted set comes with it."""
    accepted = ", ".join(sorted(web.LOOPBACK_HOSTNAMES))

    # Control: an accepted host is served on the same fixture, so the 403s below
    # are the header's fault, not the harness's.
    control = server.request("/board", host=f"localhost:{server.port}")
    assert control.status == 200

    # A host outside the allowlist.
    foreign = server.request("/board", host="evil.example.com")
    assert foreign.status == 403
    assert foreign.headers["Content-Type"] == "text/plain; charset=utf-8"
    assert "'evil.example.com'" in foreign.text
    assert accepted in foreign.text
    assert str(server.port) in foreign.text
    assert "--allowed-host" in foreign.text
    assert foreign.text.endswith("\n")

    # A host:port whose port differs from the bound port.
    wrong_port = server.request("/board", host="localhost:9")
    assert wrong_port.status == 403
    assert "'localhost:9'" in wrong_port.text
    assert str(server.port) in wrong_port.text
    assert "port" in wrong_port.text

    # An unparseable Host header.
    unparseable = server.request("/board", host="a:b:c")
    assert unparseable.status == 403
    assert "'a:b:c'" in unparseable.text
    assert accepted in unparseable.text

    # No Host header at all.
    absent = server.request("/board", skip_host=True)
    assert absent.status == 403
    assert "no Host header" in absent.text
    assert accepted in absent.text


def test_a_refused_host_head_request_carries_no_body(server: WebClient) -> None:
    """HEAD stays body-less, so the refusal text never leaks into headers."""
    response = server.request("/board", method="HEAD", host="evil.example.com")
    assert response.status == 403
    assert response.headers["Content-Type"] == "text/plain; charset=utf-8"
    assert response.body == b""
    assert all(
        "Host header not allowed" not in value for value in response.headers.values()
    )


def test_a_giant_host_header_cannot_inflate_the_refusal(server: WebClient) -> None:
    """An oversized Host value is echoed bounded, never verbatim."""
    response = server.request("/board", host="x" * 5000)
    assert response.status == 403
    assert "..." in response.text
    assert len(response.text) < web.HOST_ECHO_LIMIT + 400


def test_remote_binding_accepts_only_configured_hosts(
    db_file: Path, start_server: Callable[..., WebClient]
) -> None:
    client = start_server(
        db_file,
        allowed_hosts=frozenset({"board.example.com"}),
        remote=True,
    )
    assert client.request("/board", host="board.example.com").status == 200
    assert client.request("/board", host="localhost").status == 403
    assert client.request("/board", host="evil.example.com").status == 403


def test_host_header_parsing() -> None:
    assert web.parse_host_header("localhost") == ("localhost", None)
    assert web.parse_host_header("LOCALHOST:8765") == ("localhost", 8765)
    assert web.parse_host_header("[::1]:8765") == ("::1", 8765)
    assert web.parse_host_header("[::1]") == ("::1", None)
    for bad in ("", "a:b:c", "[::1", "[::1]x", "host:99999"):
        with pytest.raises(ValueError):
            web.parse_host_header(bad)


def test_loopback_and_allowlist_policy() -> None:
    assert web.is_loopback_host("127.0.0.1")
    assert web.is_loopback_host("localhost")
    assert web.is_loopback_host("::1")
    assert not web.is_loopback_host("0.0.0.0")

    hosts, remote = web.resolve_allowed_hosts(
        "127.0.0.1", allow_remote=False, allowed_hosts=()
    )
    assert hosts >= {"localhost", "127.0.0.1", "::1"}
    assert remote is False

    with pytest.raises(UsageError):
        web.resolve_allowed_hosts("0.0.0.0", allow_remote=False, allowed_hosts=())
    with pytest.raises(UsageError):
        web.resolve_allowed_hosts("0.0.0.0", allow_remote=True, allowed_hosts=())

    hosts, remote = web.resolve_allowed_hosts(
        "0.0.0.0", allow_remote=True, allowed_hosts=["board.example.com"]
    )
    assert hosts == {"board.example.com"}
    assert remote is True


# --------------------------------------------------------------------------- #
# untrusted text
# --------------------------------------------------------------------------- #

HOSTILE_TITLE = "<script>alert(1)</script>"
HOSTILE_TEXT = 'quotes " and \'apostrophes\' & <b>bold</b>'


@pytest.fixture
def hostile(db_file: Path) -> dict[str, str]:
    conn = schema.open_database(db_file)
    try:
        task = service.create_task(
            conn,
            actor=DEFAULT_ACTOR,
            title=f"{HOSTILE_TITLE} {HOSTILE_TEXT}",
            description=HOSTILE_TEXT,
            labels=(f"label{HOSTILE_TEXT}",),
        )["task"]
        service.add_note(conn, task["id"], HOSTILE_TEXT, actor=DEFAULT_ACTOR)
        return {"id": task["id"]}
    finally:
        conn.close()


def test_task_text_is_escaped_in_html(
    server: WebClient, hostile: dict[str, str]
) -> None:
    board_page = server.request("/board").text
    assert "<script>alert(1)</script>" not in board_page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in board_page
    assert "&amp;lt;b&amp;gt;" not in board_page
    assert "&lt;b&gt;bold&lt;/b&gt;" in board_page

    detail = server.request(f"/task/{hostile['id']}").text
    assert "<script>alert(1)</script>" not in detail
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in detail
    assert "&quot;" in detail
    assert "&#x27;apostrophes&#x27;" in detail
    assert "&amp;" in detail
    assert "&lt;b&gt;bold&lt;/b&gt;" in detail

    outline = server.request("/graph").text
    assert "<script>alert(1)</script>" not in outline


def test_task_text_round_trips_intact_through_json(
    server: WebClient, hostile: dict[str, str]
) -> None:
    detail = server.request(f"/api/tasks/{hostile['id']}").json()
    assert detail["task"]["title"] == f"{HOSTILE_TITLE} {HOSTILE_TEXT}"
    assert detail["task"]["description"] == HOSTILE_TEXT
    assert detail["task"]["labels"] == [f"label{HOSTILE_TEXT}"]
    assert detail["notes"][0]["payload"]["text"] == HOSTILE_TEXT


# --------------------------------------------------------------------------- #
# task page redesign and in-house Markdown renderer
# --------------------------------------------------------------------------- #

FORBIDDEN_JS_APIS = (
    "innerHTML",
    "outerHTML",
    "insertAdjacentHTML",
    "document.write",
    "eval",
    "new Function",
)


def test_markdown_asset_is_served_with_security_headers(server: WebClient) -> None:
    response = server.request("/static/markdown.js")
    assert response.status == 200
    assert response.headers["Content-Type"].startswith("text/javascript")
    assert response.headers.get("Content-Security-Policy") == "default-src 'self'"
    assert response.headers.get("X-Content-Type-Options") == "nosniff"


def test_served_web_assets_avoid_forbidden_html_injection_apis(
    server: WebClient,
) -> None:
    for name in ("app.css", "theme.js", "board.js", "graph.js", "markdown.js"):
        text = server.request(f"/static/{name}").text
        for api in FORBIDDEN_JS_APIS:
            assert api not in text, f"{name} contains forbidden API {api!r}"


def test_the_task_page_uses_the_design_markup(
    server: WebClient, board: dict[str, str]
) -> None:
    page = server.request(f"/task/{board['blocked']}").text
    assert 'class="task-header"' in page
    assert 'class="task-header__title"' in page
    assert 'class="facts"' in page
    assert 'class="blockers"' in page
    assert 'class="link-list"' in page
    assert 'class="timeline"' in page
    assert 'class="status-chip"' in page
    assert 'class="description"' in page
    assert "A \u2192 B means B depends on A." in page


def test_the_description_fallback_is_a_pre_and_escaped(
    server: WebClient, hostile: dict[str, str]
) -> None:
    page = server.request(f"/task/{hostile['id']}").text
    assert '<pre class="description">' in page
    assert "&lt;b&gt;bold&lt;/b&gt;" in page
    assert "<b>bold</b>" not in page
    assert "&quot;" in page


def test_a_rejected_link_destination_is_not_emitted_as_href(
    server: WebClient, db_file: Path
) -> None:
    connection = schema.open_database(db_file)
    try:
        task = service.create_task(
            connection,
            actor=DEFAULT_ACTOR,
            title="Link safety",
            description="[click](javascript:alert(1))",
        )["task"]
    finally:
        connection.close()

    page = server.request(f"/task/{task['id']}").text
    assert 'href="javascript:' not in page
    assert "javascript:alert(1)" in page
    script = server.request("/static/markdown.js").text
    assert "isHttpUrl" in script


# --------------------------------------------------------------------------- #
# empty database, large graph, and the no-JavaScript fallback
# --------------------------------------------------------------------------- #


def test_an_empty_database_renders_every_view(server: WebClient) -> None:
    for path in ("/", "/board", "/graph"):
        response = server.request(path)
        assert response.status == 200, path
        assert response.headers["Content-Security-Policy"] == "default-src 'self'"

    assert "No tasks yet." in server.request("/graph").text
    assert counts_line(server.request("/board").text).startswith("tasks=0 ")

    empty_graph = server.request("/api/graph").json()
    assert empty_graph["nodes"] == []
    assert empty_graph["edges"] == []

    assert server.request("/task/T-000001").status == 404
    assert server.request("/api/tasks/T-000001").status == 404
    assert server.request("/nope").status == 404


def test_no_javascript_fallback_renders_the_dependency_structure(
    server: WebClient, board: dict[str, str]
) -> None:
    page = server.request("/graph").text
    assert "Dependency outline" in page
    assert 'id="graph-fallback"' in page
    assert board["done"] in page
    assert board["blocked"] in page
    assert f"depends on: {board['done']}" in page
    assert f"depends on: {board['eligible']}" in page


def test_hide_closed_filters_the_outline_and_keeps_derived_flags(
    server: WebClient, board: dict[str, str]
) -> None:
    """?hide_closed=1 removes done/cancelled nodes and their edges only.

    The blocked flag and reason must still come from the full graph: the
    blocked task depends on the eligible task (not closed), so it stays blocked
    and keeps its marker text even though its finished prerequisite is gone.
    """
    full = server.request("/graph").text
    hidden = server.request("/graph?hide_closed=1").text

    # The full view names the finished task and its dependents.
    assert board["done"] in full
    assert f"depends on: {board['done']}" in full

    # The filtered view removes the finished node's outline line and every edge
    # touching it. (The ID still appears as the neighbourhood form placeholder.)
    assert f'href="/task/{board["done"]}"' not in hidden
    assert f"depends on: {board['done']}" not in hidden
    # Every remaining node is still present.
    for key in ("claimed", "eligible", "blocked", "backlog"):
        assert f'href="/task/{board[key]}"' in hidden
    # The hidden count and the "of" totals are visible. The seed graph has five
    # nodes and three edges; hiding "done" removes one node and two edges.
    assert "nodes=4 of 5" in hidden
    assert "edges=1 of 3" in hidden
    assert "1 hidden" in hidden

    # The derived flag survives: the blocked task is still marked blocked and
    # its reason still names the eligible prerequisite computed from the full
    # graph, not from the filtered set.
    assert 'data-blocked="true"' in hidden
    assert "blocked" in hidden


def test_graph_page_marks_claim_holders_in_the_outline(
    server: WebClient, board: dict[str, str]
) -> None:
    page = server.request("/graph").text
    assert f"[claimed by {DEFAULT_ACTOR} since " in page
    assert "claimed \u2014 double ring + holder badge" in page
    # The legend states both the claimed and hidden cues as words, not colour.
    assert "double ring" in page
    assert "holder badge" in page


def test_graph_page_carries_keyboard_and_accessibility_hooks(
    server: WebClient, board: dict[str, str]
) -> None:
    page = server.request("/graph").text
    # The help dialog and the skip link are in the shared shell.
    assert 'id="help-dialog"' in page
    assert 'class="skip-link"' in page
    assert 'href="#main-content"' in page
    # The toolbar exposes the two new toggles with pressed state.
    assert 'data-graph-action="toggle-claimed"' in page
    assert 'aria-pressed="true"' in page
    assert 'data-graph-action="focus-claimed"' in page
    assert 'data-graph-action="toggle-hide-closed"' in page
    assert 'aria-pressed="false"' in page


def test_graph_page_exposes_the_canvas_toolbar_selection_and_minimap(
    server: WebClient,
) -> None:
    """The interactive shell is server-rendered; the script only fills it."""
    page = server.request("/graph").text
    assert 'class="graph-toolbar"' in page
    assert 'role="toolbar"' in page
    for action in ("zoom-in", "zoom-out", "fit-all", "zoom-reset"):
        assert f'data-graph-action="{action}"' in page
    assert 'id="graph-canvas"' in page
    assert 'id="graph-selection"' in page
    assert 'id="minimap"' in page
    assert 'id="minimap-toggle"' in page
    assert "A &#8594; B means B depends on A." in page


def test_a_thousand_task_graph_degrades_gracefully(
    server: WebClient, db_file: Path
) -> None:
    _insert_many_tasks(db_file, LARGE_GRAPH_TASKS)

    page = server.request("/graph")
    assert page.status == 200
    assert len(page.body) < LARGE_GRAPH_BYTE_LIMIT
    assert f"nodes={LARGE_GRAPH_TASKS} edges={LARGE_GRAPH_TASKS - 1}" in page.text
    assert models.task_id_for_sequence(LARGE_GRAPH_TASKS) in page.text

    board_page = server.request("/board")
    assert board_page.status == 200
    assert len(board_page.body) < LARGE_GRAPH_BYTE_LIMIT

    graph = server.request("/api/graph").json()
    assert len(graph["nodes"]) == LARGE_GRAPH_TASKS
    assert len(graph["edges"]) == LARGE_GRAPH_TASKS - 1


def _insert_many_tasks(database: Path, count: int) -> None:
    """Seed a chain of ``count`` tasks directly — this test is about rendering."""
    conn = schema.open_database(database)
    try:
        with db.write_transaction(conn):
            for sequence in range(1, count + 1):
                conn.execute(
                    """
                    INSERT INTO tasks(
                        id, sequence, title, description, status, priority,
                        assignee, blocked_reason, created_at, updated_at,
                        completed_at, version
                    ) VALUES(?, ?, ?, '', 'backlog', 'normal', NULL, NULL, ?, ?, NULL, 1)
                    """,
                    (
                        models.task_id_for_sequence(sequence),
                        sequence,
                        f"Bulk task {sequence}",
                        "2026-10-15T00:00:00Z",
                        "2026-10-15T00:00:00Z",
                    ),
                )
                if sequence > 1:
                    conn.execute(
                        "INSERT INTO dependencies(prerequisite_id, task_id) "
                        "VALUES(?, ?)",
                        (
                            models.task_id_for_sequence(sequence - 1),
                            models.task_id_for_sequence(sequence),
                        ),
                    )
            conn.execute(
                "UPDATE metadata SET value = ? WHERE key = 'next_sequence'",
                (str(count + 1),),
            )
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# static files and the serve command
# --------------------------------------------------------------------------- #


def test_static_assets_are_served_from_the_package_only(server: WebClient) -> None:
    css = server.request("/static/app.css")
    assert css.status == 200
    assert css.headers["Content-Type"].startswith("text/css")
    assert ":root" in css.text

    script = server.request("/static/graph.js")
    assert script.status == 200
    assert script.headers["Content-Type"].startswith("text/javascript")

    assert server.request("/static/../web.py").status == 404
    assert server.request("/static/..%2fweb.py").status == 404
    assert server.request("/static/nothing.css").status == 404
    assert server.request("/static/web.py").status == 404


def test_serve_refuses_a_remote_bind_without_the_explicit_options(
    run: RunCli, db_file: Path
) -> None:
    code, out, _ = run("serve", "--db", str(db_file), "--host", "0.0.0.0", "--json")
    assert code == 2
    assert json.loads(out)["error"]["code"] == "usage"

    code, out, _ = run(
        "serve", "--db", str(db_file), "--host", "0.0.0.0", "--allow-remote", "--json"
    )
    assert code == 2
    assert json.loads(out)["error"]["code"] == "usage"


def test_serve_reports_a_missing_database(run: RunCli, tmp_path: Path) -> None:
    missing = tmp_path / "absent.sqlite3"
    code, out, _ = run("serve", "--db", str(missing), "--json")
    assert code == 3
    assert json.loads(out)["error"]["code"] == "not_found"


def test_serve_json_prints_exactly_one_startup_record(db_file: Path) -> None:
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "tackboard",
            "serve",
            "--db",
            str(db_file),
            "--port",
            "0",
            "--json",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(REPO_ROOT),
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(SRC), "PYTHONUNBUFFERED": "1"},
    )
    assert process.stdout is not None
    assert process.stderr is not None
    try:
        line = process.stdout.readline()
        assert line.strip().startswith("{"), f"no JSON record on stdout: {line!r}"
        record = json.loads(line)
        assert record["schema_version"] == 1
        assert record["event"] == "serve.start"
        assert record["port"] > 0
        assert record["database"] == str(db_file)
        assert record["remote"] is False
        assert "127.0.0.1" in record["allowed_hosts"]
        assert record["authentication"] == "none"
    finally:
        process.terminate()
        rest = process.stdout.read()
        assert "schema_version" not in process.stderr.read()
        process.wait(timeout=20)
        process.stdout.close()
        process.stderr.close()

    assert rest == "", f"serve wrote more than one record: {rest!r}"


# --------------------------------------------------------------------------- #
# the board-level memo
# --------------------------------------------------------------------------- #


def _write_memo(
    database: Path, text: str, *, author: str = DEFAULT_ACTOR, **kwargs: Any
) -> None:
    connection = schema.open_database(database)
    try:
        memo.replace_memo(connection, actor=author, text=text, **kwargs)
    finally:
        connection.close()


def test_api_memo_is_the_cli_document(server: WebClient, db_file: Path) -> None:
    _write_memo(db_file, "standing note", reason="first")
    response = server.request("/api/memo")
    assert response.status == 200
    assert response.headers["Content-Type"] == "application/json; charset=utf-8"

    written = schema.open_database(db_file)
    try:
        expected = {"schema_version": 1, **memo.read_memo(written)}
    finally:
        written.close()
    assert response.json() == expected


def test_api_memo_of_an_empty_board_is_the_empty_document(server: WebClient) -> None:
    assert server.request("/api/memo").json() == {
        "schema_version": 1,
        "rev": 0,
        "text": "",
        "author": None,
        "created_at": None,
        "bytes": 0,
    }


def test_the_board_page_shows_the_memo_escaped(
    server: WebClient, db_file: Path
) -> None:
    hostile = "<script>alert('memo')</script> & <b>bold</b>"
    _write_memo(db_file, hostile, reason="first")

    page = server.request("/board").text
    assert "memo-text" in page
    assert "rev 1" in page
    assert DEFAULT_ACTOR in page
    assert hostile not in page
    assert "&lt;script&gt;" in page
    assert "&amp;" in page


def test_a_small_memo_is_open_and_a_large_one_is_collapsed(
    server: WebClient, db_file: Path
) -> None:
    _write_memo(db_file, "short", reason="first")
    small = server.request("/board").text
    assert '<details class="memo-details" open>' in small

    connection = schema.open_database(db_file)
    try:
        memo.replace_memo(
            connection,
            actor=DEFAULT_ACTOR,
            text="x" * (memo.COLLAPSE_BYTES + 1),
            reason="grow",
            if_rev=1,
        )
    finally:
        connection.close()
    large = server.request("/board").text
    assert '<details class="memo-details" open>' not in large
    assert "memo-details" in large
    # The whole memo is present; it is collapsed, never truncated.
    assert "x" * (memo.COLLAPSE_BYTES + 1) in large


def test_an_empty_board_says_there_is_no_memo(server: WebClient) -> None:
    page = server.request("/board").text
    assert "no memo yet" in page
    assert "memo-details" in page


def test_the_task_page_does_not_repeat_the_memo(
    server: WebClient, db_file: Path
) -> None:
    connection = schema.open_database(db_file)
    try:
        task = service.create_task(
            connection, actor=DEFAULT_ACTOR, title="Task", status="ready"
        )["task"]
    finally:
        connection.close()
    _write_memo(db_file, "only on the board", reason="first")

    page = server.request(f"/task/{task['id']}").text
    assert "only on the board" not in page
    assert "memo-details" not in page


def test_the_web_has_no_memo_write_path(server: WebClient, db_file: Path) -> None:
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        response = server.request("/api/memo", method=method)
        assert response.status == 405, method
        assert response.headers["Allow"] == "GET, HEAD"

    _write_memo(db_file, "unchanged", reason="first")
    before = server.request("/api/memo").body
    server.request("/api/memo", method="POST")
    assert server.request("/api/memo").body == before


def test_a_corrupt_memo_table_answers_on_the_api_and_the_board(
    db_file: Path, start_server: Callable[..., WebClient]
) -> None:
    """A board whose memo table is unusable must not drop the connection.

    The API returns the typed 500 envelope and the board page renders the typed
    error page, rather than raising out of ``_respond`` with no response.
    """
    connection = sqlite3.connect(db_file, isolation_level=None)
    try:
        connection.execute("DROP TABLE IF EXISTS memo_revisions")
        connection.execute("CREATE TABLE memo_revisions (zzz TEXT)")
        connection.execute("PRAGMA user_version=2")
    finally:
        connection.close()

    client = start_server(db_file)

    api = client.request("/api/memo")
    assert api.status == 500
    error = api.json()["error"]
    assert error["code"] == "memo_table_invalid"
    assert error["exit_code"] == 6
    assert error["retryable"] is False

    board = client.request("/board")
    assert board.status == 500
    assert "memo revision table is not usable" in board.text


# --------------------------------------------------------------------------- #
# ETag, 304 and header discipline
# --------------------------------------------------------------------------- #


def test_json_endpoints_carry_an_etag_and_honour_if_none_match(
    server: WebClient, board: dict[str, str]
) -> None:
    """A matching If-None-Match is answered 304 with no body and full headers."""
    for path in (
        "/api/board",
        "/api/graph",
        "/api/memo",
        f"/api/tasks/{board['done']}",
    ):
        first = server.request(path)
        assert first.status == 200
        etag = first.headers.get("ETag")
        assert etag is not None, f"{path} carries no ETag"
        assert etag.startswith('"') and etag.endswith('"')
        assert first.headers.get("Cache-Control") == "no-cache"

        second = server.request(path, extra_headers={"If-None-Match": etag})
        assert second.status == 304
        assert second.body == b""
        assert second.headers.get("ETag") == etag
        assert second.headers.get("Cache-Control") == "no-cache"
        assert second.headers.get("Content-Security-Policy") == "default-src 'self'"
        assert second.headers.get("X-Content-Type-Options") == "nosniff"

        weak = server.request(path, extra_headers={"If-None-Match": f"W/{etag}"})
        assert weak.status == 304

        other = server.request(path, extra_headers={"If-None-Match": '"stale"'})
        assert other.status == 200
        assert other.headers.get("ETag") == etag


def test_head_returns_the_same_headers_as_get_without_a_body(
    server: WebClient, board: dict[str, str]
) -> None:
    for path in ("/api/board", "/api/graph", "/api/memo"):
        get = server.request(path)
        head = server.request(path, method="HEAD")
        assert head.status == 200
        assert head.body == b""
        assert head.headers.get("ETag") == get.headers.get("ETag")
        assert head.headers.get("Cache-Control") == "no-cache"
        assert head.headers.get("Content-Security-Policy") == "default-src 'self'"
        assert head.headers.get("X-Content-Type-Options") == "nosniff"


def test_html_pages_are_served_no_store(server: WebClient) -> None:
    for path in ("/", "/board", "/graph"):
        headers = server.request(path).headers
        assert headers.get("Cache-Control") == "no-store"


def test_static_assets_carry_the_security_headers(server: WebClient) -> None:
    for name in ("app.css", "theme.js", "board.js", "graph.js"):
        response = server.request(f"/static/{name}")
        assert response.status == 200
        assert response.headers.get("Content-Security-Policy") == "default-src 'self'"
        assert response.headers.get("X-Content-Type-Options") == "nosniff"


def test_rendered_pages_have_no_inline_script_style_or_external_hosts(
    server: WebClient, board: dict[str, str]
) -> None:
    """The CSP is only honest if the pages contain no inline or foreign code."""
    for path in ("/", "/board", "/graph", f"/task/{board['done']}"):
        page = server.request(path).text
        assert "<script>" not in page
        assert "<style" not in page
        assert ' style="' not in page
        assert "style='" not in page
        assert "http://" not in page
        assert "https://" not in page


def test_board_renders_and_filters_without_javascript(
    server: WebClient, board: dict[str, str]
) -> None:
    """The server-rendered board is complete: tasks, columns and GET filters."""
    page = server.request("/board").text
    assert 'class="board"' in page
    assert 'data-status="backlog"' in page
    assert "Done prerequisite" in page
    assert "Claimed work" in page
    assert "Backlog idea" in page
    assert 'method="get"' in page
    assert 'action="/board"' in page

    backlog = server.request("/board?status=backlog").text
    assert "Backlog idea" in backlog
    assert "Done prerequisite" not in backlog

    eligible = server.request("/board?eligible=1").text
    assert "Eligible work" in eligible
    assert "Blocked work" not in eligible
