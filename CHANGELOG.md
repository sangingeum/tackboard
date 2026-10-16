# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Repository scaffold: package layout, packaging metadata, MIT license, CI.
- Domain core: configuration and project resolution, connection/transaction
  handling, schema with migrations, backup and diagnostics, task models, the
  service layer and its single transition function, dependency DAG handling,
  claims with the atomic `next --claim` selector, and append-only history.
- The `tack` command-line interface: every command except the browser server,
  global options accepted before or after the subcommand, versioned JSON
  documents on stdout, human renderings of the same data, and the documented
  exit-code contract.
- `history.list_notes`, so `show` can display a task's latest notes without
  SQL outside the service layer.
- Operator documentation in `README.md` (installation, project selection, CLI
  examples, status/dependency/claim semantics, the agent quickstart snippet,
  backup and troubleshooting) and contributor documentation in `DESIGN.md`.
- Test coverage for the CLI contract: filters, counts, JSON documents and the
  error envelope, actor resolution, the exit-code matrix, an end-to-end
  lifecycle, and the packaging (console script and `python -m`) paths.
- The read-only web view and the `tack serve` command: a Kanban board with
  filters, counts and a pausable auto-refresh; a dependency graph drawn by
  hand-written SVG with a server-rendered indented outline for browsers without
  JavaScript; and a task detail page. `GET` and `HEAD` only, Host-header
  validation, `Content-Security-Policy` and `nosniff` on every response, and
  `PRAGMA query_only` connections to the selected database only.
- `tackboard.views`, the interface-neutral read models (`board_document`,
  `graph_document`, `task_document`) that the CLI and the web layer both use, so
  `/api/board`, `/api/graph` and `/api/tasks/<id>` are the CLI's own documents.
- Test coverage for the web area: two-database isolation, JSON parity with the
  CLI, filters and counts, method rejection, Host validation, escaping, an empty
  database, a 1,000-task graph, the no-JavaScript fallback, and the single
  `serve --json` startup record.
- `SKILL.md`: the agent-facing usage guide — a flat command reference, the
  versioned JSON and exit-code contracts, the claim/work/complete loop, and the
  best practices for claiming, depending on, and completing work from a CLI.
- Field limits documented in `SKILL.md` and `README.md`: every write-time limit
  in one table (`title` 200 characters, `description` 64 KiB, a label 50
  characters and 20 labels per task, `blocked_reason` and note text 2 KiB,
  `--reason` 1024 characters, agent-id shape), so a caller can size a value
  before writing it instead of discovering the limit on exit 4.

### Fixed

- `depend remove` no longer advertises `--force`/`--reason`. Removing a
  dependency is not one of the enumerated `--force` cases (spec 6.2), so the
  flag is now rejected as a usage error (exit 2), exactly as `update` already
  rejects it. `--agent` and `--if-version` are unchanged.
- `init` on an existing database now refuses an older schema version with the
  same error `open_database` uses (`Run 'tack migrate'`, exit 6) instead of
  silently creating current tables, re-seeding metadata and stamping a new
  schema version. `init` is strictly idempotent-or-refuse again, and `migrate`
  remains the only command that changes a schema version (spec 5.4).
- A forced start into `in_progress` now covers a *cancelled* prerequisite as
  well as an unfinished one. `tack claim --force --reason` and
  `tack status … in_progress --force --reason` consult one shared force
  predicate, so the claim/status policy (spec 8.2 row 1, spec 6.2 case 2) is
  enforced identically from every entry point.
- Starting a task for which the actor already holds a claim — for example
  beginning work on a `review` task the actor claimed — no longer rewrites the
  claim's `claimed_at`. "If already held by actor, keep the claim" now keeps
  the claim as it stands (spec 8.2).
- Length-limit validation errors now carry machine-readable `details` —
  `field`, `max`, `unit` and `actual` — instead of an empty object, so an agent
  can recover programmatically rather than parsing the message prose. Filed as
  issue #2, where a 200-character title limit was discoverable only by hitting
  exit 4 mid-round; the same details cover `description`, `label`, `labels`,
  `blocked_reason`, `note` and `--reason`.

## [0.1.0] - 2026-10-16

First release. `tackboard` is a project-scoped task board for coordinating
several agents on one codebase: a stable `tack` CLI over a single SQLite
database per project, a versioned JSON contract, readiness derived from a
dependency DAG, single-holder claims with an atomic "claim the next ready
task" selector, and a read-only board and dependency-graph web view for the
operator. The Unreleased section above describes this first-release surface in
full; this entry records the release and the packaging work that makes the
distributions installable.

## [0.1.1] - 2026-10-16
Fixed issues#2


### Added

- Distributions on PyPI as `tackboard` (`uv tool install tackboard`,
  `uvx tackboard --help`, or `pip install tackboard`). The wheel and the sdist
  both carry the web templates and static assets and the MIT license; the sdist
  also carries the README and the project metadata.

### Fixed

- Declared Python support is consistent: the package requires Python 3.12 and
  the `Programming Language :: Python` classifier no longer advertises 3.11.
