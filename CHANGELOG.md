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
