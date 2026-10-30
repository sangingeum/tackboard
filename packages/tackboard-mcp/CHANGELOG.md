# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
