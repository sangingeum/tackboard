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

from tackboard import claims, cli, db, models, schema, service, web  # noqa: E402
from tackboard.errors import UsageError  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ACTOR = "dev"

RunCli = Callable[..., tuple[int, str, str]]

#: Generous bound for the 1,000-task graph page: proves the page stays bounded
#: without asserting a wall-clock number.
LARGE_GRAPH_TASKS = 1000
LARGE_GRAPH_BYTE_LIMIT = 4 * 1024 * 1024

#: Fields :func:`tackboard.views.graph_document` adds on top of the CLI nodes.
DERIVED_NODE_FIELDS = frozenset(
    {"dependency_ready", "eligible", "reviewable", "blocked", "claimed_by"}
)


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
        self, path: str, *, method: str = "GET", host: str | None = None
    ) -> Response:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        headers = {"Host": host} if host is not None else {}
        try:
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
        conn, actor=DEFAULT_ACTOR, title="Done prerequisite", status="ready"
    )["task"]
    claims.claim(conn, done["id"], actor=DEFAULT_ACTOR)
    service.complete_task(conn, done["id"], actor=DEFAULT_ACTOR)
    claimed = service.create_task(
        conn,
        actor=DEFAULT_ACTOR,
        title="Claimed work",
        status="ready",
        depends_on=[done["id"]],
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

    # The claim set the assignee on the done and claimed tasks.
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
    assert page.count('class="card card-done"') == 20
    assert "show all (5 more)" in page
    assert 'href="/board?status=done"' in page

    expanded = server.request("/board?status=done").text
    assert expanded.count('class="card card-done"') == 25
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
