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

import hashlib
import html
import ipaddress
import json
import re
import sqlite3
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from string import Template
from typing import Any
from urllib.parse import parse_qs, unquote, urlencode, urlsplit

from . import memo, schema, views
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

#: Status glyphs and labels: the one visual language shared by board, graph
#: and task page. The glyph is aria-hidden wherever it is rendered; the label
#: carries the meaning for assistive technology. Keep this map in sync with
#: ``STATUS_GLYPH`` in static/graph.js and ``GLYPHS`` in static/board.js; the
#: test suite asserts all three agree.
STATUS_GLYPHS: dict[str, str] = {
    "backlog": "…",
    "ready": "▷",
    "in_progress": "▶",
    "review": "◐",
    "done": "✓",
    "cancelled": "✕",
}

STATUS_LABELS: dict[str, str] = {
    "backlog": "backlog",
    "ready": "ready",
    "in_progress": "in progress",
    "review": "review",
    "done": "done",
    "cancelled": "cancelled",
}

#: Filled pips encode priority without relying on colour.
PRIORITY_PIPS: dict[str, str] = {
    "low": "▁",
    "normal": "▁▁",
    "high": "▁▁▁",
    "urgent": "▁▁▁▁",
}


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


_TASK_ID_RE = re.compile(r"(?<![A-Za-z0-9_-])([A-Z]{1,4}-\d+)(?![A-Za-z0-9_-])")


def _nowrap_task_ids_html(text: str) -> str:
    """Escape text and keep task IDs unbroken in narrow layouts.

    A hyphen is a valid line-break point, so a plain sentence such as
    ``blocked: prerequisite unfinished T-000052`` can split the ID across two
    lines. Wrapping the ID in ``.nowrap`` lets the sentence wrap *before* the
    ID while keeping the ID itself atomic.
    """
    return _TASK_ID_RE.sub(
        lambda match: f'<span class="nowrap">{match.group(1)}</span>',
        _esc(text),
    )


def _render(template_name: str, **values: str) -> str:
    text = (TEMPLATE_DIR / template_name).read_text(encoding="utf-8")
    return Template(text).substitute(values)


def _url(**params: str) -> str:
    pairs = {key: value for key, value in params.items() if value}
    return "/board" if not pairs else f"/board?{urlencode(pairs)}"


def _counts_line(counts: Mapping[str, Any]) -> str:
    return " ".join(f"{key}={counts[key]}" for key in counts)


def _etag(body: bytes) -> str:
    """A strong validator: sha256 truncated to 16 bytes, hex, quoted (design 6.1)."""
    return f'"{hashlib.sha256(body).hexdigest()[:32]}"'


def _if_none_match_matches(header: str | None, etag: str) -> bool:
    """True when an ``If-None-Match`` value names this validator."""
    if not header:
        return False
    for candidate in header.split(","):
        value = candidate.strip()
        if value.startswith("W/"):
            value = value[2:].strip()
        if value in {"*", etag}:
            return True
    return False


def _status_glyph(status: str) -> str:
    return STATUS_GLYPHS.get(status, "•")


def _status_label(status: str) -> str:
    return STATUS_LABELS.get(status, status)


def _relative_age(iso: str | None, now: datetime) -> str:
    """Compact relative age for a claim badge (``12m``); empty when unparseable."""
    if not iso:
        return ""
    try:
        stamp = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return ""
    seconds = max(0, int((now - stamp).total_seconds()))
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h"
    return f"{hours // 24}d"


def _display_time(iso: str | None) -> str:
    """Short human time for the eye; the ISO value stays in ``datetime``."""
    if not iso:
        return "—"
    try:
        stamp = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return iso
    return stamp.strftime("%d %b %H:%M")


def _time_html(iso: str | None) -> str:
    if not iso:
        return "—"
    return f'<time datetime="{_esc(iso)}">{_esc(_display_time(iso))}</time>'


def _fact_labels_html(task: Mapping[str, Any]) -> str:
    labels = task.get("labels") or []
    if not labels:
        return "—"
    return " ".join(f'<span class="tag">{_esc(label)}</span>' for label in labels)


def _claim_fact_html(
    claim: Mapping[str, Any] | None, now: datetime, *, stale: bool = False
) -> str:
    if claim is None:
        return "none"
    holder = _esc(claim["agent_id"])
    claimed_at = _esc(claim["claimed_at"])
    age = _relative_age(str(claim["claimed_at"]), now)
    stale_html = ' <span class="claim-stale">\u26a0 stale claim</span>' if stale else ""
    return (
        f"{holder}, claimed "
        f'<time datetime="{claimed_at}" title="{claimed_at}">{_esc(age)} ago</time>'
        f"{stale_html}"
    )


def _readiness_chips_html(
    task: Mapping[str, Any], claim: Mapping[str, Any] | None
) -> str:
    chips: list[str] = []
    if claim is not None:
        chips.append(
            '<li><span class="state-chip" data-state="claimed">'
            f"\u25ce claimed by {_esc(claim['agent_id'])}</span></li>"
        )
    if task.get("claim_stale"):
        chips.append(
            '<li><span class="state-chip" data-state="stale">'
            "\u26a0 stale claim</span></li>"
        )
    if task.get("eligible"):
        chips.append(
            '<li><span class="state-chip" data-state="eligible">'
            "▷ eligible</span></li>"
        )
    if task.get("reviewable"):
        chips.append(
            '<li><span class="state-chip" data-state="reviewable">'
            "◐ reviewable</span></li>"
        )
    if task.get("blocked"):
        chips.append(
            '<li><span class="state-chip" data-state="blocked">'
            "⊘ blocked</span></li>"
        )
    if not chips:
        return ""
    return f'<ul class="readiness" aria-label="Readiness">{"".join(chips)}</ul>'


def _readiness_sentence(
    task: Mapping[str, Any], claim: Mapping[str, Any] | None
) -> str:
    states = [
        "eligible" if task.get("eligible") else "not eligible",
        "reviewable" if task.get("reviewable") else "not reviewable",
        "blocked" if task.get("blocked") else "not blocked",
    ]
    if claim is not None:
        states.append(f"claimed by {claim['agent_id']}")
    if task.get("claim_stale"):
        states.append("stale claim")
    return "Readiness: " + ", ".join(states) + "."


def _blocker_text(entry: Mapping[str, Any]) -> str:
    code = str(entry.get("code") or "blocked")
    task_id = entry.get("task_id")
    reason = entry.get("reason")
    if code == "prerequisite_unfinished" and task_id:
        return f"{task_id} is not done"
    if code == "prerequisite_cancelled" and task_id:
        return f"{task_id} is cancelled"
    if reason:
        return str(reason)
    return code.replace("_", " ")


def _blocked_reason_text(task: Mapping[str, Any]) -> str:
    reason = str(task.get("blocked_reason") or "").strip()
    if reason:
        return reason
    blockers = task.get("blockers") or []
    if blockers:
        return _blocker_text(blockers[0])
    return "prerequisite unfinished"


def _blocked_summary_html(task: Mapping[str, Any]) -> str:
    if not task.get("blocked"):
        return ""
    return (
        '<p class="task-blocked-summary">Blocked by: '
        f"{_nowrap_task_ids_html(_blocked_reason_text(task))}</p>"
    )


def _dependency_links(
    items: Sequence[Mapping[str, Any]],
    now: datetime,
    *,
    prerequisite: bool,
) -> str:
    if not items:
        return '<p class="empty">None.</p>'
    rows: list[str] = []
    for item in items:
        status = str(item["status"])
        not_done = ""
        if prerequisite and status != "done":
            not_done = ' <span class="link-list__warn">not done</span>'
        rows.append(
            _render(
                "dep_line.html",
                id=_esc(item["id"]),
                title=_esc(item["title"]),
                status=_esc(status),
                glyph=_esc(_status_glyph(status)),
                status_label=_esc(_status_label(status)),
                not_done=not_done,
            )
        )
    return f'<ul class="link-list">{"".join(rows)}</ul>'


def _event_kind(event_type: str) -> str:
    kinds = {
        "task.created": "created",
        "task.updated": "updated",
        "task.status_changed": "status",
        "task.completed": "completed",
        "task.reopened": "reopened",
        "task.cancelled": "cancelled",
        "task.restored": "restored",
        "task.claimed": "claimed",
        "task.released": "released",
        "dependency.added": "dependency",
        "dependency.removed": "dependency",
        "note": "note",
    }
    return kinds.get(event_type, event_type)


def _event_payload_text(event: Mapping[str, Any]) -> str:
    event_type = str(event.get("event_type") or "")
    payload = event.get("payload") or {}
    if event_type == "task.status_changed":
        return f"{payload.get('from', '?')} → {payload.get('to', '?')}"
    if event_type == "task.created":
        parts = ["task created"]
        if payload.get("priority"):
            parts.append(f"priority {payload['priority']}")
        return ", ".join(parts)
    if event_type == "task.updated":
        fields = payload.get("fields") or []
        update_parts: list[str] = []
        if "assignee" in fields and "previous_assignee" in payload:
            previous = payload.get("previous_assignee")
            if "assignee" in payload:
                new = payload.get("assignee")
                update_parts.append(
                    "assignee: "
                    f"{'unassigned' if previous is None else previous} "
                    f"\u2192 {'unassigned' if new is None else new}"
                )
            else:
                # An event written before the new value was recorded: name the
                # previous holder honestly rather than inventing a successor.
                update_parts.append(
                    "assignee: "
                    f"{'unassigned' if previous is None else previous} "
                    "\u2192 (new value not recorded)"
                )
            others = [str(field) for field in fields if field != "assignee"]
            if others:
                update_parts.append(", ".join(others) + " updated")
            return "; ".join(update_parts)
        if fields:
            return "updated: " + ", ".join(str(field) for field in fields)
        return "task updated"
    if event_type == "task.claimed":
        holder = payload.get("agent") or payload.get("agent_id") or "agent"
        return f"claimed by {holder}"
    if event_type == "task.released":
        return "claim released"
    if event_type == "task.completed":
        reason = payload.get("reason")
        return "completed" + (f": {reason}" if reason else "")
    if event_type == "task.cancelled":
        reason = payload.get("reason")
        return "cancelled" + (f": {reason}" if reason else "")
    if event_type == "task.reopened":
        return "reopened"
    if event_type == "task.restored":
        return "restored"
    if event_type == "dependency.added":
        return f"dependency added: {payload.get('prerequisite_id', '?')}"
    if event_type == "dependency.removed":
        return f"dependency removed: {payload.get('prerequisite_id', '?')}"
    if not payload:
        return event_type
    pairs = ", ".join(
        f"{key}: {value}" for key, value in sorted(payload.items())
    )
    return f"{event_type}: {pairs}"


def _timeline(
    notes: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
) -> str:
    items: list[tuple[str, str]] = []
    for note in notes:
        items.append(
            (
                str(note["timestamp"]),
                _render(
                    "note.html",
                    timestamp=_esc(note["timestamp"]),
                    timestamp_display=_esc(_display_time(note["timestamp"])),
                    actor=_esc(note["actor"]),
                    text=_esc(note["payload"].get("text", "")),
                ),
            )
        )
    for event in events:
        if event.get("event_type") == "note":
            continue
        items.append(
            (
                str(event["timestamp"]),
                _render(
                    "history.html",
                    timestamp=_esc(event["timestamp"]),
                    timestamp_display=_esc(_display_time(event["timestamp"])),
                    actor=_esc(event["actor"]),
                    event_type=_esc(_event_kind(str(event["event_type"]))),
                    payload=_esc(_event_payload_text(event)),
                ),
            )
        )
    if not items:
        return '<p class="empty">No notes or history.</p>'
    items.sort(key=lambda pair: pair[0], reverse=True)
    return (
        '<ol class="timeline" aria-label="Notes and history, newest first">'
        + "".join(item for _, item in items)
        + "</ol>"
    )


def _labels_html(task: Mapping[str, Any]) -> str:
    labels = task.get("labels") or []
    if not labels:
        return ""
    items = "".join(f'<li class="tag">{_esc(label)}</li>' for label in labels)
    return f'<ul class="card__labels" aria-label="Labels">{items}</ul>'


def _state_chips(task: Mapping[str, Any]) -> str:
    chips: list[str] = []
    if task.get("eligible"):
        chips.append('<span class="state-chip" data-state="eligible">▷ eligible</span>')
    if task.get("reviewable"):
        chips.append('<span class="state-chip" data-state="reviewable">◐ reviewable</span>')
    return "".join(chips)


def _blocked_html(task: Mapping[str, Any]) -> str:
    if not task.get("blocked"):
        return ""
    reason = str(task.get("blocked_reason") or "").strip()
    if not reason:
        blockers = task.get("blockers") or []
        if blockers:
            first = blockers[0]
            code = str(first.get("code") or "blocked").replace("_", " ")
            detail = first.get("task_id") or first.get("reason") or ""
            reason = f"{code} {detail}".strip()
    if not reason:
        reason = "prerequisite unfinished"
    return f'<p class="card__blocked">blocked: {_nowrap_task_ids_html(reason)}</p>'


def _claim_html(task: Mapping[str, Any], now: datetime) -> str:
    holder = task.get("claimed_by")
    claimed_at = task.get("claimed_at")
    if not holder:
        return ""
    age = _relative_age(str(claimed_at) if claimed_at else None, now)
    title = (
        f"{holder} \u2014 claimed {age} ago ({claimed_at})"
        if age
        else f"{holder} ({claimed_at})"
    )
    stale = (
        ' <span class="claim-stale" title="Past the configured stale-claim threshold">'
        "\u26a0 stale</span>"
        if task.get("claim_stale")
        else ""
    )
    return (
        '<p class="card__claim">'
        f'<span class="claim-badge" title="{_esc(title)}">@{_esc(holder)}</span> '
        f'<time class="card__claim-age" datetime="{_esc(claimed_at)}" '
        f'title="{_esc(claimed_at)}">{_esc(age)}</time>'
        f"{stale}"
        "</p>"
    )


def _card(task: Mapping[str, Any], now: datetime) -> str:
    status = str(task["status"])
    return _render(
        "card.html",
        id=_esc(task["id"]),
        title=_esc(task["title"]),
        status=_esc(status),
        status_label=_esc(_status_label(status)),
        glyph=_esc(_status_glyph(status)),
        priority=_esc(task["priority"]),
        pips=_esc(PRIORITY_PIPS.get(str(task["priority"]), "")),
        assignee=_esc(task["assignee"] or "-"),
        labels=_labels_html(task),
        state_chips=_state_chips(task),
        blocked=_blocked_html(task),
        claim=_claim_html(task, now),
        blocked_flag="true" if task.get("blocked") else "false",
        claimed_flag="true" if task.get("claimed_by") else "false",
    )


def _without(query: Mapping[str, list[str]], key: str) -> str:
    """The board URL with one filter removed; other filters survive."""
    params = {name: values[0] for name, values in query.items() if values and name != key}
    return _url(**params)


def _active_chips(query: Mapping[str, list[str]]) -> str:
    """One removable chip per active filter; each chip is a plain GET link."""
    chips: list[str] = []
    status = _first(query, "status")
    if status:
        chip = (
            f'<span class="status-chip" data-status="{_esc(status)}">'
            f'<span class="status-chip__glyph" aria-hidden="true">'
            f"{_esc(_status_glyph(status))}</span> {_esc(_status_label(status))}</span>"
        )
        chips.append(
            f'<a class="chip" href="{_esc(_without(query, "status"))}" '
            f'aria-label="Remove status filter: {_esc(_status_label(status))}">'
            f'{chip}<span class="chip__x" aria-hidden="true">✕</span></a>'
        )
    for key, label in (
        ("assignee", "assignee"),
        ("priority", "priority"),
        ("label", "label"),
    ):
        value = _first(query, key)
        if value:
            chips.append(
                f'<a class="chip" href="{_esc(_without(query, key))}" '
                f'aria-label="Remove {label} filter: {_esc(value)}">'
                f'{_esc(label)}: <strong>{_esc(value)}</strong>'
                f'<span class="chip__x" aria-hidden="true">✕</span></a>'
            )
    for key, label in (("blocked", "blocked"), ("eligible", "eligible")):
        if query.get(key):
            chips.append(
                f'<a class="chip" href="{_esc(_without(query, key))}" '
                f'aria-label="Remove {label} filter">'
                f'{_esc(label)}<span class="chip__x" aria-hidden="true">✕</span></a>'
            )
    if not chips:
        return '<p class="filters__none">No filters — showing everything.</p>'
    return "".join(chips)


def _filter_form(query: Mapping[str, list[str]]) -> str:
    """Filter chips plus a native disclosure holding the full GET form.

    Active filters are plain links so removing one works without JavaScript;
    the disclosure submits as a normal GET when scripts are absent. Status is a
    radio group, not checkboxes: the existing contract accepts one status value
    and two ticked boxes would silently drop one.
    """

    def selected(key: str, option: str) -> str:
        return " selected" if _first(query, key) == option else ""

    def checked(key: str) -> str:
        return " checked" if query.get(key) else ""

    def value_of(key: str) -> str:
        return _first(query, key)

    status_radios = "".join(
        f'<label class="inline"><input type="radio" name="status" '
        f'value="{_esc(status)}"{" checked" if _first(query, "status") == status else ""}>'
        f" {_esc(_status_label(status))}</label>"
        for status in STATUSES
    )
    priority_options = "".join(
        f'<option value="{_esc(pri)}"{selected("priority", pri)}>{_esc(pri)}</option>'
        for pri in PRIORITIES
    )
    return _render(
        "filters.html",
        chips=_active_chips(query),
        status_radios=status_radios,
        priority_options=priority_options,
        assignee=_esc(value_of("assignee")),
        label=_esc(value_of("label")),
        blocked_checked=checked("blocked"),
        eligible_checked=checked("eligible"),
    )


def _count_anchor(
    *,
    href: str,
    value: int,
    key: str,
    active: bool,
    suffix: str,
    inner: str = "",
) -> str:
    """A summary count as one tab stop; the suffix completes the accessible name."""
    current = ' aria-current="true"' if active else ""
    return (
        f'<a class="count" href="{_esc(href)}" data-filter="{_esc(key)}"{current}>'
        f"{inner}"
        f'<span class="count__value">{value}</span>'
        f'<span class="sr-only"> {_esc(suffix)}</span></a>'
    )


def _summary_bar(document: Mapping[str, Any], query: Mapping[str, list[str]]) -> str:
    """The design 4.2 summary: headline four as a ``dl``, statuses as a ``ul``.

    Claimed is rendered as static text rather than a link: this leg keeps the
    existing six filter dimensions and the read models have no claimed filter,
    so a claimed link would be a dead control.
    """
    counts = document["counts"]
    active_status = _first(query, "status")
    headline = "".join(
        [
            "<div class=\"summary__item\"><dt>total</dt><dd>"
            + _count_anchor(
                href="/board", value=int(counts["tasks"]), key="all",
                active=False, suffix="tasks in total",
            )
            + "</dd></div>",
            "<div class=\"summary__item\"><dt>eligible</dt><dd>"
            + _count_anchor(
                href="/board?eligible=1", value=int(counts["eligible"]),
                key="eligible", active=bool(query.get("eligible")),
                suffix="eligible tasks",
            )
            + "</dd></div>",
            "<div class=\"summary__item\"><dt>blocked</dt><dd>"
            + _count_anchor(
                href="/board?blocked=1", value=int(counts["blocked"]),
                key="blocked", active=bool(query.get("blocked")),
                suffix="blocked tasks",
            )
            + "</dd></div>",
            "<div class=\"summary__item\"><dt>claimed</dt><dd>"
            f'<span class="count count--static" title="Claimed tasks are not '
            f'filterable in this view">{int(counts["claimed"])}</span>'
            '<span class="sr-only"> claimed tasks</span></dd></div>',
        ]
    )
    status_items = "".join(
        "<li>"
        + _count_anchor(
            href=f"/board?status={status}",
            value=int(counts[status]),
            key=f"status:{status}",
            active=active_status == status,
            suffix=f"{_status_label(status)} tasks",
            inner=(
                f'<span class="status-chip" data-status="{_esc(status)}">'
                f'<span class="status-chip__glyph" aria-hidden="true">'
                f"{_esc(_status_glyph(status))}</span> {_esc(_status_label(status))}</span>"
            ),
        )
        + "</li>"
        for status in STATUSES
    )
    return (
        '<section class="summary" aria-labelledby="summary-heading">'
        '<h2 id="summary-heading" class="sr-only">Board summary</h2>'
        f'<dl class="summary__counts">{headline}</dl>'
        f'<ul class="summary__status" aria-label="Counts by status">{status_items}</ul>'
        "</section>"
    )


def _board_columns(
    tasks: Sequence[Mapping[str, Any]],
    *,
    status_selected: bool,
    query: Mapping[str, list[str]],
    now: datetime,
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
        cards = "".join(_card(task, now) for task in shown)
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
                status_label=_esc(_status_label(status)),
                glyph=_esc(_status_glyph(status)),
                total=str(len(group)),
                cards=cards,
                empty='<p class="column__empty">No tasks</p>' if not group else "",
                show_all=show_all,
            )
        )
    return "".join(columns)


def _outline_counts(
    visible_nodes: int,
    total_nodes: int,
    visible_edges: int,
    total_edges: int,
    hidden: int,
) -> str:
    """The server-rendered count line for the dependency outline.

    The short form is unchanged when nothing is hidden; when hide-closed is on
    both the visible and the full totals appear so truncation is never silent.
    """
    if hidden:
        return (
            f"nodes={visible_nodes} of {total_nodes} \u00b7 "
            f"edges={visible_edges} of {total_edges} \u00b7 {hidden} hidden"
        )
    return f"nodes={visible_nodes} edges={visible_edges}"


def _claimed_suffix(node: Mapping[str, Any]) -> str:
    """The outline's claim text: the information survives without JavaScript."""
    holder = node.get("claimed_by")
    if not holder:
        return ""
    claimed_at = node.get("claimed_at")
    stale = " \u2014 stale" if node.get("claim_stale") else ""
    if claimed_at:
        return f"[claimed by {holder} since {claimed_at}{stale}]"
    return f"[claimed by {holder}{stale}]"


def _dependency_list(
    graph: Mapping[str, Any],
    *,
    nodes: Sequence[Mapping[str, Any]] | None = None,
    edges: Sequence[Mapping[str, Any]] | None = None,
) -> str:
    """The server-rendered indented fallback: works with JavaScript disabled.

    ``nodes`` and ``edges`` default to the full document. ``_graph_page`` passes
    the hide-closed view so the outline matches the canvas while every node still
    carries the derived flags computed from the full graph.
    """
    node_list: list[Mapping[str, Any]] = list(
        nodes if nodes is not None else graph["nodes"]
    )
    edge_list: list[Mapping[str, Any]] = list(
        edges if edges is not None else graph["edges"]
    )
    depths = _topological_depths(node_list, edge_list)
    prereqs: dict[str, list[str]] = {}
    for edge in edge_list:
        prereqs.setdefault(str(edge["target"]), []).append(str(edge["source"]))

    def order(node: Mapping[str, Any]) -> tuple[int, int]:
        return (depths.get(str(node["id"]), 0), _sequence(node))

    lines: list[str] = []
    for node in sorted(node_list, key=order):
        node_id = str(node["id"])
        depth = min(depths.get(node_id, 0), MAX_INDENT_DEPTH)
        needs = ", ".join(prereqs.get(node_id, [])) or "none"
        status = str(node["status"])
        lines.append(
            _render(
                "dep_outline.html",
                depth=str(depth),
                id=_esc(node_id),
                title=_esc(node["title"]),
                status=_esc(status),
                glyph=_esc(_status_glyph(status)),
                status_label=_esc(_status_label(status)),
                needs=_esc(needs),
                reviewable_marker=(
                    '<span class="state-chip" data-state="reviewable">'
                    "\u25d0 reviewable</span>"
                    if node.get("reviewable")
                    else ""
                ),
                claimed=_esc(_claimed_suffix(node)),
                blocked_flag="true" if node.get("blocked") else "false",
                blocked_marker="\u2298 blocked" if node.get("blocked") else "",
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


def _badge_list(items: Sequence[Mapping[str, Any]], now: datetime) -> str:
    if not items:
        return '<p class="empty">None.</p>'
    rows = "".join(_card(task, now) for task in items)
    return f'<ul class="cards">{rows}</ul>'


def _blocker_list(task: Mapping[str, Any]) -> str:
    entries = list(task["blockers"])
    if not entries:
        return '<p class="empty">No derived blockers.</p>'
    rows = "".join(
        f'<li class="blocker" data-kind="{_esc(entry.get("code"))}">'
        f'<span class="blocker__code">{_esc(entry.get("code"))}</span> '
        f'<span class="blocker__text">{_esc(_blocker_text(entry))}</span></li>'
        for entry in entries
    )
    return f'<ul class="blockers">{rows}</ul>'


def _memo_panel(conn: sqlite3.Connection) -> str:
    """The board-level memo, escaped inside a collapsible ``<pre>``.

    A long memo is collapsed rather than truncated: the whole text is always in
    the document, and a reader opens it deliberately. The rev and author are
    shown so an operator can see which revision is on screen.
    """
    document = views.memo_document(conn)
    rev = int(document["rev"])
    if rev == 0:
        return _render(
            "memo.html",
            heading="Project memo",
            meta="no memo yet",
            open_attr=" open",
            empty=" empty",
            text="No memo yet. Write one with 'tack memo TEXT --reason ...'.",
        )
    meta = (
        f"rev {rev} &#183; {_esc(document['author'])} &#183; "
        f"{_esc(document['created_at'])} &#183; {document['bytes']} bytes"
    )
    open_attr = "" if int(document["bytes"]) > memo.COLLAPSE_BYTES else " open"
    return _render(
        "memo.html",
        heading="Project memo",
        meta=meta,
        open_attr=open_attr,
        empty="",
        text=_esc(document["text"]),
    )


def _legend() -> str:
    """The legend mirrors the drawn card: a status glyph plus its border/colour.

    Derived states stay as overlays: ``blocked`` thickens the border,
    ``eligible`` adds the corner mark and ``claimed`` adds the double ring and
    the holder badge. Labels carry the meaning; glyphs are decorative.
    """
    statuses = (
        ("backlog", "backlog"),
        ("ready", "ready"),
        ("in_progress", "in progress"),
        ("review", "review"),
        ("done", "done"),
        ("cancelled", "cancelled"),
    )
    items = "".join(
        f'<li class="legend-item legend-item--status" data-status="{_esc(status)}">'
        f'<span class="legend-glyph" aria-hidden="true">'
        f"{_esc(STATUS_GLYPHS[status])}</span>"
        f"<span>{_esc(label)}</span></li>"
        for status, label in statuses
    )
    overlays = (
        ("shape-crossed", "blocked \u2014 thick border"),
        ("shape-eligible", "eligible \u2014 corner mark"),
        ("shape-reviewable", "reviewable \u2014 double top edge"),
        ("shape-claimed", "claimed \u2014 double ring + holder badge"),
    )
    items += "".join(
        f'<li class="legend-item {css}"><span class="legend-shape"></span>'
        f"<span>{_esc(label)}</span></li>"
        for css, label in overlays
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

        request_etag = self.headers.get("If-None-Match")
        headers: dict[str, str] = {}
        if content_type == JSON_CONTENT_TYPE:
            # Strong validator over the canonical JSON body (design 6.1).
            response_etag = _etag(body)
            headers["ETag"] = response_etag
            headers["Cache-Control"] = "no-cache"
            if _if_none_match_matches(request_etag, response_etag):
                self._send(
                    HTTPStatus.NOT_MODIFIED,
                    content_type,
                    b"",
                    include_body=False,
                    extra_headers=headers,
                )
                return
        else:
            # HTML and static assets are never cached by the browser.
            headers["Cache-Control"] = "no-store"

        if status == HTTPStatus.METHOD_NOT_ALLOWED:
            self._send(
                status,
                content_type,
                body,
                include_body=include_body,
                extra_headers={**headers, "Allow": "GET, HEAD"},
            )
            return
        self._send(
            status, content_type, body, include_body=include_body, extra_headers=headers
        )

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
        if path == "/api/memo":
            return self._api(lambda conn: views.memo_document(conn))
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
        except sqlite3.Error as exc:  # pragma: no cover - defensive
            status = HTTPStatus.INTERNAL_SERVER_ERROR
            page = _render(
                "error.html",
                title=_esc(f"{status} {HTTPStatus(status).phrase}"),
                heading=_esc(f"{status} {HTTPStatus(status).phrase}"),
                message=_esc(str(exc)),
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
        now = datetime.now(UTC)
        content = _render(
            "board.html",
            memo=_memo_panel(conn),
            summary=_summary_bar(document, query),
            filters=_filter_form(query),
            counts_line=_esc(_counts_line(document["counts"])),
            columns=_board_columns(
                document["tasks"],
                status_selected=filters["status"] is not None,
                query=query,
                now=now,
            ),
        )
        return self._shell(
            title="Board",
            project=document["project"],
            current="board",
            content=content,
            scripts='<script src="/static/board.js" defer></script>',
        )

    def _graph_page(
        self, conn: sqlite3.Connection, query: Mapping[str, list[str]]
    ) -> str:
        task_id = _first(query, "task") or None
        document = views.graph_document(
            conn, task_id, include_claim_staleness=True
        )
        project = schema.database_label(conn)
        stale_claims = " ".join(
            _esc(str(node["id"]))
            for node in document["nodes"]
            if node.get("claim_stale")
        )
        # ``hide_closed`` is the URL form of the toolbar toggle. The JSON
        # endpoints keep returning the full graph; only the no-JS outline and
        # the server-rendered counts are filtered here.
        hide_closed = _first(query, "hide_closed") == "1"
        total_nodes = len(document["nodes"])
        total_edges = len(document["edges"])
        outline_nodes: list[Mapping[str, Any]] = list(document["nodes"])
        outline_edges: list[Mapping[str, Any]] = list(document["edges"])
        hidden_count = 0
        if hide_closed:
            closed = {
                str(node["id"])
                for node in outline_nodes
                if str(node["status"]) in TERMINAL_STATUSES
            }
            hidden_count = len(closed)
            outline_nodes = [
                node for node in outline_nodes if str(node["id"]) not in closed
            ]
            outline_edges = [
                edge
                for edge in outline_edges
                if str(edge["source"]) not in closed
                and str(edge["target"]) not in closed
            ]
        content = _render(
            "graph.html",
            task=_esc(task_id or ""),
            node_count=str(total_nodes),
            edge_count=str(total_edges),
            outline_counts=_esc(
                _outline_counts(
                    len(outline_nodes),
                    total_nodes,
                    len(outline_edges),
                    total_edges,
                    hidden_count,
                )
            ),
            hidden_count=str(hidden_count),
            hide_closed_pressed="true" if hide_closed else "false",
            hide_closed_value="1" if hide_closed else "0",
            legend=_legend(),
            stale_claims=stale_claims,
            fallback=_dependency_list(
                document, nodes=outline_nodes, edges=outline_edges
            ),
        )
        return self._shell(
            title="Dependency graph",
            project=project,
            current="graph",
            content=content,
            scripts='<script src="/static/graph.js" defer></script>',
        )

    def _task_page(self, conn: sqlite3.Connection, task_id: str) -> str:
        document = views.task_document(conn, task_id)
        task = document["task"]
        now = datetime.now(UTC)
        claim = document["claim"]
        status = str(task["status"])
        content = _render(
            "task.html",
            id=_esc(task["id"]),
            title=_esc(task["title"]),
            description=_esc(task["description"]) or "&#8212;",
            status=_esc(status),
            glyph=_esc(_status_glyph(status)),
            status_label=_esc(_status_label(status)),
            priority=_esc(task["priority"]),
            priority_pips=_esc(PRIORITY_PIPS.get(str(task["priority"]), "")),
            assignee=_esc(task["assignee"] or "-"),
            labels_html=_fact_labels_html(task),
            claim_html=_claim_fact_html(
                claim, now, stale=bool(task.get("claim_stale"))
            ),
            readiness_chips=_readiness_chips_html(task, claim),
            readiness_sentence=_readiness_sentence(task, claim),
            blocked_summary=_blocked_summary_html(task),
            blocked_reason=_esc(task["blocked_reason"] or "-"),
            blockers=_blocker_list(task),
            ready=_esc(str(task["dependency_ready"])),
            created_html=_time_html(task["created_at"]),
            updated_html=_time_html(task["updated_at"]),
            completed_html=_time_html(task["completed_at"]),
            version=_esc(str(task["version"])),
            prerequisites=_dependency_links(
                document["prerequisites"], now, prerequisite=True
            ),
            dependents=_dependency_links(
                document["dependents"], now, prerequisite=False
            ),
            timeline=_timeline(document["notes"], document["history"]),
        )
        return self._shell(
            title=f"{task['id']} {task['title']}",
            project=schema.database_label(conn),
            current="board",
            content=content,
            scripts='<script src="/static/markdown.js" defer></script>',
        )

    def _shell(
        self, *, title: str, project: str, current: str, content: str, scripts: str
    ) -> str:
        nav = "".join(
            f'<a href="{href}"'
            + (' aria-current="page"' if name == current else "")
            + f">{label}</a>"
            for name, label, href in (
                ("board", "Board", "/board"),
                ("graph", "Graph", "/graph"),
            )
        )
        return _render(
            "base.html",
            title=_esc(title),
            project=_esc(project),
            database=_esc(str(self._config.database_path)),
            nav=nav,
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
        if int(status) != HTTPStatus.NOT_MODIFIED:
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
