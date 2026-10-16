# AGENTS.md — tackboard

Static orientation brief for agents working in this repository. Read this
first, before any other exploration step. Keep it current: a structural change
(new top-level directory, changed build process, new architectural pattern)
updates this file in the same change. Static information only — no run results,
no bug lists, nothing that goes stale quickly.

## What this is

`tackboard` is a project-scoped task board for coordinating multiple AI agents.
One SQLite database per project; a stable CLI (`tack`) is the primary interface
for agents; a read-only web view serves the human operator. It is a
coordination board, not a project-management suite.

Governing specification: the tool's behavior is defined by the implementation
specification the project was built against (sections 1-19: goals, task model,
dependency DAG, claims, CLI contract, web UI, schema, tests, acceptance
criteria). `DESIGN.md` restates the architecture for contributors, and
`README.md` is the operator guide (its "Agent quickstart" section is the same
text `tack guide` prints).

## Layout

```text
src/tackboard/
  __init__.py      package metadata
  __main__.py      `python -m tackboard`
  cli.py           argument parsing, output formatting, exit codes
  config.py        project/database resolution (--db > env > .tackboard.toml)
  db.py            connection handling, transactions, busy/retry policy
  schema.py        DDL, application_id / user_version, migrations, backup
  models.py        dataclasses and enums (task, status, priority, events)
  service.py       domain layer: CRUD, transitions, validation, history
  dependencies.py  dependency edges, cycle detection, readiness, graph export
  claims.py        claim/release, atomic `next --claim`
  history.py       append-only event emission and queries
  views.py         read models shared by the CLI and the web layer
  web.py           read-only HTTP server (board, DAG, task detail, /api/*)
  templates/       server-rendered HTML
  static/          CSS and JavaScript (no inline scripts, no external hosts)
tests/             pytest suite, mirrors the module layout
```

`views.py` holds the interface-neutral read models (`board_document`,
`graph_document`, `task_document`): one read transaction, plain dictionaries,
no HTTP. The CLI and the web layer both consume them, so the pages, the JSON
API and the CLI describe one snapshot with the same shapes. `web.py` adds the
HTTP concerns only — routing, Host validation, headers, escaping.

## Commands

```bash
uv sync                                  # create .venv and install (dev group included)
uv run pytest -q                         # full test suite
uv run pytest tests/test_claims.py -q    # one module
uvx ruff check src tests                 # lint (must be clean)
uv run mypy src                          # type check (must be clean)
uv run pytest --cov -q                   # suite with coverage
uv run tack --help                       # the CLI, from the `tack` entry point
uv run python -m tackboard --help        # same CLI, module form
uv run tack guide                        # the agent-facing usage summary
uv run tack board --db PATH --json       # every command takes --db and --json
uv run tack serve --db PATH --port 0     # the read-only web view
uv build                                 # sdist + wheel
```

Never install with global `pip` (PEP 668 blocks it); this project uses `uv`.

## Architecture principles

1. **One domain layer.** CLI and web handlers call the same service functions.
   No validation or state-transition logic is duplicated in either interface.
   Every status change — `status`, `complete`, `claim`, `release`,
   `next --claim` — goes through one transition function.
2. **One authoritative database per project.** A project is a SQLite file;
   projects never share an ID namespace, and dependencies never cross files.
3. **Explicit status, derived readiness.** Status is stored; dependency
   readiness, `eligible`, `reviewable`, and `blocked` are computed from the
   graph, claims, and explicit blockers — never trusted from a caller.
4. **Transactional mutations.** Every task mutation and its history event(s)
   commit in one `BEGIN IMMEDIATE` transaction. Connections use
   `isolation_level=None` so `BEGIN IMMEDIATE` is actually issued.
5. **CLI output is an API contract.** JSON output is stable, versioned
   (`schema_version`), and free of prose on stdout. Exit codes are stable and
   documented. Agents must never need to scrape human-formatted tables.
6. **No silent data loss.** Invalid transitions, missing IDs, and concurrency
   conflicts produce clear errors and non-zero exit codes; multi-query reads
   take a consistent snapshot.
7. **Untrusted text.** Titles, descriptions, labels, notes, and agent IDs are
   plain text: never executed, never interpreted as HTML, escaped on output.
8. **Small and understandable** beats broad: a narrow, reliable surface over a
   feature-rich one. Deferred features are listed in the specification's
   appendix and must not be half-implemented.

## Conventions

- Python 3.12, type hints throughout, standard-library `sqlite3`.
- Minimal dependencies; anything added must be justified in the change.
- Parameterized SQL only.
- Timestamps: UTC, fixed-width ISO-8601 with a `Z` suffix.
- Enum columns carry `CHECK` constraints; priority sorts by rank, never by
  the text column.
- No color codes in CLI output; UTF-8 everywhere.
- Commit messages: `type: summary` (`feat`, `fix`, `test`, `docs`, `chore`).
