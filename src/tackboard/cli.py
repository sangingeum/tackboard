"""Command-line interface: argument parsing, output rendering, exit codes.

Spec: section 10 (the CLI contract), section 10.5 (the JSON documents), and
section 10.6 (the exit codes). This module deliberately holds no domain logic:
status transitions, dependency and cycle rules, claim policy, version
arithmetic and SQL all live in the service layer (``service``, ``claims``,
``dependencies``, ``history``, which route every status change through
``service.apply_transition``). The CLI resolves options, calls the library, and
renders the result as text or as the versioned JSON documents of spec 10.5.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sqlite3
import sys
import traceback
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

from . import __version__, claims, config, dependencies, history, schema, service, views
from .errors import InternalError, NotFoundError, TackError, UsageError
from .models import PRIORITIES, STATUSES, TERMINAL_STATUSES

#: Version of the JSON documents written to stdout (spec 10.5). Every document
#: carries it; contracts only ever grow by adding fields within a version.
JSON_SCHEMA_VERSION = 1

#: Sort keys accepted by ``--sort`` (spec 10.2).
SORT_FIELDS: tuple[str, ...] = (
    "priority",
    "status",
    "created_at",
    "updated_at",
    "id",
    "title",
)

#: Column widths for the human task line. ``max(7,5,11,6,4,9)=11`` is the
#: longest stored status (``in_progress``) and ``max(3,6,4,6)=6`` the longest
#: priority (``normal``/``urgent``) — verified with ``calc``.
STATUS_WIDTH = 11
PRIORITY_WIDTH = 6
ASSIGNEE_WIDTH = 12

#: ``done`` and ``cancelled`` board groups show this many most recently updated
#: tasks plus a count of the rest (spec 10.2).
TERMINAL_GROUP_LIMIT = 10

#: ``show`` displays the latest notes (spec 9.1).
SHOW_NOTE_LIMIT = 5

#: The counts of spec 10.2, in output order: total, stored statuses, derived.
_COUNT_KEYS: tuple[str, ...] = (
    "tasks",
    *STATUSES,
    "eligible",
    "reviewable",
    "blocked",
    "claimed",
)

#: Human verbs for the mutation result line.
_MUTATION_VERBS: dict[str, str] = {
    "create": "Created",
    "update": "Updated",
    "status": "Status changed for",
    "complete": "Completed",
    "reopen": "Reopened",
    "cancel": "Cancelled",
    "restore": "Restored",
    "claim": "Claimed",
    "release": "Released",
    "depend.add": "Dependency added to",
    "depend.remove": "Dependency removed from",
}

GUIDE_TEXT = """\
tack — a project-scoped task board for coordinating agents

Setup
  tack init --db PATH                  create the board (creates parent dirs)
  export TACKBOARD_DB=PATH             or pass --db PATH on every command
  export TACKBOARD_AGENT=my-agent      every mutating command needs an actor
  tack guide                           print this summary

The work loop
  tack next --claim --json             take the top eligible task, atomically
  tack next --stage review --claim     take a task waiting for review
  tack show T-000001 --json            read one task in full
  tack note T-000001 "progress note"   append a handoff note
  tack complete T-000001 --json        finish it
  tack reopen T-000001 --json          put it back to ready

Reading the board
  tack board --json                    every task, for machines
  tack board --brief                   one line of counts
  tack board --count                   counts, one key=value per line
  tack board --eligible --limit 5      only work an agent may claim now
  tack list --assignee my-agent        filter by assignee
  tack list --label core --full        --full adds the description
  tack list --blocked                  only tasks with derived blockers

Dependencies
  tack create "Ship the parser" --depends-on T-000001
  tack depend add T-000002 --on T-000001
  tack deps T-000002                   direct prerequisites
  tack dependents T-000001             direct dependents
  tack graph --format dot              whole graph; --format json for machines

JSON
  --json works before or after the subcommand and prints exactly one JSON
  document on stdout, always carrying "schema_version": 1. --full adds
  descriptions to board, list and next. With --json, errors are printed to
  stdout as {"schema_version":1,"error":{...}} and stderr stays empty unless
  --verbose is given; without --json, errors go to stderr.

Exit codes
  0 success, including no-ops and an empty next
  1 internal error              4 validation, invalid transition, or cycle
  2 usage error, bad option     5 concurrency conflict: claim held by another
    combination, or no actor      agent, stale version, lock timeout
  3 task, project, or database  6 database, schema, or migration error
    not found / not configured

Status and readiness
  Statuses: backlog, ready, in_progress, review, done, cancelled.
  Readiness is derived, never stored: a task is eligible when its status is
  ready, every prerequisite is done, it has no explicit blocked reason, and
  nobody holds a claim. --force --reason TEXT is allowed only for the audited
  cases listed in the specification — exhaustive, not a general bypass.
"""


@dataclass(slots=True, frozen=True)
class _Context:
    """Resolved global options that affect rendering.

    ``--verbose`` is handled where errors are reported, because a failure
    during parsing happens before any context exists.
    """

    json: bool
    quiet: bool


# --------------------------------------------------------------------------- #
# streams and rendering primitives
# --------------------------------------------------------------------------- #


def _configure_stdio() -> None:
    """Force UTF-8 on the output streams; never emit color (spec 10.5)."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(ValueError, OSError):
                reconfigure(encoding="utf-8")


def _write_line(text: str) -> None:
    sys.stdout.write(text + "\n")


def _write_err(text: str) -> None:
    sys.stderr.write(text + "\n")


def _write_json(document: Mapping[str, Any]) -> None:
    _write_line(json.dumps(document, ensure_ascii=False))


def _task_line(task: Mapping[str, Any]) -> str:
    """One human line for a task: id, status, priority, assignee, title, flags."""
    flags: list[str] = []
    if task["eligible"]:
        flags.append("eligible")
    if task["reviewable"]:
        flags.append("reviewable")
    if task["blocked"]:
        flags.append("blocked")
    claimed_by = task["claimed_by"]
    if claimed_by is not None:
        flags.append(f"claimed:{claimed_by}")
    suffix = f"  [{' '.join(flags)}]" if flags else ""
    assignee = task["assignee"] or "-"
    return (
        f"{task['id']}  {str(task['status']):<{STATUS_WIDTH}}"
        f"  {str(task['priority']):<{PRIORITY_WIDTH}}"
        f"  {str(assignee):<{ASSIGNEE_WIDTH}}  {task['title']}{suffix}"
    )


def _mutation_document(result: Mapping[str, Any]) -> dict[str, Any]:
    """The spec 10.5 mutation envelope: schema_version, changed, task."""
    document: dict[str, Any] = {
        "schema_version": JSON_SCHEMA_VERSION,
        "changed": bool(result.get("changed", False)),
        "task": result.get("task"),
    }
    if "active_dependents" in result:
        document["active_dependents"] = result["active_dependents"]
    return document


def _render_mutation(verb: str, document: Mapping[str, Any]) -> list[str]:
    task = document["task"]
    if task is None:
        return [f"{verb}: no task involved."]
    if not document["changed"]:
        return [f"No change: {task['id']} is {task['status']}."]
    lines = [f"{verb} {_task_line(task)}"]
    for dependent in document.get("active_dependents") or []:
        lines.append(f"  active dependent {_task_line(dependent)}")
    return lines


def _counts_line(counts: Mapping[str, Any]) -> str:
    return " ".join(f"{key}={counts[key]}" for key in _COUNT_KEYS)


def _board_lines(
    tasks: Sequence[Mapping[str, Any]],
    *,
    status_selected: bool,
    limited: bool,
) -> list[str]:
    """Group by stored status with the spec 10.2 terminal-group truncation."""
    groups: dict[str, list[Mapping[str, Any]]] = {status: [] for status in STATUSES}
    for task in tasks:
        groups[str(task["status"])].append(task)

    lines: list[str] = []
    for status in STATUSES:
        group = groups[status]
        shown: Sequence[Mapping[str, Any]] = group
        hidden = 0
        if status in TERMINAL_STATUSES and not status_selected and not limited:
            recent = sorted(group, key=lambda item: str(item["updated_at"]), reverse=True)
            shown = recent[:TERMINAL_GROUP_LIMIT]
            hidden = len(recent) - len(shown)
        lines.append(f"{status} ({len(group)})")
        lines.extend(f"  {_task_line(task)}" for task in shown)
        if hidden:
            lines.append(f"  ... {hidden} more")
    return lines


def _render_show(task: Mapping[str, Any], notes: Sequence[Mapping[str, Any]]) -> list[str]:
    lines = [f"{task['id']}  {task['title']}"]
    lines.append(f"  status:        {task['status']}")
    lines.append(f"  priority:      {task['priority']}")
    lines.append(f"  assignee:      {task['assignee'] or '-'}")
    lines.append(f"  labels:        {', '.join(task['labels']) or '-'}")
    lines.append(f"  depends on:    {', '.join(task['depends_on']) or '-'}")
    lines.append(
        "  readiness:     "
        f"dependency_ready={task['dependency_ready']} "
        f"eligible={task['eligible']} "
        f"reviewable={task['reviewable']} "
        f"blocked={task['blocked']}"
    )
    claim = task["claimed_by"] or "-"
    if task["claimed_at"]:
        claim = f"{claim} (since {task['claimed_at']})"
    lines.append(f"  claim:         {claim}")
    lines.append(f"  blocked reason: {task['blocked_reason'] or '-'}")
    for blocker in task["blockers"]:
        entry = json.dumps(blocker, ensure_ascii=False, sort_keys=True)
        lines.append(f"  blocker:       {entry}")
    lines.append(f"  created:       {task['created_at']}")
    lines.append(f"  updated:       {task['updated_at']}")
    lines.append(f"  completed:     {task['completed_at'] or '-'}")
    lines.append(f"  version:       {task['version']}")
    if task["description"]:
        lines.append("  description:")
        lines.extend(f"    {line}" for line in str(task["description"]).splitlines())
    if notes:
        lines.append(f"  notes (latest {len(notes)}):")
        for note in notes:
            text = str(note["payload"].get("text", ""))
            lines.append(f"    {note['timestamp']}  {note['actor']}  {text}")
    return lines


# --------------------------------------------------------------------------- #
# project and actor resolution
# --------------------------------------------------------------------------- #


def _resolve_db_path(args: argparse.Namespace) -> Path:
    return config.resolve_database(
        getattr(args, "db", None),
        cwd=Path.cwd(),
        env=os.environ,
    )


@contextmanager
def _project(args: argparse.Namespace) -> Iterator[sqlite3.Connection]:
    """Open the selected project database, read-write, for one command."""
    conn = schema.open_database(_resolve_db_path(args))
    try:
        yield conn
    finally:
        conn.close()


def _actor(args: argparse.Namespace) -> str:
    return config.resolve_actor(getattr(args, "agent", None), env=os.environ)


def _description_from_args(args: argparse.Namespace) -> str | None:
    """Return the requested description, or ``None`` when none was supplied."""
    path = args.description_file
    if path is not None:
        if path == "-":
            return sys.stdin.read()
        try:
            return Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            raise NotFoundError(f"Cannot read {path}: {exc}") from exc
    text = args.description
    return None if text is None else str(text)


# --------------------------------------------------------------------------- #
# parser construction
# --------------------------------------------------------------------------- #


class _Parser(argparse.ArgumentParser):
    """An ArgumentParser that reports usage failures as ``UsageError``.

    ``argparse`` exits with text on stderr; spec 10.5 requires the JSON error
    envelope on stdout when ``--json`` was given, so parsing errors are raised
    and rendered by :func:`main` like any other error.
    """

    def error(self, message: str) -> NoReturn:
        raise UsageError(message)


def _shared_options() -> _Parser:
    """The global options, accepted before and after the subcommand.

    Every default is ``SUPPRESS`` so that a value set by the parser before the
    subcommand is never overwritten by the subparser's own default.
    """
    shared = _Parser(add_help=False)
    shared.add_argument(
        "--db",
        metavar="PATH",
        default=argparse.SUPPRESS,
        help="project database (overrides TACKBOARD_DB and .tackboard.toml)",
    )
    shared.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="print one JSON document on stdout",
    )
    shared.add_argument(
        "--quiet",
        action="store_true",
        default=argparse.SUPPRESS,
        help="suppress non-essential informational output",
    )
    shared.add_argument(
        "--verbose",
        action="store_true",
        default=argparse.SUPPRESS,
        help="diagnostics and tracebacks on stderr",
    )
    shared.add_argument(
        "--version",
        action="version",
        version=f"tack (tackboard) {__version__}",
        help="print the version and exit",
    )
    return shared


def _add_actor(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--agent",
        metavar="AGENT_ID",
        help="acting agent (overrides TACKBOARD_AGENT)",
    )


def _add_force(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--force",
        action="store_true",
        help="apply an audited administrative override (requires --reason)",
    )
    parser.add_argument("--reason", metavar="TEXT", help="reason recorded with a --force")


def _add_if_version(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--if-version",
        type=int,
        metavar="N",
        help="fail with exit 5 unless the task version is N",
    )


def _add_mutation_options(parser: argparse.ArgumentParser, *, if_version: bool = True) -> None:
    _add_actor(parser)
    _add_force(parser)
    if if_version:
        _add_if_version(parser)


def _add_write_options(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--description", metavar="TEXT", help="task description")
    group.add_argument(
        "--description-file",
        metavar="PATH",
        help="read the description from PATH ('-' reads stdin)",
    )
    parser.add_argument("--priority", choices=PRIORITIES, metavar="PRIORITY")
    parser.add_argument("--assignee", metavar="AGENT_ID", help="assignee ('' clears it)")


def _add_filters(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--status", choices=STATUSES, metavar="STATUS")
    parser.add_argument("--assignee", metavar="AGENT_ID")
    parser.add_argument("--label", metavar="LABEL")
    parser.add_argument("--priority", choices=PRIORITIES, metavar="PRIORITY")
    parser.add_argument("--blocked", action="store_true", help="only tasks with blockers")
    parser.add_argument(
        "--eligible",
        "--ready",
        dest="eligible",
        action="store_true",
        help="only tasks eligible for work (--ready is an alias)",
    )
    parser.add_argument("--limit", type=int, metavar="N")
    parser.add_argument("--sort", choices=SORT_FIELDS, metavar="FIELD")
    parser.add_argument("--reverse", action="store_true")
    parser.add_argument("--full", action="store_true", help="include descriptions")
    parser.add_argument("--brief", action="store_true", help="one line of counts, no tasks")
    parser.add_argument("--count", action="store_true", help="counts only, one per line")


def _build_parser() -> _Parser:
    shared = _shared_options()
    parser = _Parser(
        prog="tack",
        parents=[shared],
        description=(
            "Project-scoped task board for coordinating multiple agents. "
            "Run 'tack guide' for the agent-oriented usage summary."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    init = subparsers.add_parser(
        "init", parents=[shared], help="create and initialize a project database"
    )
    init.set_defaults(handler="init")

    migrate = subparsers.add_parser(
        "migrate", parents=[shared], help="back up and migrate an older database"
    )
    migrate.set_defaults(handler="migrate")

    backup = subparsers.add_parser(
        "backup", parents=[shared], help="write a consistent snapshot of the database"
    )
    backup.add_argument("path", metavar="PATH", help="destination file (must not exist)")
    backup.set_defaults(handler="backup")

    for name, grouped, summary in (
        ("board", True, "show the board, grouped by status"),
        ("list", False, "show a flat list of tasks"),
    ):
        listing = subparsers.add_parser(name, parents=[shared], help=summary)
        _add_filters(listing)
        listing.set_defaults(handler="board" if grouped else "list")

    show = subparsers.add_parser("show", parents=[shared], help="show one task in full")
    show.add_argument("task_id", metavar="TASK_ID")
    show.set_defaults(handler="show")

    create = subparsers.add_parser("create", parents=[shared], help="create a task")
    create.add_argument("title", metavar="TITLE")
    _add_write_options(create)
    create.add_argument("--label", action="append", metavar="LABEL", help="repeatable")
    create.add_argument(
        "--depends-on",
        action="append",
        metavar="TASK_ID",
        help="prerequisite, repeatable; committed atomically",
    )
    create.add_argument("--status", choices=STATUSES, metavar="STATUS")
    _add_actor(create)
    create.set_defaults(handler="create")

    update = subparsers.add_parser("update", parents=[shared], help="update task fields")
    update.add_argument("task_id", metavar="TASK_ID")
    _add_write_options(update)
    update.add_argument("--title", metavar="TITLE")
    update.add_argument("--blocked-reason", metavar="TEXT")
    update.add_argument(
        "--clear-blocked-reason", action="store_true", help="remove the explicit blocker"
    )
    labels = update.add_mutually_exclusive_group()
    labels.add_argument("--label", action="append", metavar="LABEL", help="replace all labels")
    labels.add_argument("--add-label", action="append", metavar="LABEL")
    labels.add_argument("--remove-label", action="append", metavar="LABEL")
    _add_actor(update)
    _add_if_version(update)
    update.set_defaults(handler="update")

    status = subparsers.add_parser("status", parents=[shared], help="set a task's status")
    status.add_argument("task_id", metavar="TASK_ID")
    status.add_argument("status", choices=STATUSES, metavar="STATUS")
    _add_mutation_options(status)
    status.set_defaults(handler="status")

    for name, verb in (
        ("complete", "complete"),
        ("reopen", "reopen"),
        ("cancel", "cancel"),
        ("restore", "restore"),
    ):
        shortcut = subparsers.add_parser(
            name, parents=[shared], help=f"shortcut for the {verb} transition"
        )
        shortcut.add_argument("task_id", metavar="TASK_ID")
        _add_mutation_options(shortcut)
        shortcut.set_defaults(handler=name)

    note = subparsers.add_parser("note", parents=[shared], help="append a note")
    note.add_argument("task_id", metavar="TASK_ID")
    note.add_argument("text", metavar="TEXT")
    _add_actor(note)
    note.set_defaults(handler="note")

    for name in ("claim", "release"):
        claim_parser = subparsers.add_parser(
            name, parents=[shared], help=f"{name} a task"
        )
        claim_parser.add_argument("task_id", metavar="TASK_ID")
        _add_mutation_options(claim_parser)
        claim_parser.set_defaults(handler=name)

    claims_parser = subparsers.add_parser(
        "claims", parents=[shared], help="list active claims"
    )
    claims_parser.add_argument("--agent", metavar="AGENT_ID", help="filter by agent")
    claims_parser.add_argument(
        "--older-than", metavar="DURATION", help="only claims older than this, e.g. 2h"
    )
    claims_parser.set_defaults(handler="claims")

    next_parser = subparsers.add_parser(
        "next", parents=[shared], help="select the next task for the agent"
    )
    _add_actor(next_parser)
    next_parser.add_argument(
        "--claim", action="store_true", help="select and claim in one transaction"
    )
    next_parser.add_argument("--stage", choices=claims.STAGES, metavar="STAGE")
    next_parser.add_argument("--label", metavar="LABEL")
    next_parser.add_argument("--min-priority", choices=PRIORITIES, metavar="PRIORITY")
    next_parser.add_argument("--all-assignees", action="store_true")
    next_parser.add_argument("--full", action="store_true")
    _add_force(next_parser)
    next_parser.set_defaults(handler="next")

    depend = subparsers.add_parser(
        "depend", parents=[shared], help="add or remove a prerequisite edge"
    )
    depend_subs = depend.add_subparsers(dest="depend_command", metavar="{add,remove}")

    depend_add = depend_subs.add_parser("add", parents=[shared], help="add a prerequisite")
    depend_add.add_argument("task_id", metavar="TASK_ID")
    depend_add.add_argument("--on", dest="prerequisite_id", required=True, metavar="TASK_ID")
    _add_mutation_options(depend_add)
    depend_add.set_defaults(handler="depend.add")

    depend_remove = depend_subs.add_parser(
        "remove", parents=[shared], help="remove a prerequisite"
    )
    depend_remove.add_argument("task_id", metavar="TASK_ID")
    depend_remove.add_argument("--on", dest="prerequisite_id", required=True, metavar="TASK_ID")
    _add_mutation_options(depend_remove)
    depend_remove.set_defaults(handler="depend.remove")

    for name, summary in (
        ("deps", "list a task's direct prerequisites"),
        ("dependents", "list the tasks that depend on a task"),
    ):
        related = subparsers.add_parser(name, parents=[shared], help=summary)
        related.add_argument("task_id", metavar="TASK_ID")
        related.set_defaults(handler=name)

    graph = subparsers.add_parser("graph", parents=[shared], help="export the dependency graph")
    graph.add_argument("task_id", nargs="?", metavar="TASK_ID")
    graph.add_argument("--format", choices=("json", "dot"), default="json", metavar="FORMAT")
    graph.set_defaults(handler="graph")

    history_parser = subparsers.add_parser(
        "history", parents=[shared], help="show the activity history"
    )
    history_parser.add_argument("task_id", nargs="?", metavar="TASK_ID")
    history_parser.add_argument("--limit", type=int, metavar="N")
    history_parser.set_defaults(handler="history")

    doctor = subparsers.add_parser(
        "doctor", parents=[shared], help="check the database for problems"
    )
    doctor.set_defaults(handler="doctor")

    guide = subparsers.add_parser(
        "guide", parents=[shared], help="print the agent-oriented usage summary"
    )
    guide.set_defaults(handler="guide")

    return parser


# --------------------------------------------------------------------------- #
# command handlers
# --------------------------------------------------------------------------- #


def _run_maintenance(ctx: _Context, args: argparse.Namespace, handler: str) -> int:
    path = _resolve_db_path(args)
    if handler == "init":
        result = schema.init_database(path)
    elif handler == "migrate":
        result = schema.migrate_database(path)
    else:
        result = schema.backup_database(path, Path(args.path).expanduser())

    if ctx.json:
        _write_json(result)
        return 0

    label = result.get("project_label")
    if handler == "init":
        verb = "Initialized" if result.get("created") else "Already initialized"
        suffix = f", project {label!r}" if label else ""
        _write_line(
            f"{verb} {result['database']} "
            f"(schema v{result['database_schema_version']}{suffix})."
        )
    elif handler == "migrate":
        if result.get("migrated"):
            _write_line(
                f"Migrated {result['database']} from schema v{result['from_version']} "
                f"to v{result['to_version']}; backup at {result.get('backup')}."
            )
        else:
            _write_line(
                f"No migration needed: {result['database']} is at "
                f"schema v{result['database_schema_version']}."
            )
    else:
        _write_line(
            f"Backed up {result['database']} to {result['backup']} "
            f"(schema v{result['database_schema_version']})."
        )
    return 0


def _run_listing(ctx: _Context, args: argparse.Namespace, *, grouped: bool) -> int:
    if args.brief and args.count:
        raise UsageError("--brief and --count cannot be combined.")
    if args.limit is not None and args.limit < 0:
        raise UsageError("--limit must not be negative.")

    with _project(args) as conn:
        document = views.board_document(
            conn,
            status=args.status,
            assignee=args.assignee,
            label=args.label,
            priority=args.priority,
            blocked=True if args.blocked else None,
            eligible=True if args.eligible else None,
            limit=args.limit,
            sort=args.sort,
            reverse=args.reverse,
            full=args.full,
        )
    tasks = document["tasks"]
    counts = document["counts"]
    project = document["project"]

    if args.brief or args.count:
        if ctx.json:
            _write_json(
                {
                    "schema_version": JSON_SCHEMA_VERSION,
                    "project": project,
                    "counts": counts,
                }
            )
        elif args.brief:
            _write_line(_counts_line(counts))
        else:
            for key in _COUNT_KEYS:
                _write_line(f"{key}={counts[key]}")
        return 0

    if ctx.json:
        _write_json(
            {
                "schema_version": JSON_SCHEMA_VERSION,
                "project": project,
                "counts": counts,
                "tasks": tasks,
            }
        )
        return 0

    if not ctx.quiet:
        _write_line(f"# {project}")
    if grouped:
        for line in _board_lines(
            tasks,
            status_selected=args.status is not None,
            limited=args.limit is not None,
        ):
            _write_line(line)
    else:
        for task in tasks:
            _write_line(_task_line(task))
    return 0


def _run_show(ctx: _Context, args: argparse.Namespace) -> int:
    with _project(args) as conn:
        task = service.get_task(conn, args.task_id)
        notes = history.list_notes(conn, args.task_id, limit=SHOW_NOTE_LIMIT)

    if ctx.json:
        _write_json(
            {"schema_version": JSON_SCHEMA_VERSION, "task": task, "notes": notes}
        )
        return 0
    for line in _render_show(task, notes):
        _write_line(line)
    return 0


def _emit_mutation(ctx: _Context, verb: str, result: Mapping[str, Any]) -> int:
    document = _mutation_document(result)
    if ctx.json:
        _write_json(document)
        return 0
    for line in _render_mutation(verb, document):
        _write_line(line)
    return 0


def _run_create(ctx: _Context, args: argparse.Namespace) -> int:
    actor = _actor(args)
    description = _description_from_args(args)
    with _project(args) as conn:
        result = service.create_task(
            conn,
            actor=actor,
            title=args.title,
            description="" if description is None else description,
            priority=args.priority or "normal",
            assignee=args.assignee,
            labels=tuple(args.label or ()),
            status=args.status or "backlog",
            depends_on=tuple(args.depends_on or ()),
        )
    return _emit_mutation(ctx, _MUTATION_VERBS["create"], result)


def _run_update(ctx: _Context, args: argparse.Namespace) -> int:
    actor = _actor(args)
    if args.blocked_reason is not None and args.clear_blocked_reason:
        raise UsageError("--blocked-reason and --clear-blocked-reason cannot be combined.")

    changes: dict[str, Any] = {}
    description = _description_from_args(args)
    if description is not None:
        changes["description"] = description
    if args.title is not None:
        changes["title"] = args.title
    if args.priority is not None:
        changes["priority"] = args.priority
    if args.assignee is not None:
        changes["assignee"] = args.assignee
    if args.blocked_reason is not None:
        changes["blocked_reason"] = args.blocked_reason
    elif args.clear_blocked_reason:
        changes["blocked_reason"] = None
    if args.label is not None:
        changes["replace_labels"] = tuple(args.label)
    if args.add_label:
        changes["add_labels"] = tuple(args.add_label)
    if args.remove_label:
        changes["remove_labels"] = tuple(args.remove_label)

    with _project(args) as conn:
        result = service.update_task(
            conn,
            args.task_id,
            actor=actor,
            if_version=args.if_version,
            **changes,
        )
    return _emit_mutation(ctx, _MUTATION_VERBS["update"], result)


def _run_status(ctx: _Context, args: argparse.Namespace) -> int:
    actor = _actor(args)
    with _project(args) as conn:
        result = service.set_status(
            conn,
            args.task_id,
            args.status,
            actor=actor,
            if_version=args.if_version,
            force=args.force,
            reason=args.reason,
        )
    return _emit_mutation(ctx, _MUTATION_VERBS["status"], result)


def _run_shortcut(ctx: _Context, args: argparse.Namespace, handler: str) -> int:
    actor = _actor(args)
    shortcuts = {
        "complete": service.complete_task,
        "reopen": service.reopen_task,
        "cancel": service.cancel_task,
        "restore": service.restore_task,
    }
    with _project(args) as conn:
        result = shortcuts[handler](
            conn,
            args.task_id,
            actor=actor,
            if_version=args.if_version,
            force=args.force,
            reason=args.reason,
        )
    return _emit_mutation(ctx, _MUTATION_VERBS[handler], result)


def _run_note(ctx: _Context, args: argparse.Namespace) -> int:
    actor = _actor(args)
    with _project(args) as conn:
        result = service.add_note(conn, args.task_id, args.text, actor=actor)
    document = {
        "schema_version": JSON_SCHEMA_VERSION,
        "changed": bool(result["changed"]),
        "event": result["event"],
    }
    if ctx.json:
        _write_json(document)
        return 0
    _write_line(f"Note added to {result['event']['task_id']}.")
    return 0


def _run_claim_command(ctx: _Context, args: argparse.Namespace, handler: str) -> int:
    actor = _actor(args)
    function = claims.claim if handler == "claim" else claims.release
    with _project(args) as conn:
        result = function(
            conn,
            args.task_id,
            actor=actor,
            force=args.force,
            reason=args.reason,
            if_version=args.if_version,
        )
    return _emit_mutation(ctx, _MUTATION_VERBS[handler], result)


def _run_claims(ctx: _Context, args: argparse.Namespace) -> int:
    with _project(args) as conn:
        result = claims.list_claims(
            conn,
            agent=args.agent,
            older_than=getattr(args, "older_than", None),
        )
    if ctx.json:
        _write_json(result)
        return 0
    if not ctx.quiet:
        _write_line("# active claims")
    if not result["items"]:
        _write_line("(none)")
    for item in result["items"]:
        _write_line(
            f"{item['task_id']}  {item['agent_id']}  claimed {item['claimed_at']} "
            f"({item['age_seconds']}s ago)"
        )
    return 0


def _run_next(ctx: _Context, args: argparse.Namespace) -> int:
    actor = _actor(args)
    with _project(args) as conn:
        result = claims.next_task(
            conn,
            actor=actor,
            stage=args.stage or "work",
            claim=args.claim,
            force=args.force,
            reason=args.reason,
            label=args.label,
            min_priority=args.min_priority,
            all_assignees=args.all_assignees,
            full=args.full,
        )
        task = result["task"]
        if args.claim and task is not None:
            task = service.project_task(task, full=args.full)
            result = {**result, "task": task}

    if ctx.json:
        if args.claim:
            _write_json({"schema_version": JSON_SCHEMA_VERSION, "task": result["task"]})
        else:
            _write_json(
                {"schema_version": JSON_SCHEMA_VERSION, "tasks": result["tasks"]}
            )
        return 0

    if not ctx.quiet:
        _write_line("# next task")
    if args.claim:
        if result["task"] is None:
            _write_line("No eligible tasks.")
        else:
            _write_line(_task_line(result["task"]))
        return 0
    if not result["tasks"]:
        _write_line("No eligible tasks.")
        return 0
    for candidate in result["tasks"]:
        _write_line(_task_line(candidate))
    return 0


def _run_depend(ctx: _Context, args: argparse.Namespace, handler: str) -> int:
    actor = _actor(args)
    with _project(args) as conn:
        if handler == "depend.add":
            result = dependencies.add_dependency(
                conn,
                args.task_id,
                args.prerequisite_id,
                actor=actor,
                if_version=args.if_version,
                force=args.force,
                reason=args.reason,
            )
        else:
            result = dependencies.remove_dependency(
                conn,
                args.task_id,
                args.prerequisite_id,
                actor=actor,
                if_version=args.if_version,
            )
    return _emit_mutation(ctx, _MUTATION_VERBS[handler], result)


def _run_related(ctx: _Context, args: argparse.Namespace, handler: str) -> int:
    with _project(args) as conn:
        if handler == "deps":
            result = dependencies.list_dependencies(conn, args.task_id)
        else:
            result = dependencies.list_dependents(conn, args.task_id)
    if ctx.json:
        _write_json(result)
        return 0
    title = "prerequisites of" if handler == "deps" else "dependents of"
    if not ctx.quiet:
        _write_line(f"# {title} {result['task_id']}")
    if not result["tasks"]:
        _write_line("(none)")
    for task in result["tasks"]:
        _write_line(_task_line(task))
    return 0


def _run_graph(ctx: _Context, args: argparse.Namespace) -> int:
    if args.format == "dot" and ctx.json:
        raise UsageError("--json cannot be combined with 'graph --format dot'.")
    with _project(args) as conn:
        result = dependencies.export_graph(conn, args.task_id, fmt=args.format)
    if args.format == "dot":
        _write_line(str(result))
        return 0
    if ctx.json:
        _write_json(result)
        return 0
    _write_line(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _run_history(ctx: _Context, args: argparse.Namespace) -> int:
    with _project(args) as conn:
        if args.task_id is not None:
            service.get_task(conn, args.task_id)
        result = history.list_events(conn, args.task_id, limit=args.limit)
    if ctx.json:
        _write_json(result)
        return 0
    if not ctx.quiet:
        _write_line(f"# history {result['task_id'] or '(all tasks)'}")
    if not result["items"]:
        _write_line("(no events)")
    for item in result["items"]:
        payload = json.dumps(item["payload"], ensure_ascii=False, sort_keys=True)
        _write_line(
            f"{item['timestamp']}  {item['actor']}  {item['event_type']}  "
            f"{item['task_id'] or '-'}  {payload}"
        )
    return 0


def _run_doctor(ctx: _Context, args: argparse.Namespace) -> int:
    result = schema.doctor(_resolve_db_path(args))
    if ctx.json:
        _write_json(result)
    else:
        for finding in result["errors"]:
            _write_line(f"error:   [{finding['code']}] {finding['message']}")
        for finding in result["warnings"]:
            _write_line(f"warning: [{finding['code']}] {finding['message']}")
        if not result["errors"] and not result["warnings"]:
            _write_line("No problems found.")
    return 6 if result["errors"] else 0


def _run_guide(ctx: _Context, args: argparse.Namespace) -> int:
    del ctx, args
    sys.stdout.write(GUIDE_TEXT)
    return 0


# --------------------------------------------------------------------------- #
# dispatch
# --------------------------------------------------------------------------- #


def _dispatch(ctx: _Context, args: argparse.Namespace) -> int:
    handler = getattr(args, "handler", None)
    if not isinstance(handler, str):
        if args.command == "depend":
            raise UsageError(
                "Missing subcommand: use 'tack depend add' or 'tack depend remove'."
            )
        raise UsageError("Missing command. Run 'tack --help' for usage.")

    if handler in {"init", "migrate", "backup"}:
        return _run_maintenance(ctx, args, handler)
    if handler == "board":
        return _run_listing(ctx, args, grouped=True)
    if handler == "list":
        return _run_listing(ctx, args, grouped=False)
    if handler == "show":
        return _run_show(ctx, args)
    if handler == "create":
        return _run_create(ctx, args)
    if handler == "update":
        return _run_update(ctx, args)
    if handler == "status":
        return _run_status(ctx, args)
    if handler in {"complete", "reopen", "cancel", "restore"}:
        return _run_shortcut(ctx, args, handler)
    if handler == "note":
        return _run_note(ctx, args)
    if handler in {"claim", "release"}:
        return _run_claim_command(ctx, args, handler)
    if handler == "claims":
        return _run_claims(ctx, args)
    if handler == "next":
        return _run_next(ctx, args)
    if handler in {"depend.add", "depend.remove"}:
        return _run_depend(ctx, args, handler)
    if handler in {"deps", "dependents"}:
        return _run_related(ctx, args, handler)
    if handler == "graph":
        return _run_graph(ctx, args)
    if handler == "history":
        return _run_history(ctx, args)
    if handler == "doctor":
        return _run_doctor(ctx, args)
    if handler == "guide":
        return _run_guide(ctx, args)
    raise InternalError(f"Unhandled command {handler!r}.")


def _report_error(error: TackError, *, json_mode: bool, verbose: bool) -> int:
    """Render one error and return its exit code (spec 10.1, 10.5, 10.6)."""
    if json_mode:
        document: dict[str, Any] = {"schema_version": JSON_SCHEMA_VERSION}
        document.update(error.to_json())
        _write_json(document)
    else:
        _write_err(f"error: {error.message}")
    if verbose:
        traceback.print_exc(file=sys.stderr)
    return error.exit_code


def main(argv: Sequence[str] | None = None) -> int:
    """Run the ``tack`` CLI and return its exit code."""
    raw = list(sys.argv[1:] if argv is None else argv)
    json_mode = "--json" in raw
    verbose = "--verbose" in raw
    _configure_stdio()

    try:
        args = _build_parser().parse_args(raw)
        json_mode = bool(getattr(args, "json", False))
        verbose = bool(getattr(args, "verbose", False))
        ctx = _Context(
            json=json_mode,
            quiet=bool(getattr(args, "quiet", False)),
        )
        return _dispatch(ctx, args)
    except TackError as exc:
        return _report_error(exc, json_mode=json_mode, verbose=verbose)
    except SystemExit as exc:
        code = exc.code
        if isinstance(code, int):
            return code
        return 0 if code is None else 1
    except Exception as exc:  # noqa: BLE001 - the CLI must never leak a traceback by default
        return _report_error(
            InternalError(f"{type(exc).__name__}: {exc}"),
            json_mode=json_mode,
            verbose=verbose,
        )


__all__ = ["GUIDE_TEXT", "JSON_SCHEMA_VERSION", "SORT_FIELDS", "main"]
