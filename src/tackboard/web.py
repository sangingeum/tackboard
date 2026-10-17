"""Read-only web view: HTTP server, request routing and the JSON API.

Spec: section 11 (web UI) and section 14 (security and operational behaviour).
The server is read-only by design:

- ``GET`` and ``HEAD`` are the only accepted methods; everything else is 405.
- Every request validates the parsed ``Host`` header (hostname *and* port)
  against an allowlist before any routing happens — the DNS-rebinding guard.
- Responses carry ``Content-Security-Policy: default-src 'self'`` and
  ``X-Content-Type-Options: nosniff``; there are no inline scripts or styles.
- Connections are opened per request with ``PRAGMA query_only=ON`` (never
  ``mode=ro``, which is unreliable against a WAL database) and only ever against
  the one database the CLI selected.
- Task text reaches the browser only through ``html.escape`` or ``json.dumps``.

Application data comes from :mod:`tackboard.views`, so the pages, the JSON API
and the CLI all describe the same snapshot with the same shapes.
"""

from __future__ import annotations

import html
import ipaddress
import json
import sqlite3
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from string import Template
from typing import Any
from urllib.parse import parse_qs, unquote, urlencode, urlsplit

from . import schema, views
from .errors import NotFoundError, TackError, UsageError, ValidationError
from .models import PRIORITIES, STATUSES, TERMINAL_STATUSES

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

JSON_SCHEMA_VERSION = 1
JSON_CONTENT_TYPE = "application/json; charset=utf-8"
HTML_CONTENT_TYPE = "text/html; charset=utf-8"

CONTENT_SECURITY_POLICY = "default-src 'self'"
NOSNIFF = "nosniff"

PACKAGE_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = PACKAGE_DIR / "templates"
STATIC_DIR = PACKAGE_DIR / "static"

#: Hostnames accepted for a loopback bind, in addition to the bind host itself.
LOOPBACK_HOSTNAMES: frozenset[str] = frozenset({"localhost", "127.0.0.1", "::1"})

_STATIC_CONTENT_TYPES: dict[str, str] = {
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
}

#: ``done`` and ``cancelled`` columns show this many recent cards plus a link.
WEB_TERMINAL_LIMIT = 20

#: The fallback dependency list indents at most this many levels.
MAX_INDENT_DEPTH = 12

#: Auto-refresh interval, in seconds (spec 11.2).
AUTO_REFRESH_SECONDS = 5


# --------------------------------------------------------------------------- #
# host and binding policy
# --------------------------------------------------------------------------- #


def is_loopback_host(value: str) -> bool:
    """True when ``value`` names the loopback interface (``localhost``/127.x/::1)."""
    text = value.strip().lower()
    if text in LOOPBACK_HOSTNAMES:
        return True
    try:
        return ipaddress.ip_address(text).is_loopback
    except ValueError:
        return False


def parse_host_header(value: str) -> tuple[str, int | None]:
    """Split a ``Host`` header into ``(hostname, port)``; raise on malformed input.

    Handles the IPv6 bracket syntax (``[::1]:8765``, ``[::1]``) and rejects a
    bare colon-separated value that is not a host with a port, so a caller can
    never smuggle a second hostname through.
    """
    raw = value.strip()
    if not raw:
        raise ValueError("empty Host header")
    if raw.startswith("["):
        end = raw.find("]")
        if end == -1:
            raise ValueError("unterminated IPv6 literal")
        host = raw[1:end]
        rest = raw[end + 1 :]
        if not rest:
            return host.lower(), None
        if not rest.startswith(":"):
            raise ValueError("unexpected characters after IPv6 literal")
        return host.lower(), _parse_port(rest[1:])
    if raw.count(":") == 0:
        return raw.lower(), None
    if raw.count(":") == 1:
        host, _, port_text = raw.partition(":")
        return host.lower(), _parse_port(port_text)
    raise ValueError("bare IPv6 literals must be bracketed")


def _parse_port(text: str) -> int | None:
    if not text:
        return None
    try:
        port = int(text)
    except ValueError as exc:
        raise ValueError("invalid port") from exc
    if not 0 < port < 65536:
        raise ValueError("port out of range")
    return port


#: Longest ``repr()`` rendering of a received ``Host`` value echoed in a
#: refusal body; a giant or garbage header must not inflate the response.
HOST_ECHO_LIMIT = 200


def _echo_host(raw: str) -> str:
    """A bounded, escaped rendering of a received ``Host`` value.

    ``repr`` escapes control characters and quotes, so a hostile header cannot
    forge extra body lines; the length cap keeps one oversized header from
    inflating the refusal.
    """
    text = repr(raw)
    if len(text) > HOST_ECHO_LIMIT:
        return text[:HOST_ECHO_LIMIT] + "..."
    return text


def resolve_allowed_hosts(
    host: str,
    *,
    allow_remote: bool,
    allowed_hosts: Sequence[str],
) -> tuple[frozenset[str], bool]:
    """Return ``(accepted hostnames, remote)`` for a bind request (spec 11.3).

    A loopback bind accepts the loopback names plus its own bind host; a
    non-loopback bind requires ``--allow-remote`` and at least one explicit
    ``--allowed-host`` and then accepts only those.
    """
    if is_loopback_host(host):
        return LOOPBACK_HOSTNAMES | {host.strip().lower()}, False
    if not allow_remote:
        raise UsageError(
            f"Refusing to bind to non-loopback host {host!r}: it would expose the "
            "board beyond this machine. Add --allow-remote to accept the risk."
        )
    if not allowed_hosts:
        raise UsageError(
            "--allow-remote requires at least one --allowed-host value: the "
            "Host header allowlist for a non-loopback bind."
        )
    return frozenset(item.strip().lower() for item in allowed_hosts), True


# --------------------------------------------------------------------------- #
# rendering helpers
# --------------------------------------------------------------------------- #


def _esc(value: object) -> str:
    """HTML-escape untrusted text; the only path task text takes to a page."""
    return html.escape("" if value is None else str(value), quote=True)


def _render(template_name: str, **values: str) -> str:
    text = (TEMPLATE_DIR / template_name).read_text(encoding="utf-8")
    return Template(text).substitute(values)


def _url(**params: str) -> str:
    pairs = {key: value for key, value in params.items() if value}
    return "/board" if not pairs else f"/board?{urlencode(pairs)}"


def _counts_line(counts: Mapping[str, Any]) -> str:
    return " ".join(f"{key}={counts[key]}" for key in counts)


def _flags_for(task: Mapping[str, Any]) -> list[tuple[str, str]]:
    flags: list[tuple[str, str]] = []
    if task["eligible"]:
        flags.append(("flag-eligible", "eligible"))
    if task["reviewable"]:
        flags.append(("flag-reviewable", "reviewable"))
    if task["blocked"]:
        flags.append(("flag-blocked", "blocked"))
    if task["claimed_by"]:
        flags.append(("flag-claimed", f"claimed by {task['claimed_by']}"))
    return flags


def _card(task: Mapping[str, Any]) -> str:
    labels = "".join(
        f'<span class="tag">{_esc(label)}</span>' for label in task["labels"]
    )
    flags = "".join(
        f'<span class="flag {css}">{_esc(text)}</span>' for css, text in _flags_for(task)
    )
    assignee = task["assignee"] or "-"
    return _render(
        "card.html",
        id=_esc(task["id"]),
        title=_esc(task["title"]),
        status=_esc(task["status"]),
        priority=_esc(task["priority"]),
        assignee=_esc(assignee),
        labels=labels,
        flags=flags,
    )


def _filter_form(query: Mapping[str, list[str]]) -> str:
    def selected(key: str, option: str) -> str:
        values = query.get(key)
        return " selected" if values and values[0] == option else ""

    def checked(key: str) -> str:
        return " checked" if query.get(key) else ""

    status_options = "".join(
        f'<option value="{_esc(status)}"{selected("status", status)}>{_esc(status)}</option>'
        for status in STATUSES
    )
    priority_options = "".join(
        f'<option value="{_esc(pri)}"{selected("priority", pri)}>{_esc(pri)}</option>'
        for pri in PRIORITIES
    )
    def value_of(key: str) -> str:
        values = query.get(key)
        return values[0] if values else ""

    return _render(
        "filters.html",
        status_options=status_options,
        priority_options=priority_options,
        assignee=_esc(value_of("assignee")),
        label=_esc(value_of("label")),
        blocked_checked=checked("blocked"),
        eligible_checked=checked("eligible"),
    )


def _board_columns(
    tasks: Sequence[Mapping[str, Any]],
    *,
    status_selected: bool,
    query: Mapping[str, list[str]],
) -> str:
    columns: list[str] = []
    for status in STATUSES:
        group = [task for task in tasks if task["status"] == status]
        shown: Sequence[Mapping[str, Any]] = group
        hidden = 0
        if status in TERMINAL_STATUSES and not status_selected:
            recent = sorted(group, key=lambda item: str(item["updated_at"]), reverse=True)
            shown = recent[:WEB_TERMINAL_LIMIT]
            hidden = len(recent) - len(shown)
        cards = "".join(_card(task) for task in shown)
        show_all = ""
        if hidden:
            params = {key: values[0] for key, values in query.items() if values}
            params["status"] = status
            show_all = _render(
                "show_all.html",
                count=str(hidden),
                href=_esc(_url(**params)),
            )
        columns.append(
            _render(
                "column.html",
                status=_esc(status),
                total=str(len(group)),
                cards=cards,
                show_all=show_all,
            )
        )
    return "".join(columns)


def _dependency_list(graph: Mapping[str, Any]) -> str:
    """The server-rendered indented fallback: works with JavaScript disabled."""
    nodes: list[Mapping[str, Any]] = list(graph["nodes"])
    depths = _topological_depths(nodes, graph["edges"])
    prereqs: dict[str, list[str]] = {}
    for edge in graph["edges"]:
        prereqs.setdefault(str(edge["target"]), []).append(str(edge["source"]))

    def order(node: Mapping[str, Any]) -> tuple[int, int]:
        return (depths.get(str(node["id"]), 0), _sequence(node))

    lines: list[str] = []
    for node in sorted(nodes, key=order):
        node_id = str(node["id"])
        depth = min(depths.get(node_id, 0), MAX_INDENT_DEPTH)
        needs = ", ".join(prereqs.get(node_id, [])) or "none"
        lines.append(
            _render(
                "dep_line.html",
                depth=str(depth),
                id=_esc(node_id),
                title=_esc(node["title"]),
                status=_esc(node["status"]),
                needs=_esc(needs),
            )
        )
    if not lines:
        return "<p class=\"empty\">No tasks yet.</p>"
    return "".join(lines)


def _sequence(node: Mapping[str, Any]) -> int:
    text = str(node["id"])
    _, _, tail = text.partition("-")
    return int(tail) if tail.isdigit() else 0


def _topological_depths(
    nodes: Sequence[Mapping[str, Any]], edges: Sequence[Mapping[str, Any]]
) -> dict[str, int]:
    """Longest prerequisite distance from a root, computed iteratively."""
    depth = {str(node["id"]): 0 for node in nodes}
    remaining: dict[str, int] = dict.fromkeys(depth, 0)
    dependents: dict[str, list[str]] = {}
    for edge in edges:
        source = str(edge["source"])
        target = str(edge["target"])
        if source not in depth or target not in depth:
            continue
        remaining[target] += 1
        dependents.setdefault(source, []).append(target)
    queue = [node_id for node_id in depth if remaining[node_id] == 0]
    while queue:
        current = queue.pop(0)
        for target in dependents.get(current, []):
            depth[target] = max(depth[target], depth[current] + 1)
            remaining[target] -= 1
            if remaining[target] == 0:
                queue.append(target)
    return depth


def _badge_list(items: Sequence[Mapping[str, Any]]) -> str:
    if not items:
        return '<p class="empty">None.</p>'
    rows = "".join(_card(task) for task in items)
    return f'<ul class="cards">{rows}</ul>'


def _blocker_list(task: Mapping[str, Any]) -> str:
    entries = list(task["blockers"])
    if not entries:
        return '<p class="empty">No derived blockers.</p>'
    rows = "".join(
        f'<li class="blocker"><code>{_esc(entry.get("code"))}</code>'
        f' {_esc(entry.get("task_id") or entry.get("reason") or "")}</li>'
        for entry in entries
    )
    return f'<ul class="blockers">{rows}</ul>'


def _note_list(notes: Sequence[Mapping[str, Any]]) -> str:
    if not notes:
        return '<p class="empty">No notes.</p>'
    rows = "".join(
        _render(
            "note.html",
            timestamp=_esc(note["timestamp"]),
            actor=_esc(note["actor"]),
            text=_esc(note["payload"].get("text", "")),
        )
        for note in notes
    )
    return f'<ul class="notes">{rows}</ul>'


def _history_list(events: Sequence[Mapping[str, Any]]) -> str:
    if not events:
        return '<p class="empty">No history.</p>'
    rows = "".join(
        _render(
            "history.html",
            timestamp=_esc(event["timestamp"]),
            actor=_esc(event["actor"]),
            event_type=_esc(event["event_type"]),
            payload=_esc(json.dumps(event["payload"], sort_keys=True)),
        )
        for event in events
    )
    return f'<ul class="history">{rows}</ul>'


def _legend() -> str:
    shapes = (
        ("shape-square", "done"),
        ("shape-circle", "eligible"),
        ("shape-diamond", "in progress"),
        ("shape-hexagon", "review"),
        ("shape-crossed", "blocked"),
        ("shape-octagon", "cancelled"),
        ("shape-dashed", "backlog / ready"),
    )
    items = "".join(
        f'<li class="legend-item {css}"><span class="legend-shape"></span>'
        f"<span>{_esc(label)}</span></li>"
        for css, label in shapes
    )
    return f'<ul class="legend">{items}</ul>'


# --------------------------------------------------------------------------- #
# the HTTP server
# --------------------------------------------------------------------------- #


@dataclass(slots=True, frozen=True)
class ServerConfig:
    """What the running server serves; built once, before the socket opens."""

    database_path: Path
    allowed_hosts: frozenset[str]
    remote: bool


class BoardServer(ThreadingHTTPServer):
    """A threaded HTTP server carrying its immutable :class:`ServerConfig`."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], config: ServerConfig) -> None:
        super().__init__(address, _BoardRequestHandler)
        self.config = config

    @property
    def port(self) -> int:
        return int(self.server_address[1])


class _BoardRequestHandler(BaseHTTPRequestHandler):
    """Request handling for the read-only board."""

    server_version = "tackboard"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def version_string(self) -> str:
        """Report only our own version (no Python version, no trailing space)."""
        return self.server_version

    def __getattr__(self, name: str) -> Any:
        """Answer 405 for *any* method without a handler of its own.

        The standard verbs are defined explicitly below; this catches the rest
        (a made-up verb, ``OPTIONS`` from a scanner, anything else) instead of
        letting the standard library answer 501. Only ``do_*`` names are
        intercepted, so ordinary attribute lookups still raise ``AttributeError``.
        """
        if name.startswith("do_"):
            return self._reject_method
        raise AttributeError(name)

    # -- entry points ------------------------------------------------------ #

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's naming
        self._dispatch(include_body=True)

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch(include_body=False)

    def do_POST(self) -> None:  # noqa: N802
        self._reject_method()

    def do_PUT(self) -> None:  # noqa: N802
        self._reject_method()

    def do_PATCH(self) -> None:  # noqa: N802
        self._reject_method()

    def do_DELETE(self) -> None:  # noqa: N802
        self._reject_method()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._reject_method()

    def do_TRACE(self) -> None:  # noqa: N802
        self._reject_method()

    def do_CONNECT(self) -> None:  # noqa: N802
        self._reject_method()

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        """Server logs go to stderr, never stdout (spec 10.1)."""
        sys.stderr.write(
            f"[{self.log_date_time_string()}] {self.address_string()} {format % args}\n"
        )

    # -- plumbing ---------------------------------------------------------- #

    @property
    def _server(self) -> BoardServer:
        server = self.server
        assert isinstance(server, BoardServer)
        return server

    @property
    def _config(self) -> ServerConfig:
        return self._server.config

    @property
    def _config_port(self) -> int:
        return self._server.port

    def _reject_method(self) -> None:
        self.close_connection = True
        body = f"Method {self.command} is not allowed: this board is read-only.\n"
        self._send(
            HTTPStatus.METHOD_NOT_ALLOWED,
            "text/plain; charset=utf-8",
            body.encode("utf-8"),
            include_body=True,
            extra_headers={"Allow": "GET, HEAD"},
        )

    def _host_refusal(self) -> str | None:
        """Return the 403 body when the ``Host`` header is refused, else ``None``.

        The accepted hostnames and the bound port are already public (the
        startup banner and the ``--json`` startup record carry them), so the
        refusal names both. Every refusal keeps one ``403`` status; only the
        body distinguishes an absent header, an unparseable one, a ``host:port``
        whose port differs from the bound port, and a host outside the
        allowlist.
        """
        raw = self.headers.get("Host")
        if not raw:
            return self._host_refusal_body("no Host header was sent")
        try:
            host, port = parse_host_header(raw)
        except ValueError:
            return self._host_refusal_body(
                f"{_echo_host(raw)} is not a valid host or host:port value"
            )
        if port is not None and port != self._config_port:
            return self._host_refusal_body(
                f"{_echo_host(raw)} names port {port}, but this server is bound "
                f"to port {self._config_port}"
            )
        if host not in self._config.allowed_hosts:
            return self._host_refusal_body(_echo_host(raw))
        return None

    def _host_refusal_body(self, reason: str) -> str:
        """The refusal body: the offending value, the allowlist and the bound port."""
        accepted = ", ".join(sorted(self._config.allowed_hosts))
        return (
            f"Host header not allowed: {reason}.\n"
            f"Accepted Host names: {accepted}.\n"
            f"A host:port Host header must carry the port this server is bound "
            f"to ({self._config_port}).\n"
            f"Add --allowed-host NAME to accept another host; a non-loopback "
            f"bind also needs --allow-remote.\n"
        )

    def _dispatch(self, *, include_body: bool) -> None:
        try:
            parsed = urlsplit(self.path)
            path = unquote(parsed.path)
            query = parse_qs(parsed.query, keep_blank_values=True)
        except ValueError:
            status, content_type, body = _plain_response(
                HTTPStatus.BAD_REQUEST, "Malformed request target.\n"
            )
            self._send(status, content_type, body, include_body=include_body)
            return

        refusal = self._host_refusal()
        if refusal is not None:
            status, content_type, body = _plain_response(HTTPStatus.FORBIDDEN, refusal)
            self._send(status, content_type, body, include_body=include_body)
            return

        status, content_type, body = self._respond(path, query)

        if status == HTTPStatus.METHOD_NOT_ALLOWED:
            self._send(
                status,
                content_type,
                body,
                include_body=include_body,
                extra_headers={"Allow": "GET, HEAD"},
            )
            return
        self._send(status, content_type, body, include_body=include_body)

    def _respond(
        self, path: str, query: Mapping[str, list[str]]
    ) -> tuple[int, str, bytes]:
        if path.startswith("/static/"):
            return self._static(path[len("/static/") :])
        if path in {"", "/", "/board"}:
            return self._page(lambda conn: self._board_page(conn, query))
        if path == "/graph":
            return self._page(lambda conn: self._graph_page(conn, query))
        if path.startswith("/task/"):
            return self._page(lambda conn: self._task_page(conn, path[len("/task/") :]))
        if path == "/api/board":
            return self._api(
                lambda conn: views.board_document(conn, **_board_filters(query))
            )
        if path == "/api/graph":
            task_id = _first(query, "task") or None
            return self._api(lambda conn: views.graph_document(conn, task_id))
        if path.startswith("/api/tasks/"):
            task_id = path[len("/api/tasks/") :]
            return self._api(lambda conn: views.task_document(conn, task_id))
        return _plain_response(HTTPStatus.NOT_FOUND, "Not found.\n")

    # -- handlers ---------------------------------------------------------- #

    def _open(self) -> sqlite3.Connection:
        return schema.open_database(self._config.database_path, read_only=True)

    def _api(
        self, handler: Callable[[sqlite3.Connection], dict[str, Any]]
    ) -> tuple[int, str, bytes]:
        conn = self._open()
        try:
            document = handler(conn)
        except TackError as exc:
            envelope: dict[str, Any] = {"schema_version": JSON_SCHEMA_VERSION}
            envelope.update(exc.to_json())
            return (
                _http_status(exc),
                JSON_CONTENT_TYPE,
                _json_bytes(envelope),
            )
        except sqlite3.Error as exc:  # pragma: no cover - defensive
            return (
                HTTPStatus.INTERNAL_SERVER_ERROR,
                JSON_CONTENT_TYPE,
                _json_bytes(
                    {
                        "schema_version": JSON_SCHEMA_VERSION,
                        "error": {
                            "code": "internal",
                            "message": str(exc),
                            "exit_code": 1,
                            "retryable": False,
                            "details": {},
                        },
                    }
                ),
            )
        finally:
            conn.close()
        return HTTPStatus.OK, JSON_CONTENT_TYPE, _json_bytes(document)

    def _page(
        self,
        handler: Callable[[sqlite3.Connection], str],
    ) -> tuple[int, str, bytes]:
        conn = self._open()
        try:
            body = handler(conn)
        except TackError as exc:
            status = _http_status(exc)
            page = _render(
                "error.html",
                title=_esc(f"{status} {HTTPStatus(status).phrase}"),
                heading=_esc(f"{status} {HTTPStatus(status).phrase}"),
                message=_esc(exc.message),
            )
            return status, HTML_CONTENT_TYPE, page.encode("utf-8")
        finally:
            conn.close()
        return HTTPStatus.OK, HTML_CONTENT_TYPE, body.encode("utf-8")

    def _board_page(
        self, conn: sqlite3.Connection, query: Mapping[str, list[str]]
    ) -> str:
        filters = _board_filters(query)
        document = views.board_document(conn, **filters)
        content = _render(
            "board.html",
            filters=_filter_form(query),
            counts_line=_esc(_counts_line(document["counts"])),
            project=_esc(document["project"]),
            database=_esc(str(self._config.database_path)),
            columns=_board_columns(
                document["tasks"],
                status_selected=filters["status"] is not None,
                query=query,
            ),
            interval=str(AUTO_REFRESH_SECONDS),
        )
        return self._shell(
            title="Board",
            project=document["project"],
            content=content,
            scripts='<script src="/static/refresh.js" defer></script>',
        )

    def _graph_page(
        self, conn: sqlite3.Connection, query: Mapping[str, list[str]]
    ) -> str:
        task_id = _first(query, "task") or None
        document = views.graph_document(conn, task_id)
        project = schema.database_label(conn)
        content = _render(
            "graph.html",
            task=_esc(task_id or ""),
            node_count=str(len(document["nodes"])),
            edge_count=str(len(document["edges"])),
            legend=_legend(),
            fallback=_dependency_list(document),
        )
        return self._shell(
            title="Dependency graph",
            project=project,
            content=content,
            scripts='<script src="/static/graph.js" defer></script>',
        )

    def _task_page(self, conn: sqlite3.Connection, task_id: str) -> str:
        document = views.task_document(conn, task_id)
        task = document["task"]
        claim = document["claim"]
        claim_text = "none"
        if claim is not None:
            claim_text = (
                f"{claim['agent_id']} (since {claim['claimed_at']}, "
                f"{claim['age_seconds']}s ago)"
            )
        content = _render(
            "task.html",
            id=_esc(task["id"]),
            title=_esc(task["title"]),
            description=_esc(task["description"]) or "&#8212;",
            status=_esc(task["status"]),
            priority=_esc(task["priority"]),
            assignee=_esc(task["assignee"] or "-"),
            labels=_esc(", ".join(task["labels"]) or "-"),
            claim=_esc(claim_text),
            ready=_esc(str(task["dependency_ready"])),
            eligible=_esc(str(task["eligible"])),
            reviewable=_esc(str(task["reviewable"])),
            blocked=_esc(str(task["blocked"])),
            blocked_reason=_esc(task["blocked_reason"] or "-"),
            blockers=_blocker_list(task),
            created=_esc(task["created_at"]),
            updated=_esc(task["updated_at"]),
            completed=_esc(task["completed_at"] or "-"),
            version=_esc(str(task["version"])),
            prerequisites=_badge_list(document["prerequisites"]),
            dependents=_badge_list(document["dependents"]),
            notes=_note_list(document["notes"]),
            history=_history_list(document["history"]),
        )
        return self._shell(
            title=f"{task['id']} {task['title']}",
            project=schema.database_label(conn),
            content=content,
            scripts="",
        )

    def _shell(self, *, title: str, project: str, content: str, scripts: str) -> str:
        return _render(
            "base.html",
            title=_esc(title),
            project=_esc(project),
            database=_esc(str(self._config.database_path)),
            content=content,
            scripts=scripts,
        )

    def _static(self, name: str) -> tuple[int, str, bytes]:
        if not name or Path(name).name != name or name.startswith("."):
            return HTTPStatus.NOT_FOUND, HTML_CONTENT_TYPE, b"Not found.\n"
        target = STATIC_DIR / name
        content_type = _STATIC_CONTENT_TYPES.get(target.suffix.lower())
        if content_type is None or not target.is_file():
            return HTTPStatus.NOT_FOUND, HTML_CONTENT_TYPE, b"Not found.\n"
        return HTTPStatus.OK, content_type, target.read_bytes()

    # -- responses --------------------------------------------------------- #

    def _send(
        self,
        status: int,
        content_type: str,
        body: bytes,
        *,
        include_body: bool,
        extra_headers: Mapping[str, str] | None = None,
    ) -> None:
        self.send_response(int(status))
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        self.send_header("X-Content-Type-Options", NOSNIFF)
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        if self.close_connection or int(status) >= 400:
            self.close_connection = True
            self.send_header("Connection", "close")
        self.end_headers()
        if include_body and body:
            self.wfile.write(body)


def _plain_response(status: int, message: str) -> tuple[int, str, bytes]:
    return status, "text/plain; charset=utf-8", message.encode("utf-8")


def _first(query: Mapping[str, list[str]], key: str) -> str:
    values = query.get(key)
    return values[0] if values else ""


def _board_filters(query: Mapping[str, list[str]]) -> dict[str, Any]:
    return {
        "status": _first(query, "status") or None,
        "assignee": _first(query, "assignee") or None,
        "priority": _first(query, "priority") or None,
        "label": _first(query, "label") or None,
        "blocked": True if query.get("blocked") else None,
        "eligible": True if query.get("eligible") else None,
    }


def _http_status(error: TackError) -> int:
    if isinstance(error, NotFoundError):
        return HTTPStatus.NOT_FOUND
    if isinstance(error, (UsageError, ValidationError)):
        return HTTPStatus.BAD_REQUEST
    return HTTPStatus.INTERNAL_SERVER_ERROR


def _json_bytes(document: Mapping[str, Any]) -> bytes:
    return json.dumps(document, ensure_ascii=False).encode("utf-8")


# --------------------------------------------------------------------------- #
# lifecycle
# --------------------------------------------------------------------------- #


def create_server(config: ServerConfig, host: str, port: int) -> BoardServer:
    """Bind a server (``port=0`` asks the OS for a free port)."""
    return BoardServer((host, port), config)


def startup_record(server: BoardServer) -> dict[str, Any]:
    """The one JSON record ``tack serve --json`` prints to stdout."""
    config = server.config
    return {
        "schema_version": JSON_SCHEMA_VERSION,
        "event": "serve.start",
        "database": str(config.database_path),
        "host": str(server.server_address[0]),
        "port": server.port,
        "url": f"http://{_url_host(str(server.server_address[0]))}:{server.port}/",
        "remote": config.remote,
        "allowed_hosts": sorted(config.allowed_hosts),
        "authentication": "none",
    }


def _url_host(host: str) -> str:
    return f"[{host}]" if ":" in host else host


def banner(server: BoardServer) -> str:
    """The human-facing startup banner, written to stderr."""
    config = server.config
    lines = [
        f"tackboard serving {config.database_path}",
        f"  on http://{_url_host(str(server.server_address[0]))}:{server.port}/",
        "  read-only: GET and HEAD only, no authentication",
    ]
    if config.remote:
        lines.append(
            "  WARNING: bound to a non-loopback interface; the board has no "
            "authentication. Anyone who can reach it can read the whole board."
        )
    return "\n".join(lines)


def serve(
    config: ServerConfig,
    host: str,
    port: int,
    *,
    on_record: Callable[[dict[str, Any]], None] | None = None,
    announce: Callable[[str], None] | None = None,
) -> None:
    """Run the server until interrupted (spec 11.3)."""
    server = create_server(config, host, port)
    record = startup_record(server)
    if on_record is not None:
        on_record(record)
    if announce is not None:
        announce(banner(server))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()


__all__ = [
    "AUTO_REFRESH_SECONDS",
    "CONTENT_SECURITY_POLICY",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "JSON_SCHEMA_VERSION",
    "BoardServer",
    "ServerConfig",
    "banner",
    "create_server",
    "is_loopback_host",
    "parse_host_header",
    "resolve_allowed_hosts",
    "serve",
    "startup_record",
]
