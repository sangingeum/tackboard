# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.10.0] - 2026-11-04

### Added

- `board_info` (read-only): reports the board this process serves — `attached`,
  `configured`, `path`, `exists`, `source` (`env`/`discovery`/`attach`/`none`),
  `project` and `warnings`. A missing or unconfigured board is a warning; a
  corrupt or foreign board raises the CLI's document.
- `board_create` (administrative): creates a board at an absolute path through
  `tack init --db PATH --json` and returns that document unchanged. It does not
  attach the new board.
- `board_attach` (administrative, process-global): validates an existing board
  with `tack doctor`, then switches every later call in the process to it. A
  failed validation leaves the old board active.
- `--allow-board-admin` and `TACKBOARD_MCP_ALLOW_ADMIN` gate the two
  administrative tools; both are off by default and read once at start-up.

### Changed

- The adapter's version tracks the core's: `tackboard-mcp` 0.10.0 depends on
  `tackboard>=0.10.0,<0.11`.
- A `not_found` CLI error now reaches the client with the CLI's actionable
  message, the full document and a one-line hint naming `board_create` and
  `board_attach`, instead of a generic tool failure.
- `board_info` and `board_attach` are the documented exceptions to the
  no-domain-logic rule: both return adapter-owned documents.

## [0.9.0] - 2026-10-30

### Added

- `memo_read` (with an optional revision), `list` (the CLI filters, descriptions
  only with `full`), `claim` (with the audited `force` + `reason` path),
  `status` and `reject` (mandatory reason; the claim returns to the author).
  An MCP-only agent can now read the memo, list and claim work, submit it for
  review, and complete or reject it without falling back to the CLI.
- `reopen`, `depend_add`, `memo_append` and `claims` remain deliberately out of
  scope for this adapter; the CLI covers them.

## [0.8.0] - 2026-10-30

### Added

- First public release. `tackboard-mcp` is distributed separately from the core
  and depends on `tackboard>=0.8.0,<0.9`, which supplies the `tack` CLI. The
  adapter's version tracks the core's, so a matching pair shares one number.
- Seven MCP tools — `next_claim`, `show`, `note`, `complete`, `release`,
  `create` and `board_brief` — that invoke the core CLI with `--json` and return
  the document unchanged, so a client never sees a second implementation of
  claims, transitions or validation.
- All four MCP annotation hints declared explicitly on every tool.

### Changed

- The default command is `python -m tackboard` from the environment the adapter
  is installed into, falling back to the `tack` console script when the core is
  not importable. `TACK_CMD` still overrides both.
